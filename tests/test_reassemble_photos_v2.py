# 独立転送入力と隔離出力による、移植可能なv2受信側契約の試験。
"""Portable v2 receiver contracts, independent wire inputs and isolated outputs."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import frame_v2_wire_oracle as oracle
from test_reassemble_photos import qr_image, write_image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from windows import reassemble_photos as receiver
from windows import decode_binary_photo as decoder


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


def single(name=b"TEST.MAG", payload=b"\x00\x1a\x80\xff\r\n"):
    return oracle.frame(0, 1, bytes([len(name)]) + name + payload)


class ReceiverV2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.output = self.directory / "explicit output.bin"
        self.automatic = self.directory / "TEST.MAG"
        self.raw = oracle.fixture(6000)
        self.frames = oracle.split(self.raw)

    def invoke(self, frames, explicit=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        options = ["--output", str(self.output)] if explicit else []
        options += [f"photo-{i}.png" for i in range(len(frames))]
        with working_directory(self.directory), redirect_stdout(stdout), redirect_stderr(stderr), \
                patch.object(decoder, "decode_photo", side_effect=frames):
            try:
                code = receiver.main(options)
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_restored(self, source, frames=None, name=b"TEST.MAG", duplicates=0):
        frames = oracle.split(source, name) if frames is None else list(frames)
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                code, stdout, stderr = self.invoke(frames, explicit)
                output = self.output if explicit else self.directory / name.decode("ascii")
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual(output.read_bytes(), source)
                unique = len(oracle.split(source, name))
                shown = str(self.output) if explicit else name.decode("ascii")
                self.assertEqual(stdout, f"Restored: {len(source)} bytes from {unique} frames "
                                 f"({duplicates} identical duplicates) -> {shown}\n")
                output.unlink()
                self.assertEqual(list(self.directory.iterdir()), [])

    def assert_rejected(self, frames, category):
        for explicit in (False, True):
            for existing in (False, True):
                with self.subTest(explicit=explicit, existing=existing):
                    output = self.output if explicit else self.automatic
                    if existing:
                        output.write_bytes(b"keep\x00\xff\x1a")
                    # 攻撃用の名前も含め、どのパスも出力用に開かれないことを確認する。
                    # No path may be opened for output, including an attacker name.
                    with patch.object(Path, "open", side_effect=AssertionError("early output open")):
                        code, stdout, stderr = self.invoke(frames, explicit)
                    self.assertEqual((code, stdout), (1, ""), stderr)
                    self.assertIn(category + ":", stderr)
                    self.assertNotIn("Traceback", stderr)
                    if existing:
                        self.assertEqual(output.read_bytes(), b"keep\x00\xff\x1a")
                        output.unlink()
                    self.assertEqual(list(self.directory.iterdir()), [])

    def test_fixed_6000_vector_and_restore(self):
        expected = [oracle.PREFIX_6000 + self.raw[:2935],
                    bytes.fromhex("503938510200010003") + self.raw[2935:5879],
                    bytes.fromhex("503938510200020003") + self.raw[5879:]]
        self.assertEqual(self.frames, expected)
        self.assertEqual(list(map(len, self.frames)), [2953, 2953, 130])
        self.assert_restored(self.raw, expected)

    def test_fixed_29198_vector_and_restore(self):
        raw = oracle.fixture(29198)
        frames = oracle.split(raw)
        self.assertEqual(len(frames), 10)
        self.assertEqual(frames[0][:18], bytes.fromhex("50393851020000000a08544553542e4d4147"))
        self.assertEqual(frames[-1], bytes.fromhex("50393851020009000a") + raw[26487:])
        self.assertEqual(len(raw[26487:]), 2711)
        self.assertEqual(list(map(len, frames)), [2953] * 9 + [2720])
        self.assert_restored(raw, reversed(frames))

    def test_single_one_byte_fixed_vector(self):
        self.assert_restored(b"\x00", [bytes.fromhex("503938510200000001014100")], name=b"A")

    def test_single_all_256_values(self):
        self.assert_restored(bytes(range(256)))

    def test_special_bytes_across_new_boundaries(self):
        raw = bytearray(bytes(range(256)) * 24)
        for offset in (0, 2931, 5875):
            raw[offset:offset + len(oracle.SPECIAL)] = oracle.SPECIAL
        raw[-2:] = b"\x00\x1a"
        self.assert_restored(bytes(raw), reversed(oracle.split(bytes(raw))))

    def test_frame_zero_last(self):
        self.assert_restored(self.raw, [self.frames[i] for i in (2, 1, 0)])

    def test_arbitrary_order(self):
        self.assert_restored(self.raw, [self.frames[i] for i in (1, 0, 2)])

    def test_identical_duplicates_all_indices(self):
        self.assert_restored(self.raw, [self.frames[i] for i in (2, 0, 1, 0, 2, 1)], duplicates=3)

    def test_all_filename_lengths_and_capacity_boundaries(self):
        for n, name in enumerate(oracle.NAMES, 1):
            self.assertEqual(len(name), n)
            first = 2943 - n
            for size in (1, first - 1, first, first + 1, first + 2944, first + 2945):
                with self.subTest(n=n, size=size):
                    self.assert_restored(oracle.fixture(size), name=name)

    def test_metadata_lookalike_in_later_payload(self):
        raw = self.raw[:2935] + b"\x08TEST.MAG" + self.raw[2944:]
        frames = oracle.split(raw)
        self.assertEqual(frames[1][9:18], b"\x08TEST.MAG")
        self.assert_restored(raw, frames)

    def test_candidate_size_is_not_a_limit(self):
        for size in (1474560, 1474561):
            raw = oracle.fixture(size)
            frames = oracle.split(raw)
            self.assertEqual(len(frames), 501)
            random.Random(0x4E1).shuffle(frames)
            self.assert_restored(raw, frames)

    def test_big_endian_public_parser_contract(self):
        for index, count in ((255, 256), (256, 501), (65534, 65535)):
            payload = b"x" if index == count - 1 else b"x" * 2944
            self.assertEqual(receiver.parse_frame(oracle.frame(index, count, payload)), (index, count))

    def test_public_collection_and_summary_contracts(self):
        ordered, duplicates = receiver.collect_frames([self.frames[i] for i in (2, 1, 0, 0)])
        self.assertEqual((ordered, duplicates), (self.frames, 1))
        summary = receiver.reassemble_frames(ordered, self.output)
        self.assertIsInstance(summary, receiver.Summary)
        self.assertEqual(tuple(summary), (3, 0, 6000))
        self.assertEqual(self.output.read_bytes(), self.raw)

    def test_public_reassemble_default_output(self):
        with working_directory(self.directory):
            summary = receiver.reassemble_frames(self.frames)
        self.assertEqual(tuple(summary), (3, 0, 6000))
        self.assertEqual(self.automatic.read_bytes(), self.raw)

    def test_filename_allowed_characters(self):
        for char in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~":
            name = bytes([char]) + b"." + bytes([char])
            receiver._validate_filename_v2(name)
            self.assertEqual(receiver.parse_frame(single(name)), (0, 1))

    def test_filename_all_256_values(self):
        allowed = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~.")
        for byte in range(256):
            name = b"A" + bytes([byte]) + b"B"
            with self.subTest(byte=byte):
                if byte in allowed:
                    receiver._validate_filename_v2(name)
                else:
                    with self.assertRaisesRegex(receiver.ReceiveError, "filename_invalid"):
                        receiver._validate_filename_v2(name)

    def test_all_valid_basename_extension_lengths(self):
        for base in range(1, 9):
            for extension in range(4):
                name = b"A" * base + (b"." + b"X" * extension if extension else b"")
                self.assertEqual(receiver.parse_frame(single(name)), (0, 1))

    def test_reserved_prefixes_are_accepted(self):
        for name in (b"CONSOLE.TXT", b"COMPRESS.DAT", b"CONIN$1.TXT", b"CONOUT$1.TXT",
                     b"COM10.BIN", b"LPT10", b"NUL1"):
            self.assert_restored(oracle.SPECIAL, name=name)

    def test_all_special_characters_can_be_saved(self):
        for char in b"!#$%&'()-@^_`{}~":
            self.assert_restored(oracle.SPECIAL, name=bytes([char]) + b".BIN")

    def test_filename_case_rejected_even_with_output(self):
        for name in (b"test.mag", b"TEST.mag", b"TeSt.MAG"):
            self.assert_rejected([single(name)], "filename_invalid")

    def test_filename_grammar_rejected_even_with_output(self):
        for name in (b".", b"..", b".A", b"A.", b"A..B", b"A.B.C", b"ABCDEFGHI",
                     b"ABCDEFGHI.X", b"A.ABCD", b" A", b"A ", b"A\x00B", b"A\n", b"A\x1a",
                     b"A\x7f", b"A\x80", b"A\xff", b"A/B", b"A\\B", b"B:A", b"B:\\A",
                     b"/A", b"\\A", b"../A", b"..\\A", b"A:B", b"A*B", b"A?B", b'A"B'):
            self.assert_rejected([single(name)], "filename_invalid")

    def test_reserved_names_with_and_without_extension(self):
        reserved = [b"CON", b"CONIN$", b"CONOUT$", b"PRN", b"AUX", b"NUL", b"CLOCK$",
                    b"COM", b"LPT", b"LST"]
        reserved += [f"{prefix}{i}".encode("ascii") for prefix in ("COM", "LPT") for i in range(10)]
        for base in reserved:
            for ext in (b"", b".MAG"):
                self.assert_rejected([single(base + ext)], "filename_reserved")

    def test_metadata_length_zero(self):
        self.assert_rejected([oracle.frame(0, 1, b"\x00raw")], "filename_length_invalid")

    def test_metadata_length_13_through_255(self):
        for n in range(13, 256):
            self.assert_rejected([oracle.frame(0, 1, bytes([n]) + b"A" * 12 + b"raw")],
                                 "filename_length_invalid")

    def test_old_unsupported_v2_vector_is_invalid_metadata(self):
        self.assert_rejected([bytes.fromhex("50393851020000000178")], "filename_length_invalid")

    def test_truncated_metadata(self):
        for n in (1, 8, 12):
            for received in range(n):
                self.assert_rejected([oracle.frame(0, 1, bytes([n]) + b"A" * received)],
                                     "filename_truncated")

    def test_frame_zero_without_raw(self):
        for count in (1, 2):
            self.assert_rejected([oracle.frame(0, count, b"\x08TEST.MAG")], "frame_length_invalid")

    def test_later_frame_without_raw(self):
        for index in (1, 2):
            self.assert_rejected([oracle.frame(index, 3, b"")], "frame_length_invalid")

    def test_short_headers_and_missing_metadata_length(self):
        for n in range(10):
            self.assert_rejected([single()[:n]], "frame_length_invalid")

    def test_oversized_complete_frames(self):
        for index, count in ((0, 1), (0, 2), (1, 2)):
            body = b"\x08TEST.MAG" if index == 0 else b""
            self.assert_rejected([oracle.frame(index, count, body + b"x" * (2945 - len(body)))],
                                 "frame_length_invalid")

    def test_nonfinal_short_frame_zero(self):
        self.assert_rejected([self.frames[0][:-1], *self.frames[1:]], "nonfinal_payload_length_invalid")

    def test_nonfinal_short_later_frame(self):
        self.assert_rejected([self.frames[0], self.frames[1][:-1], self.frames[2]],
                             "nonfinal_payload_length_invalid")

    def test_invalid_magic(self):
        for offset in range(4):
            frame = bytearray(single())
            frame[offset] ^= 255
            self.assert_rejected([bytes(frame)], "transport_magic_invalid")

    def test_unknown_versions(self):
        for version in (0, 3, 255):
            self.assert_rejected([single()[:4] + bytes([version]) + single()[5:]],
                                 "transport_version_unsupported")

    def test_zero_count(self):
        self.assert_rejected([oracle.frame(0, 0, single()[9:])], "frame_count_invalid")

    def test_index_out_of_range(self):
        for index, count in ((1, 1), (2, 1), (65535, 65535)):
            self.assert_rejected([oracle.frame(index, count, b"x")], "frame_index_invalid")

    def test_mixed_versions_both_orders(self):
        v1 = oracle.frame(0, 1, b"x", version=1)
        for pair in ((v1, single()), (single(), v1)):
            self.assert_rejected(pair, "transport_version_mismatch")

    def test_mixed_versions_before_count_mismatch(self):
        v1 = oracle.frame(0, 2, b"x" * 2944, version=1)
        for pair in ((v1, single()), (single(), v1)):
            self.assert_rejected(pair, "transport_version_mismatch")

    def test_count_mismatch_both_orders(self):
        foreign = self.frames[0][:7] + b"\x00\x04" + self.frames[0][9:]
        for frames in ([foreign, *self.frames], [*self.frames, foreign]):
            self.assert_rejected(frames, "frame_count_mismatch")

    def test_missing_frame_zero(self):
        self.assert_rejected(self.frames[1:], "missing_frame")

    def test_missing_middle_and_final(self):
        for absent in (1, 2):
            self.assert_rejected([f for i, f in enumerate(self.frames) if i != absent], "missing_frame")

    def test_duplicates_do_not_fill_missing(self):
        self.assert_rejected([self.frames[i] for i in (1, 1, 2)], "missing_frame")

    def test_conflicting_duplicate_payload_both_orders(self):
        changed = self.frames[1][:-1] + bytes([self.frames[1][-1] ^ 255])
        for pair in ((changed, self.frames[1]), (self.frames[1], changed)):
            self.assert_rejected([self.frames[0], *pair, self.frames[2]], "conflicting_duplicate")

    def test_conflicting_frame_zero_filename(self):
        changed = self.frames[0][:10] + b"BEST.MAG" + self.frames[0][18:]
        for pair in ((changed, self.frames[0]), (self.frames[0], changed)):
            self.assert_rejected([*pair, *self.frames[1:]], "conflicting_duplicate")

    def test_conflicting_final_length_after_complete(self):
        for changed in (self.frames[-1][:-1], self.frames[-1] + b"\x00"):
            self.assert_rejected([*self.frames, changed], "conflicting_duplicate")

    def test_late_invalid_frame_after_complete(self):
        self.assert_rejected([*self.frames, b""], "frame_length_invalid")

    def test_late_invalid_metadata_after_complete(self):
        self.assert_rejected([*self.frames, single(b"test.mag")], "filename_invalid")

    def test_late_unreadable_photo_after_complete(self):
        for explicit in (False, True):
            with patch.object(Path, "open", side_effect=AssertionError("early output")):
                code, stdout, stderr = self.invoke([*self.frames, FileNotFoundError("late.jpg")], explicit)
            self.assertEqual((code, stdout), (2, ""))
            self.assertIn("input_photo_unreadable", stderr)
            self.assertEqual(list(self.directory.iterdir()), [])

    def test_every_photo_consumed_before_open(self):
        frames = [self.frames[i] for i in (2, 1, 0, 0)]
        def inputs():
            for frame in frames:
                self.assertFalse(self.automatic.exists())
                yield frame
        with working_directory(self.directory):
            summary = receiver.reassemble_frames(inputs())
        self.assertEqual(tuple(summary), (3, 1, 6000))
        self.assertEqual(self.automatic.read_bytes(), self.raw)

    def test_decode_failure_precedes_omitted_output_error(self):
        code, stdout, stderr = self.invoke([FileNotFoundError("missing.jpg")])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("input_photo_unreadable", stderr)
        self.assertNotIn("output_required", stderr)

    def test_invalid_frame_precedes_omitted_output_error(self):
        self.assert_rejected([oracle.frame(0, 0, b"x", version=1)], "frame_count_invalid")

    def test_v1_no_output_full_collection_required(self):
        frames = [oracle.frame(0, 2, b"x" * 2944, version=1), oracle.frame(1, 2, b"y", version=1)]
        code, stdout, stderr = self.invoke(frames)
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("output_required: --output", stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_explicit_output_accepts_unicode_long_path(self):
        self.output = self.directory / "長い restored filename beyond 8.3.bin"
        self.assert_restored(self.raw)

    def test_existing_output_unchanged(self):
        for explicit in (False, True):
            output = self.output if explicit else self.automatic
            output.write_bytes(oracle.SPECIAL)
            code, stdout, stderr = self.invoke(self.frames, explicit)
            self.assertEqual((code, stdout), (3, ""))
            self.assertIn("output_already_exists", stderr)
            self.assertEqual(output.read_bytes(), oracle.SPECIAL)
            output.unlink()

    def test_existing_directory_unchanged(self):
        self.automatic.mkdir()
        marker = self.automatic / "marker.bin"
        marker.write_bytes(oracle.SPECIAL)
        code, stdout, stderr = self.invoke(self.frames)
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("output_", stderr)
        self.assertTrue(self.automatic.is_dir())
        self.assertEqual(marker.read_bytes(), oracle.SPECIAL)

    def test_existing_symlink_unchanged(self):
        target = self.directory / "target.bin"
        target.write_bytes(oracle.SPECIAL)
        self.automatic.symlink_to(target)
        code, stdout, _ = self.invoke(self.frames)
        self.assertEqual((code, stdout), (3, ""))
        self.assertTrue(self.automatic.is_symlink())
        self.assertEqual(target.read_bytes(), oracle.SPECIAL)

    def test_dangling_symlink_unchanged(self):
        target = self.directory / "absent.bin"
        self.automatic.symlink_to(target)
        code, stdout, _ = self.invoke(self.frames)
        self.assertEqual((code, stdout), (3, ""))
        self.assertTrue(self.automatic.is_symlink())
        self.assertFalse(target.exists())

    def test_name_collision_follows_filesystem_case_rules(self):
        lower = self.directory / "test.mag"
        lower.write_bytes(oracle.SPECIAL)
        insensitive = self.automatic.exists()
        code, stdout, stderr = self.invoke(self.frames)
        if insensitive:
            self.assertEqual((code, stdout), (3, ""))
            self.assertIn("output_already_exists", stderr)
        else:
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(self.automatic.read_bytes(), self.raw)
        self.assertEqual(lower.read_bytes(), oracle.SPECIAL)

    def test_creation_race_at_exclusive_open(self):
        original_open = Path.open
        def competing_open(path, mode="r", *args, **kwargs):
            if mode == "xb":
                self.assertEqual(path, Path("TEST.MAG"))
                with original_open(path, "wb") as other:
                    other.write(b"other writer")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", new=competing_open):
            code, stdout, stderr = self.invoke(self.frames)
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("output_already_exists", stderr)
        self.assertEqual(self.automatic.read_bytes(), b"other writer")

    def test_open_failure_does_not_cleanup(self):
        with patch.object(Path, "open", side_effect=PermissionError("denied")), \
                patch.object(Path, "unlink") as unlink:
            code, stdout, stderr = self.invoke(self.frames)
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("output_write_error", stderr)
        unlink.assert_not_called()

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
                    owner.automatic.rename(owner.directory / "owned-partial.bin")
                    with original_open(owner.automatic, "wb") as replacement:
                        replacement.write(b"replacement")
                if fault == "close":
                    raise OSError("injected flush/close error")
        def open_output(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return FaultyStream(stream) if mode == "xb" else stream
        with patch.object(Path, "open", new=open_output):
            return self.invoke(self.frames)

    def test_write_error_removes_partial(self):
        code, stdout, stderr = self.faulty_save("write")
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("injected disk error; partial output removed", stderr)
        self.assertFalse(self.automatic.exists())

    def test_short_write_removes_partial(self):
        code, stdout, stderr = self.faulty_save("short")
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("short output write; partial output removed", stderr)
        self.assertFalse(self.automatic.exists())

    def test_flush_close_error_removes_partial(self):
        code, stdout, stderr = self.faulty_save("close")
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("injected flush/close error; partial output removed", stderr)
        self.assertFalse(self.automatic.exists())

    def test_cleanup_failure_is_reported(self):
        with patch.object(Path, "unlink", side_effect=PermissionError("sharing violation")):
            code, stdout, stderr = self.faulty_save("write")
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("partial output cleanup failed: sharing violation", stderr)
        self.assertEqual(self.automatic.read_bytes(), self.raw[:3])

    def test_cleanup_preserves_replacement(self):
        code, stdout, stderr = self.faulty_save("replacement")
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("cleanup refused", stderr)
        self.assertEqual(self.automatic.read_bytes(), b"replacement")
        self.assertEqual((self.directory / "owned-partial.bin").read_bytes(), self.raw[:3])

    def test_unknown_identity_refuses_cleanup(self):
        with patch.object(receiver.os, "fstat", side_effect=OSError("identity unavailable")):
            code, stdout, stderr = self.invoke(self.frames)
        self.assertEqual((code, stdout), (3, ""))
        self.assertIn("cleanup refused", stderr)
        self.assertEqual(self.automatic.read_bytes(), b"")

    def test_known_same_count_mixed_transfers_not_detectable(self):
        other = bytes(b ^ 255 for b in self.raw)
        mixed = [self.frames[0], oracle.split(other, b"ELSE.BIN")[1], self.frames[2]]
        expected = self.raw[:2935] + other[2935:5879] + self.raw[5879:]
        self.assert_restored(expected, mixed)
        self.assertNotEqual(expected, self.raw)
        self.assertNotEqual(expected, other)


class PhotoV2IntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.photos = self.directory / "写真 with spaces"
        self.photos.mkdir()
        self.destination = self.directory / "destination"
        self.destination.mkdir()

    def cli(self, frames, *, explicit=False, suffix=".png", order=None):
        paths = []
        for index, raw in enumerate(frames):
            path = self.photos / f"unrelated-{index}{suffix}"
            write_image(path, qr_image(raw))
            self.assertEqual(decoder.decode_photo(path, report_errors=True), raw)
            paths.append(path)
        before = [path.read_bytes() for path in paths]
        order = list(range(len(paths))) if order is None else order
        options = ["--output", "explicit restored binary.bin"] if explicit else []
        result = subprocess.run([sys.executable, "-B", "-X", "utf8",
                                 str(ROOT / "windows/reassemble_photos.py"), *options,
                                 *(str(paths[i]) for i in order)], cwd=self.destination,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual([path.read_bytes() for path in paths], before)
        return result

    def test_png_automatic_filename_in_subprocess_cwd(self):
        raw = oracle.fixture(6000)
        result = self.cli(oracle.split(raw), order=(2, 1, 0, 0))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout, "Restored: 6000 bytes from 3 frames (1 identical duplicates) -> TEST.MAG\n")
        self.assertEqual((self.destination / "TEST.MAG").read_bytes(), raw)
        self.assertFalse((self.photos / "TEST.MAG").exists())

    def test_jpeg_explicit_output(self):
        raw = oracle.fixture(6000)
        result = self.cli(oracle.split(raw), explicit=True, suffix=".jpg", order=(2, 0, 1))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertIn("-> explicit restored binary.bin", result.stdout)
        self.assertEqual((self.destination / "explicit restored binary.bin").read_bytes(), raw)
        self.assertFalse((self.destination / "TEST.MAG").exists())

    def test_single_binary_automatic(self):
        raw = bytes(range(256)) + oracle.SPECIAL
        result = self.cli(oracle.split(raw, b"A.BIN"))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual((self.destination / "A.BIN").read_bytes(), raw)

    def test_v1_explicit_output_unchanged(self):
        raw = oracle.SPECIAL
        result = self.cli([oracle.frame(0, 1, raw, version=1)], explicit=True)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual((self.destination / "explicit restored binary.bin").read_bytes(), raw)
        self.assertEqual(result.stdout, f"Restored: {len(raw)} bytes from 1 frames "
                         "(0 identical duplicates) -> explicit restored binary.bin\n")

    def test_v1_omitted_output_rejected(self):
        result = self.cli([oracle.frame(0, 1, oracle.SPECIAL, version=1)])
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("output_required: --output", result.stderr)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_invalid_filename_both_cli_output_modes(self):
        for explicit in (False, True):
            result = self.cli([single(b"test.mag")], explicit=explicit)
            self.assertEqual((result.returncode, result.stdout), (1, ""))
            self.assertIn("filename_invalid", result.stderr)
            self.assertEqual(list(self.destination.iterdir()), [])

    def test_automatic_output_collides_with_input_photo(self):
        photo = self.destination / "TEST.PNG"
        write_image(photo, qr_image(single(b"TEST.PNG")))
        before = photo.read_bytes()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "windows/reassemble_photos.py"),
                                 str(photo)], cwd=self.destination, capture_output=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout), (3, b""))
        self.assertIn(b"output_already_exists", result.stderr)
        self.assertEqual(photo.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
