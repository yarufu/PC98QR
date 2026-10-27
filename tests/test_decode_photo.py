# 決定的な縮小再試行テストと、実画像・CLIの回帰試験。
"""Deterministic resize fallback tests, plus real image/CLI regressions."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import generate_fixed_qr as generator
from windows import decode_photo as decoder


class MultiScaleDecoderTests(unittest.TestCase):
    def setUp(self):
        self.image = np.arange(160 * 240 * 3, dtype=np.uint8).reshape(160, 240, 3)

    def run_main(self, *options):
        # 実ファイル読み取りと、従来の引数・終了コード処理を検証する。
        # Exercise reading a real file and the unchanged argument/exit handling.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "撮影.png"
            success, encoded = cv2.imencode(".png", self.image)
            self.assertTrue(success)
            path.write_bytes(encoded.tobytes())
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = decoder.main([str(path), *options])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_original_success_avoids_resize(self):
        with patch.object(decoder.cv2, "QRCodeDetector") as factory, \
                patch.object(decoder.cv2, "resize") as resize:
            detect = factory.return_value.detectAndDecode
            detect.return_value = ("PC98-QR-TEST-002", None, None)
            self.assertEqual(decoder.decode_image(self.image), ("PC98-QR-TEST-002", 1.0))
            detect.assert_called_once()
            self.assertIs(detect.call_args.args[0], self.image)
            resize.assert_not_called()

    def test_real_original_qr_avoids_resize(self):
        image = generator.render_image(generator.make_qr().get_matrix())
        with patch.object(decoder.cv2, "resize") as resize:
            self.assertEqual(decoder.decode_image(image), ("PC98-QR-TEST-001", 1.0))
            resize.assert_not_called()

    def test_fallback_stops_at_each_first_success(self):
        expected_shapes = [(160, 240), (120, 180), (80, 120), (60, 90), (40, 60)]
        for failures, scale in enumerate((0.75, 0.5, 0.375, 0.25), start=1):
            with self.subTest(scale=scale), \
                    patch.object(decoder.cv2, "QRCodeDetector") as factory:
                detect = factory.return_value.detectAndDecode
                detect.side_effect = [("", None, None)] * failures + [
                    ("PC98-QR-TEST-002", None, None)
                ]
                self.assertEqual(decoder.decode_image(self.image), ("PC98-QR-TEST-002", scale))
                self.assertEqual(detect.call_count, failures + 1)
                self.assertIs(detect.call_args_list[0].args[0], self.image)
                self.assertEqual([call.args[0].shape[:2] for call in detect.call_args_list],
                                 expected_shapes[:failures + 1])

    def test_all_scales_fail_resize_original_with_area_in_order(self):
        with patch.object(decoder.cv2, "QRCodeDetector") as factory, \
                patch.object(decoder.cv2, "resize", wraps=cv2.resize) as resize:
            detect = factory.return_value.detectAndDecode
            # 角の検出だけではペイロードの復号成功とは扱わない。
            # Detected corners alone are not a successful payload decode.
            detect.return_value = ("", np.zeros((1, 4, 2)), None)
            self.assertEqual(decoder.decode_image(self.image), ("", None))
            self.assertEqual([call.args[0].shape[:2] for call in detect.call_args_list],
                             [(160, 240), (120, 180), (80, 120), (60, 90), (40, 60)])
            self.assertIs(detect.call_args_list[0].args[0], self.image)
            self.assertEqual(resize.call_count, 4)
            for call in resize.call_args_list:
                self.assertIs(call.args[0], self.image)
                self.assertEqual(call.kwargs["interpolation"], cv2.INTER_AREA)

    def test_main_fallback_expected_match(self):
        with patch.object(decoder.cv2, "QRCodeDetector") as factory:
            detect = factory.return_value.detectAndDecode
            detect.side_effect = [("", None, None)] * 3 + [("PC98-QR-TEST-002", None, None)]
            self.assertEqual(self.run_main("--expect", "PC98-QR-TEST-002"),
                             (0, "PC98-QR-TEST-002\n", ""))
            self.assertEqual(detect.call_count, 4)

    def test_main_mismatch_does_not_search_for_expected(self):
        for failures in (0, 3):
            for payload in ("ABC", "XYZ ", "xyz"):
                with self.subTest(failures=failures, payload=payload), \
                        patch.object(decoder.cv2, "QRCodeDetector") as factory:
                    detect = factory.return_value.detectAndDecode
                    detect.side_effect = [("", None, None)] * failures + [
                        (payload, None, None), ("XYZ", None, None)
                    ]
                    code, stdout, stderr = self.run_main("--expect", "XYZ")
                    self.assertEqual(code, 1)
                    self.assertEqual(stdout, payload + "\n")
                    self.assertEqual(stderr, "Expected exactly 'XYZ'.\n")
                    self.assertEqual(detect.call_count, failures + 1)

    def test_main_all_scales_fail(self):
        with patch.object(decoder.cv2, "QRCodeDetector") as factory:
            detect = factory.return_value.detectAndDecode
            detect.return_value = ("", None, None)
            self.assertEqual(self.run_main(), (1, "", "No decodable QR code found.\n"))
            self.assertEqual(detect.call_count, 5)

    def test_tiny_blank_image_is_decode_failure_not_image_error(self):
        self.image = np.full((1, 1, 3), 255, dtype=np.uint8)
        self.assertEqual(self.run_main(), (1, "", "No decodable QR code found.\n"))


if __name__ == "__main__":
    unittest.main()
