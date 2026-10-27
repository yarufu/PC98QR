# ZXingを模擬した復号制御の試験。
# 実際の認識はtest_qr_bytesで扱う。
"""Decoder control tests with mocked ZXing; recognition lives in test_qr_bytes."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import zxingcpp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from windows import decode_binary_photo as decoder


class Barcode:
    def __init__(self, payload, valid=True):
        self.bytes = payload
        self.valid = valid

    @property
    def text(self):
        raise AssertionError("binary decoder must never access text")


class BinaryPhotoDecoderTests(unittest.TestCase):
    def setUp(self):
        self.image = np.arange(160 * 240 * 3, dtype=np.uint8).reshape(160, 240, 3)
        forbidden = patch.object(decoder.cv2, "QRCodeDetector",
                                 side_effect=AssertionError("OpenCV payload decode forbidden"))
        forbidden.start()
        self.addCleanup(forbidden.stop)

    def invoke(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = decoder.main(arguments)
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def run_main(self, *options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "撮影.png"
            ok, encoded = cv2.imencode(".png", self.image)
            self.assertTrue(ok)
            path.write_bytes(encoded.tobytes())
            return self.invoke([str(path), *options])

    def test_original_success_uses_bytes_and_avoids_resize(self):
        payload = bytes.fromhex("001A80FF")
        with patch.object(decoder.zxingcpp, "read_barcode", return_value=Barcode(payload)) as read, \
                patch.object(decoder.cv2, "resize") as resize:
            self.assertEqual(decoder.decode_image(self.image), (payload, 1.0))
            read.assert_called_once()
            self.assertIs(read.call_args.args[0], self.image)
            self.assertEqual(read.call_args.kwargs,
                             {"formats": zxingcpp.BarcodeFormat.QRCode, "try_downscale": True})
            resize.assert_not_called()

    def test_each_fallback_stops_at_first_success(self):
        shapes = [(160, 240), (120, 180), (80, 120), (60, 90), (40, 60)]
        for failures, scale in enumerate((0.75, 0.5, 0.375, 0.25), start=1):
            with self.subTest(scale=scale), patch.object(decoder.zxingcpp, "read_barcode") as read:
                read.side_effect = [None] * failures + [Barcode(b"\x00\xff")]
                self.assertEqual(decoder.decode_image(self.image), (b"\x00\xff", scale))
                self.assertEqual(read.call_count, failures + 1)
                self.assertEqual([call.args[0].shape[:2] for call in read.call_args_list],
                                 shapes[:failures + 1])

    def test_all_scales_in_order_resize_original_with_area(self):
        with patch.object(decoder.zxingcpp, "read_barcode", return_value=None) as read, \
                patch.object(decoder.cv2, "resize", wraps=cv2.resize) as resize:
            self.assertEqual(decoder.decode_image(self.image), (None, None))
            self.assertEqual(decoder.DECODE_SCALES, (1.0, 0.75, 0.5, 0.375, 0.25))
            self.assertEqual([call.args[0].shape[:2] for call in read.call_args_list],
                             [(160, 240), (120, 180), (80, 120), (60, 90), (40, 60)])
            self.assertIs(read.call_args_list[0].args[0], self.image)
            for call in read.call_args_list:
                self.assertEqual(call.kwargs,
                                 {"formats": zxingcpp.BarcodeFormat.QRCode, "try_downscale": True})
            self.assertEqual(resize.call_count, 4)
            for call in resize.call_args_list:
                self.assertIs(call.args[0], self.image)
                self.assertEqual(call.kwargs["interpolation"], cv2.INTER_AREA)

    def test_invalid_result_is_not_success(self):
        with patch.object(decoder.zxingcpp, "read_barcode") as read:
            read.side_effect = [Barcode(b"wrong", valid=False), Barcode(b"\x00")]
            self.assertEqual(decoder.decode_image(self.image), (b"\x00", 0.75))
            self.assertEqual(read.call_count, 2)

    def test_empty_decoded_bytes_are_distinct_from_no_result(self):
        with patch.object(decoder.zxingcpp, "read_barcode", return_value=Barcode(b"")) as read:
            self.assertEqual(self.run_main(), (0, "Length: 0\nHex: \n", ""))
            read.assert_called_once()

    def test_main_without_expectation_displays_lossless_hex(self):
        with patch.object(decoder.zxingcpp, "read_barcode",
                          return_value=Barcode(bytes.fromhex("00017F80FF"))):
            self.assertEqual(self.run_main(), (0, "Length: 5\nHex: 00017F80FF\n", ""))

    def test_expect_match_is_byte_equality_after_fallback(self):
        with patch.object(decoder.zxingcpp, "read_barcode") as read:
            read.side_effect = [None] * 3 + [Barcode(bytes.fromhex("004180FF1A"))]
            self.assertEqual(self.run_main("--expect-hex", "004180fF1a"),
                             (0, "Length: 5\nHex: 004180FF1A\n", ""))
            self.assertEqual(read.call_count, 4)

    def test_expect_mismatch_never_searches_later_scales(self):
        for failures in (0, 3):
            for payload in (b"\x00", b"\xc3\x28", b"A\x00B", b"\xff\x00"):
                with self.subTest(failures=failures, payload=payload), \
                        patch.object(decoder.zxingcpp, "read_barcode") as read:
                    read.side_effect = [None] * failures + [Barcode(payload), Barcode(b"\xff")]
                    self.assertEqual(self.run_main("--expect-hex", "FF"),
                                     (1, f"Length: {len(payload)}\nHex: {payload.hex().upper()}\n",
                                      "Expected exactly FF.\n"))
                    self.assertEqual(read.call_count, failures + 1)

    def test_no_decodable_qr_exit_one(self):
        with patch.object(decoder.zxingcpp, "read_barcode", return_value=None) as read:
            self.assertEqual(self.run_main(), (1, "", "No decodable QR code found.\n"))
            self.assertEqual(read.call_count, 5)

    def test_invalid_expect_hex_exit_two_before_image_read(self):
        for value in ("", "0", "000", "GG", "0xFF", "00 FF", "FF\n", "\uff26\uff26"):
            with self.subTest(value=value), patch.object(decoder, "decode_photo") as decode:
                code, stdout, stderr = self.invoke(["unused.jpg", "--expect-hex", value])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("expected hex", stderr)
                decode.assert_not_called()

    def test_expect_requires_value_exit_two(self):
        code, stdout, stderr = self.invoke(["unused.jpg", "--expect-hex"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--expect-hex", stderr)

    def test_image_errors_exit_two_without_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.jpg").write_bytes(b"")
            (root / "broken.jpg").write_bytes(b"not an image")
            for name in ("missing.jpg", "empty.jpg", "broken.jpg"):
                with self.subTest(name=name), patch.object(decoder.zxingcpp, "read_barcode") as read:
                    code, stdout, stderr = self.invoke([str(root / name)])
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout, "")
                    self.assertTrue(stderr.startswith("Image error:"))
                    read.assert_not_called()

    def test_opencv_image_error_exit_two(self):
        with patch.object(decoder.cv2, "imdecode", side_effect=cv2.error("bad image")):
            code, stdout, stderr = self.run_main()
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Image error:", stderr)

    def test_tiny_image_resize_stays_nonempty(self):
        with patch.object(decoder.zxingcpp, "read_barcode", return_value=None) as read:
            self.assertEqual(decoder.decode_image(np.full((1, 1, 3), 255, np.uint8)), (None, None))
            self.assertEqual(read.call_count, 5)
            self.assertTrue(all(call.args[0].shape == (1, 1, 3) for call in read.call_args_list))


if __name__ == "__main__":
    unittest.main()
