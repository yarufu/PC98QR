# ホスト上の本番C画面からPNG/JPEG、受信CLIを経て、正確なバイナリ復元を検証する。
"""Host production C framebuffer -> PNG/JPEG -> receiver CLI -> exact binary."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

from test_reassemble_photos import fixture, split, write_image
from windows import decode_binary_photo as decoder

ROOT = Path(__file__).resolve().parents[1]


class ProductionPhotoReassemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)
        cls.directory = ROOT / "build/phase4c1-fixtures"
        cls.directory.mkdir(parents=True, exist_ok=True)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("PC98QR_TEST_")}
        for size in (6000, 2945):
            source = cls.directory / f"T{size}.BIN"
            source.write_bytes(fixture(size))
            for index, raw in enumerate(split(source.read_bytes())):
                result = subprocess.run([str(ROOT / "build/test_main_render"), "/T",
                                         str(index), str(source)], env=environment,
                                        capture_output=True, check=True)
                image = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
                if image is None or image.shape != (400, 640, 3):
                    raise AssertionError("invalid production framebuffer")
                for suffix in (".png", ".jpg"):
                    path = cls.directory / f"T{size}-{index}{suffix}"
                    write_image(path, image)
                    if decoder.decode_photo(path, report_errors=True) != raw:
                        raise AssertionError(f"raw frame mismatch: {path}")

    def restore(self, size, suffix, indices):
        photos = [self.directory / f"T{size}-{index}{suffix}" for index in indices]
        before = [photo.read_bytes() for photo in photos]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "restored.bin"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "windows/reassemble_photos.py"),
                                     "--output", str(output), *map(str, photos)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(output.stat().st_size, size)
            self.assertEqual(output.read_bytes(), fixture(size))
            self.assertEqual(output.read_bytes(), (self.directory / f"T{size}.BIN").read_bytes())
        self.assertEqual([photo.read_bytes() for photo in photos], before)

    def test_production_png_6000_arbitrary_duplicate(self):
        self.restore(6000, ".png", (2, 0, 1, 1))

    def test_production_jpeg_6000_reverse(self):
        self.restore(6000, ".jpg", (2, 1, 0))

    def test_production_png_2945_boundary(self):
        self.restore(2945, ".png", (1, 0))


if __name__ == "__main__":
    unittest.main()
