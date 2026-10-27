# 埋め込みQR、実Cの配置、写真1枚の復号器を検証する。
"""Validate the embedded symbol, real C placement and one-photo decoder."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np
import qrcode
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.util import MODE_ALPHA_NUM, QRData

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import generate_fixed_qr as generator
from qr_mask_oracle import selected_reference


def embedded_matrix():
    text = (ROOT / "src/qr_fixed.h").read_text(encoding="ascii")
    rows = re.findall(r"\{([01](?:, [01])*)\}", text)
    return np.array([[int(cell) for cell in row.split(", ")] for row in rows], dtype=np.uint8)


class FixedQRTests(unittest.TestCase):
    def test_generation_conditions(self):
        qr = generator.make_qr()
        self.assertEqual(qr.version, 1)
        self.assertEqual(qr.error_correction, ERROR_CORRECT_Q)
        self.assertEqual(len(qr.data_list), 1)
        self.assertEqual(qr.data_list[0].mode, MODE_ALPHA_NUM)
        self.assertEqual(qr.data_list[0].data, b"PC98-QR-TEST-001")

    def test_embedded_shape_and_version(self):
        matrix = embedded_matrix()
        self.assertEqual(matrix.shape, (21, 21))
        self.assertEqual((matrix.shape[0] - 17) // 4, 1)
        self.assertEqual(set(matrix.flat), {0, 1})

    def test_embedded_header_is_current(self):
        expected = generator.render_header(generator.make_qr().get_matrix())
        self.assertEqual((ROOT / "src/qr_fixed.h").read_text(encoding="ascii"), expected)

    def test_embedded_ecc_format_words(self):
        matrix = embedded_matrix()
        # 二つの形式情報がともにBCH保護済みのQ/mask-0値に一致することを確認する。
        # Both actual format copies must equal the BCH-protected Q/mask-0 word.
        vertical_rows = [0, 1, 2, 3, 4, 5, 7, 8, 14, 15, 16, 17, 18, 19, 20]
        horizontal_cols = [20, 19, 18, 17, 16, 15, 14, 13, 7, 5, 4, 3, 2, 1, 0]
        vertical = sum(int(matrix[row, 8]) << bit for bit, row in enumerate(vertical_rows))
        horizontal = sum(int(matrix[8, col]) << bit for bit, col in enumerate(horizontal_cols))
        self.assertEqual(vertical, 0x355F)  # Format bits 11 = ECC Q; mask bits 000. / 日本語: 形式ビット11はECC Q、マスクビットは000。
        self.assertEqual(horizontal, 0x355F)

    def test_embedded_alphanumeric_mode_and_length(self):
        matrix = embedded_matrix()
        # 最初の13データビットは右下の2列を上向きに配置する。
        # The first 13 data bits are in the bottom-right two columns (upwards).
        bits = [
            int(matrix[row, col]) ^ int((row + col) % 2 == 0)
            for row in range(20, 13, -1) for col in (20, 19)
        ]
        self.assertEqual(bits[:4], [0, 0, 1, 0])  # Alphanumeric mode indicator. / 日本語: 英数字モードの識別子。
        count = int("".join(str(bit) for bit in bits[4:13]), 2)
        self.assertEqual(count, 16)

    def test_embedded_decode_and_quiet_zone(self):
        image = generator.render_image(embedded_matrix())
        self.assertEqual(image.shape, (348, 348))
        for margin in (image[:48], image[-48:], image[:, :48], image[:, -48:]):
            self.assertTrue(np.all(margin == 255))
        decoded, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
        self.assertIsNotNone(points)
        self.assertEqual(decoded, "PC98-QR-TEST-001")

    @unittest.skipUnless(shutil.which("cc") and sys.platform != "win32", "requires WSL/POSIX cc")
    def test_real_c_main_placement_and_decode(self):
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        executable = build / "test_main_render"
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)
        output = subprocess.run([str(executable), "PC98-QR-TEST-001"],
                                check=True, capture_output=True).stdout
        frame = cv2.imdecode(np.frombuffer(output, np.uint8), cv2.IMREAD_GRAYSCALE)
        expected = np.full((400, 640), 255, dtype=np.uint8)
        matrix = np.asarray(selected_reference("PC98-QR-TEST-001").get_matrix(), dtype=np.uint8)
        body = (1 - matrix).repeat(12, axis=0).repeat(12, axis=1) * 255
        expected[74:326, 194:446] = body
        np.testing.assert_array_equal(frame, expected)
        np.testing.assert_array_equal(frame[26:374, 146:494], generator.render_image(matrix))
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(frame)[0], "PC98-QR-TEST-001")
        self.assertTrue(cv2.imwrite(str(build / "screen_preview.png"), frame))


class PhotoDecoderTests(unittest.TestCase):
    def run_decoder(self, path, *options):
        return subprocess.run([
            sys.executable, str(ROOT / "windows/decode_photo.py"), str(path), *options
        ], capture_output=True, text=True, encoding="utf-8")

    def save_image(self, path, image):
        success, encoded = cv2.imencode(path.suffix, image)
        self.assertTrue(success)
        path.write_bytes(encoded.tobytes())

    def test_png_jpeg_and_japanese_filename(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            for extension in (".png", ".jpg"):
                with self.subTest(extension=extension):
                    path = Path(directory) / ("QR 撮影確認" + extension)
                    self.save_image(path, generator.render_image(embedded_matrix()))
                    result = self.run_decoder(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "PC98-QR-TEST-001")

    def test_no_qr_and_bad_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            root = Path(directory)
            self.save_image(root / "blank.png", np.full((400, 640), 255, np.uint8))
            (root / "broken.png").write_bytes(b"not a picture")
            (root / "empty.png").write_bytes(b"")
            for name, code in (("blank.png", 1), ("broken.png", 2), ("empty.png", 2), ("missing.png", 2)):
                with self.subTest(name=name):
                    result = self.run_decoder(root / name)
                    self.assertEqual(result.returncode, code)
                    self.assertTrue(result.stderr)

    def test_unexpected_payload_is_failure(self):
        qr = qrcode.QRCode(version=1, error_correction=ERROR_CORRECT_Q, border=0)
        qr.add_data("WRONG-QR")
        qr.make(fit=False)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            path = Path(directory) / "wrong.png"
            self.save_image(path, generator.render_image(qr.get_matrix()))
            result = self.run_decoder(path)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout.strip(), "WRONG-QR")

    def test_explicit_expected_payload(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            path = Path(directory) / "dynamic.png"
            for text in ("HELLO", "PC98-QR-TEST-002", "HELLO WORLD", "$%*+-./:"):
                with self.subTest(text=text):
                    qr = qrcode.QRCode(version=1, error_correction=ERROR_CORRECT_Q,
                                       mask_pattern=0, border=0)
                    qr.add_data(QRData(text, mode=MODE_ALPHA_NUM), optimize=0)
                    qr.make(fit=False)
                    self.save_image(path, generator.render_image(qr.get_matrix()))
                    result = self.run_decoder(path, "--expect", text)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.rstrip("\r\n"), text)

    def test_explicit_expectation_mismatch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            path = Path(directory) / "fixed.png"
            self.save_image(path, generator.render_image(embedded_matrix()))
            result = self.run_decoder(path, "--expect", "HELLO")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout.strip(), "PC98-QR-TEST-001")
            self.assertIn("'HELLO'", result.stderr)

    def test_expect_requires_value(self):
        result = self.run_decoder("unused.png", "--expect")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--expect", result.stderr)

    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)


if __name__ == "__main__":
    unittest.main()
