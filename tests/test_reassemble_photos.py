# 独立した転送列、故障注入、実QR画像を使う移植可能な受信側試験。
"""Portable receiver tests: independent wire fixtures, faults and real QR images."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

import cv2
import numpy as np
import qrcode
from qrcode.util import MODE_8BIT_BYTE, QRData

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from windows import reassemble_photos as receiver
from windows import decode_binary_photo as decoder
import frame_transport_model as model


def fixture(size):
    return bytes((i * 37 + (i >> 8) * 11 + (i >> 16) * 17 + 3) & 255
                 for i in range(size))


def wire(index, count, payload):
    return b"P98Q\x01" + index.to_bytes(2, "big") + count.to_bytes(2, "big") + payload


def split(raw):
    count = (len(raw) + 2943) // 2944
    return [wire(index, count, raw[index * 2944:(index + 1) * 2944])
            for index in range(count)]


def qr_image(raw):
    qr = qrcode.QRCode(version=40, error_correction=qrcode.constants.ERROR_CORRECT_L,
                       border=4, mask_pattern=0)
    qr.add_data(QRData(raw, mode=MODE_8BIT_BYTE), optimize=0)
    qr.make(fit=False)
    return (1 - np.asarray(qr.get_matrix(), np.uint8)).repeat(3, 0).repeat(3, 1) * 255


def write_image(path, image):
    options = [cv2.IMWRITE_JPEG_QUALITY, 95] if path.suffix.lower() == ".jpg" else []
    ok, encoded = cv2.imencode(path.suffix, image, options)
    if not ok:
        raise AssertionError("image encoding failed")
    path.write_bytes(encoded.tobytes())


class Barcode:
    def __init__(self, raw, valid=True):
        self.bytes = raw
        self.valid = valid
        self.error = "test checksum error" if not valid else None

    @property
    def text(self):
        raise AssertionError("result.text must never be accessed")


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.output = self.directory / "restored.bin"
        self.raw = fixture(6000)
        self.frames = split(self.raw)

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = receiver.main(list(map(str, arguments)))
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def invoke_frames(self, frames):
        with patch.object(decoder, "decode_photo", side_effect=frames) as decode:
            result = self.invoke("--output", self.output,
                                 *(f"photo-{i}.png" for i in range(len(frames))))
        return result, decode

    def assert_restored(self, frames, expected=None, duplicates=0):
        expected = self.raw if expected is None else expected
        summary = receiver.reassemble_frames(iter(frames), self.output)
        self.assertEqual(self.output.read_bytes(), expected)
        self.assertEqual(summary.byte_count, len(expected))
        self.assertEqual(summary.duplicate_count, duplicates)
        self.assertEqual(summary.frame_count, (len(expected) + 2943) // 2944)

    def assert_rejected(self, frames, category):
        # 不正なフレーム集合では、出力が未作成でも既存でも安全に保持されることを確認する。
        # Every malformed collection must leave both absent and existing outputs safe.
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.output.write_bytes(b"existing\x00\x1a\xff\r\n")
                result, _ = self.invoke_frames(frames)
                self.assertEqual(result[0], 1, result)
                self.assertEqual(result[1], "")
                self.assertIn(category + ":", result[2])
                self.assertNotIn("Traceback", result[2])
                if existing:
                    self.assertEqual(self.output.read_bytes(), b"existing\x00\x1a\xff\r\n")
                    self.output.unlink()
                else:
                    self.assertFalse(self.output.exists())

    def test_6000_in_order(self):
        self.assertEqual([frame[:9] for frame in self.frames], [
            bytes.fromhex("503938510100000003"), bytes.fromhex("503938510100010003"),
            bytes.fromhex("503938510100020003")])
        self.assertEqual(list(map(len, self.frames)), [2953, 2953, 121])
        self.assert_restored(self.frames)

    def test_6000_reverse(self):
        self.assert_restored(reversed(self.frames))

    def test_6000_arbitrary_order(self):
        self.assert_restored([self.frames[i] for i in (2, 0, 1)])

    def test_identical_duplicate(self):
        self.assert_restored([self.frames[i] for i in (2, 0, 1, 1, 2)], duplicates=2)

    def test_single_one_byte(self):
        self.assert_restored([wire(0, 1, b"\x00")], b"\x00")

    def test_single_2944_bytes(self):
        raw = fixture(2944)
        self.assert_restored(split(raw), raw)

    def test_2945_two_frames(self):
        raw = fixture(2945)
        frames = split(raw)
        self.assertEqual(list(map(len, frames)), [2953, 10])
        self.assert_restored(reversed(frames), raw)

    def test_5888_exact_multiple_no_terminal_frame(self):
        raw = fixture(5888)
        frames = split(raw)
        self.assertEqual(list(map(len, frames)), [2953, 2953])
        self.assert_restored(reversed(frames), raw)

    def test_special_bytes_magic_boundaries_and_trailing_ctrl_z(self):
        raw = bytearray(bytes(range(256)) * 24)
        special = b"\x00\x1a\x80\xff\r\nP98Q"
        for offset in (0, 2939, 5883):
            raw[offset:offset + len(special)] = special
        raw[-2:] = b"\x00\x1a"
        self.assert_restored(reversed(split(bytes(raw))), bytes(raw))

    def test_candidate_1474560_501_frames(self):
        raw = fixture(1474560)
        frames = split(raw)
        self.assertEqual(len(frames), 501)
        self.assertEqual(list(map(len, frames)), [2953] * 500 + [2569])
        random.Random(0x4C1).shuffle(frames)
        self.assert_restored(frames, raw)
        self.assertEqual(self.output.read_bytes(), model.reassemble_frames(frames))

    def test_above_candidate_502_frames(self):
        raw = fixture(1474945)
        self.assertEqual(len(split(raw)), 502)
        self.assert_restored(reversed(split(raw)), raw)

    def test_maximum_wire_index_big_endian(self):
        self.assertEqual(receiver.parse_frame(wire(65534, 65535, b"\x1a")), (65534, 65535))
        self.assertEqual(receiver.parse_frame(wire(256, 501, fixture(2944))), (256, 501))

    def test_known_same_count_mixing_is_not_detectable(self):
        other = bytes(value ^ 255 for value in self.raw)
        mixed = [self.frames[0], split(other)[1], self.frames[2]]
        expected = self.raw[:2944] + other[2944:5888] + self.raw[5888:]
        self.assert_restored(mixed, expected)
        self.assertNotEqual(self.output.read_bytes(), self.raw)
        self.assertNotEqual(self.output.read_bytes(), other)

    def test_cli_no_photos(self):
        code, stdout, stderr = self.invoke("--output", self.output)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("photos", stderr)
        self.assertFalse(self.output.exists())

    def test_cli_output_required(self):
        with patch.object(decoder, "decode_photo", return_value=wire(0, 1, b"\x00")) as decode, \
                patch.object(Path, "open") as output_open:
            code, stdout, stderr = self.invoke("photo.png")
        decode.assert_called_once()
        output_open.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--output", stderr)
        self.assertIn("output_required", stderr)

    def test_cli_success_consumes_every_photo_before_output(self):
        frames = [self.frames[i] for i in (2, 0, 1, 1)]
        def decode(path, *, report_errors):
            self.assertTrue(report_errors)
            self.assertFalse(self.output.exists())
            return frames.pop(0)
        with patch.object(decoder, "decode_photo", side_effect=decode) as read:
            code, stdout, stderr = self.invoke("--output", self.output, "a", "b", "c", "d")
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("6000 bytes from 3 frames (1 identical duplicates)", stdout)
        self.assertEqual(read.call_count, 4)
        self.assertEqual(self.output.read_bytes(), self.raw)

    def test_missing_photo(self):
        code, stdout, stderr = self.invoke("--output", self.output, self.directory / "absent.jpg")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("input_photo_unreadable", stderr)
        self.assertFalse(self.output.exists())

    def test_invalid_image(self):
        photo = self.directory / "broken.jpg"
        photo.write_bytes(b"not an image")
        code, _, stderr = self.invoke("--output", self.output, photo)
        self.assertEqual(code, 2)
        self.assertIn("input_photo_unreadable", stderr)
        self.assertEqual(photo.read_bytes(), b"not an image")
        self.assertFalse(self.output.exists())

    def test_empty_image(self):
        photo = self.directory / "empty.jpg"
        photo.touch()
        code, _, stderr = self.invoke("--output", self.output, photo)
        self.assertEqual(code, 2)
        self.assertIn("Empty image file", stderr)
        self.assertFalse(self.output.exists())

    def test_image_access_denied(self):
        with patch.object(decoder, "decode_photo", side_effect=PermissionError("denied")):
            code, _, stderr = self.invoke("--output", self.output, "photo.jpg")
        self.assertEqual(code, 2)
        self.assertIn("denied", stderr)
        self.assertFalse(self.output.exists())

    def test_opencv_image_error(self):
        with patch.object(decoder, "decode_photo", side_effect=cv2.error("bad image")):
            code, _, stderr = self.invoke("--output", self.output, "photo.jpg")
        self.assertEqual(code, 2)
        self.assertIn("input_photo_unreadable", stderr)
        self.assertFalse(self.output.exists())

    def test_no_qr_in_blank_image(self):
        photo = self.directory / "blank.png"
        write_image(photo, np.full((400, 640, 3), 255, np.uint8))
        code, _, stderr = self.invoke("--output", self.output, photo)
        self.assertEqual(code, 1)
        self.assertIn("qr_not_found", stderr)
        self.assertFalse(self.output.exists())

    def test_invalid_qr_result(self):
        photo = self.directory / "qr.png"
        write_image(photo, np.full((100, 100, 3), 255, np.uint8))
        with patch.object(decoder.zxingcpp, "read_barcode", return_value=Barcode(b"", False)) as read:
            code, _, stderr = self.invoke("--output", self.output, photo)
        self.assertEqual(code, 1)
        self.assertIn("qr_decode_invalid", stderr)
        self.assertIn("test checksum error", stderr)
        self.assertEqual(read.call_count, 5)
        self.assertFalse(self.output.exists())

    def test_decoder_runtime_error(self):
        with patch.object(decoder, "decode_photo", side_effect=RuntimeError("decode failed")):
            code, _, stderr = self.invoke("--output", self.output, "photo.png")
        self.assertEqual(code, 1)
        self.assertIn("qr_decode_invalid", stderr)
        self.assertFalse(self.output.exists())

    def test_nonbytes_decode_rejected(self):
        self.assert_rejected(["P98Q is text"], "qr_decode_invalid")

    def test_headerless_raw_qr_rejected(self):
        self.assert_rejected([fixture(2944)], "transport_magic_invalid")

    def test_no_header_search_inside_payload(self):
        self.assert_rejected([b"prefix----" + wire(0, 1, b"x")], "transport_magic_invalid")

    def test_each_magic_byte_invalid(self):
        for offset in range(4):
            raw = bytearray(wire(0, 1, b"x"))
            raw[offset] ^= 255
            self.assert_rejected([bytes(raw)], "transport_magic_invalid")

    def test_version_unsupported(self):
        for version in (0, 3, 255):
            self.assert_rejected([b"P98Q" + bytes([version]) + b"\x00\x00\x00\x01x"],
                                 "transport_version_unsupported")

    def test_zero_count(self):
        self.assert_rejected([wire(0, 0, b"x")], "frame_count_invalid")

    def test_index_out_of_range(self):
        for index, count in ((1, 1), (2, 1), (65535, 65535)):
            self.assert_rejected([wire(index, count, b"x")], "frame_index_invalid")

    def test_short_header_and_header_only(self):
        for length in range(10):
            self.assert_rejected([wire(0, 1, b"x")[:length]], "frame_length_invalid")

    def test_empty_payload_nonfinal_and_final(self):
        for index in (0, 1):
            self.assert_rejected([wire(index, 2, b"")], "frame_length_invalid")

    def test_oversized_frame(self):
        for index, count in ((0, 1), (0, 2), (1, 2)):
            self.assert_rejected([wire(index, count, fixture(2945))], "frame_length_invalid")

    def test_nonfinal_short_payload(self):
        for size in (1, 2943):
            self.assert_rejected([wire(0, 2, fixture(size))], "nonfinal_payload_length_invalid")

    def test_count_mismatch_both_orders(self):
        foreign = wire(0, 4, fixture(2944))
        self.assert_rejected([foreign, *self.frames], "frame_count_mismatch")
        self.assert_rejected([*self.frames, foreign], "frame_count_mismatch")

    def test_conflicting_payload_both_orders(self):
        conflict = self.frames[1][:-1] + bytes([self.frames[1][-1] ^ 255])
        for pair in ((self.frames[1], conflict), (conflict, self.frames[1])):
            self.assert_rejected([self.frames[0], *pair, self.frames[2]], "conflicting_duplicate")

    def test_conflicting_final_length_after_complete(self):
        for conflict in (self.frames[2][:-1], self.frames[2] + b"\x00"):
            self.assert_rejected([*self.frames, conflict], "conflicting_duplicate")

    def test_missing_middle(self):
        self.assert_rejected([self.frames[0], self.frames[2]], "missing_frame")

    def test_missing_final(self):
        self.assert_rejected(self.frames[:2], "missing_frame")

    def test_missing_first(self):
        self.assert_rejected(self.frames[1:], "missing_frame")

    def test_duplicates_do_not_fill_missing(self):
        self.assert_rejected([self.frames[i] for i in (0, 0, 2)], "missing_frame")

    def test_empty_raw_collection(self):
        with self.assertRaisesRegex(receiver.ReceiveError, "missing_frame"):
            receiver.reassemble_frames([], self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_late_frame_after_complete(self):
        self.assert_rejected([*self.frames, b""], "frame_length_invalid")

    def test_unreadable_late_photo_after_complete(self):
        result, decode = self.invoke_frames([*self.frames, FileNotFoundError("late.jpg")])
        self.assertEqual(result[0], 2)
        self.assertIn("input_photo_unreadable", result[2])
        self.assertEqual(decode.call_count, 4)
        self.assertFalse(self.output.exists())

    def test_existing_output_unchanged(self):
        self.output.write_bytes(b"keep\x00\xff\x1a")
        result, _ = self.invoke_frames(self.frames)
        self.assertEqual(result[0], 3)
        self.assertIn("output_already_exists", result[2])
        self.assertEqual(self.output.read_bytes(), b"keep\x00\xff\x1a")

    def test_output_created_by_other_writer_during_decode(self):
        frames = iter(self.frames)
        def decode(*args, **kwargs):
            raw = next(frames)
            if raw == self.frames[-1]:
                self.output.write_bytes(b"other writer")
            return raw
        with patch.object(decoder, "decode_photo", side_effect=decode):
            code, _, stderr = self.invoke("--output", self.output, "0", "1", "2")
        self.assertEqual(code, 3)
        self.assertIn("output_already_exists", stderr)
        self.assertEqual(self.output.read_bytes(), b"other writer")

    def test_missing_output_parent(self):
        self.output = self.directory / "absent" / "out.bin"
        result, _ = self.invoke_frames(self.frames)
        self.assertEqual(result[0], 3)
        self.assertIn("output_write_error", result[2])
        self.assertFalse(self.output.parent.exists())

    def test_output_open_denied_does_not_delete_existing(self):
        self.output.write_bytes(b"keep")
        with patch.object(Path, "open", side_effect=PermissionError("denied")), \
                patch.object(Path, "unlink") as unlink:
            result, _ = self.invoke_frames(self.frames)
        self.assertEqual(result[0], 3)
        self.assertIn("output_write_error", result[2])
        unlink.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"keep")

    def faulty_save(self, fault):
        original_open = Path.open
        owner = self
        class FaultyStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def fileno(self):
                return self.stream.fileno()
            def write(self, payload):
                if fault == "close":
                    return self.stream.write(payload)
                self.stream.write(payload[:3])
                if fault == "short":
                    return 3
                raise OSError("injected disk error")
            def __exit__(self, *args):
                self.stream.close()
                if fault == "replacement":
                    owner.output.rename(owner.directory / "owned-partial.bin")
                    owner.output.write_bytes(b"replacement")
                if fault == "close":
                    raise OSError("injected flush/close error")
        def open_output(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path == self.output and mode == "xb":
                return FaultyStream(stream)
            return stream
        with patch.object(Path, "open", new=open_output):
            return self.invoke_frames(self.frames)[0]

    def test_write_error_removes_partial(self):
        result = self.faulty_save("write")
        self.assertEqual(result[0], 3)
        self.assertIn("injected disk error; partial output removed", result[2])
        self.assertFalse(self.output.exists())

    def test_short_write_removes_partial(self):
        result = self.faulty_save("short")
        self.assertEqual(result[0], 3)
        self.assertIn("short output write", result[2])
        self.assertFalse(self.output.exists())

    def test_close_failure_removes_output(self):
        result = self.faulty_save("close")
        self.assertEqual(result[0], 3)
        self.assertIn("injected flush/close error", result[2])
        self.assertFalse(self.output.exists())

    def test_cleanup_failure_reported(self):
        with patch.object(Path, "unlink", side_effect=PermissionError("sharing violation")):
            result = self.faulty_save("write")
        self.assertEqual(result[0], 3)
        self.assertIn("partial output cleanup failed", result[2])
        self.assertIn("sharing violation", result[2])
        self.assertEqual(self.output.read_bytes(), self.raw[:3])

    def test_cleanup_does_not_delete_replacement(self):
        result = self.faulty_save("replacement")
        self.assertEqual(result[0], 3)
        self.assertIn("cleanup refused", result[2])
        self.assertEqual(self.output.read_bytes(), b"replacement")
        self.assertEqual((self.directory / "owned-partial.bin").read_bytes(), self.raw[:3])

    def test_shared_decoder_fallback_keeps_conditions_and_bytes(self):
        image = np.full((160, 240, 3), 255, np.uint8)
        for failures in range(5):
            with self.subTest(failures=failures), patch.object(decoder.zxingcpp, "read_barcode") as read:
                read.side_effect = [Barcode(b"invalid", False)] * failures + [Barcode(self.frames[0])]
                raw, scale = decoder.decode_image(image, report_errors=True)
                self.assertEqual(raw, self.frames[0])
                self.assertEqual(scale, decoder.DECODE_SCALES[failures])
                self.assertEqual(read.call_count, failures + 1)
                for call in read.call_args_list:
                    self.assertEqual(call.kwargs, {
                        "formats": decoder.zxingcpp.BarcodeFormat.QRCode,
                        "try_downscale": True, "return_errors": True})

    def test_invalid_result_then_no_detections_keeps_diagnostic(self):
        with patch.object(decoder.zxingcpp, "read_barcode",
                          side_effect=[Barcode(b"", False)] + [None] * 4):
            with self.assertRaisesRegex(decoder.QRDecodeError, "checksum"):
                decoder.decode_image(np.full((160, 240, 3), 255, np.uint8), report_errors=True)

    def test_images_released_before_next_photo(self):
        photo = self.directory / "photo.png"
        photo.write_bytes(b"mock image input")
        references = []
        frames = iter(self.frames)
        def load(*args):
            self.assertTrue(all(reference() is None for reference in references))
            image = np.full((100, 100, 3), 255, np.uint8)
            references.append(weakref.ref(image))
            return image
        def read(*args, **kwargs):
            return Barcode(next(frames))
        with patch.object(decoder.cv2, "imdecode", new=load), \
                patch.object(decoder.zxingcpp, "read_barcode", new=read):
            code, _, stderr = self.invoke("--output", self.output, photo, photo, photo)
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(self.output.read_bytes(), self.raw)


class PhotoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = fixture(6000)
        cls.frames = split(cls.raw)
        cls.images = [qr_image(raw) for raw in cls.frames]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "写真 with spaces"
        self.directory.mkdir()

    def cli(self, output, photos):
        return subprocess.run([sys.executable, "-B", "-X", "utf8",
                               str(ROOT / "windows/reassemble_photos.py"),
                               "--output", str(output), *map(str, photos)],
                              capture_output=True, text=True, encoding="utf-8", cwd=self.directory)

    def restore_images(self, suffix, order):
        photos = [self.directory / f"unrelated-name-{2-i}{suffix}" for i in range(3)]
        for path, image, raw in zip(photos, self.images, self.frames):
            write_image(path, image)
            self.assertEqual(decoder.decode_photo(path, report_errors=True), raw)
        before = [path.read_bytes() for path in photos]
        output = self.directory / "復元 binary.bin"
        result = self.cli(output, [photos[i] for i in order])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("Restored: 6000 bytes from 3 frames", result.stdout)
        self.assertEqual(output.stat().st_size, 6000)
        self.assertEqual(output.read_bytes(), self.raw)
        self.assertEqual([path.read_bytes() for path in photos], before)

    def test_png_6000_in_order(self):
        self.restore_images(".png", (0, 1, 2))

    def test_png_6000_reverse(self):
        self.restore_images(".png", (2, 1, 0))

    def test_png_6000_arbitrary_with_duplicate(self):
        self.restore_images(".png", (2, 0, 1, 0))

    def test_jpeg_6000_arbitrary(self):
        self.restore_images(".jpg", (2, 0, 1))

    def test_headerless_real_qr(self):
        photo = self.directory / "raw.png"
        write_image(photo, qr_image(b"\x00\x1a\x80\xff\r\nP98Q\x00\x1a"))
        output = self.directory / "restored.bin"
        result = self.cli(output, [photo])
        self.assertEqual(result.returncode, 1)
        self.assertIn("transport_magic_invalid", result.stderr)
        self.assertFalse(output.exists())

    def test_output_same_as_photo_preserves_photo(self):
        photo = self.directory / "same.png"
        write_image(photo, qr_image(wire(0, 1, b"\x00\x1a\x80\xff\r\nP98Q\x00\x1a")))
        before = photo.read_bytes()
        result = self.cli(photo, [photo])
        self.assertEqual(result.returncode, 3)
        self.assertIn("output_already_exists", result.stderr)
        self.assertEqual(photo.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
