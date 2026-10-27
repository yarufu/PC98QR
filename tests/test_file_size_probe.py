# 独立したDOS probeの実ソースと出力を対象にした、ホストの基本動作試験。
"""Host smoke tests of the exact standalone DOS probe source and output."""
from functools import lru_cache
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def probe():
    override = os.environ.get("PC98QR_FILE_SIZE_PROBE")
    if override:
        return Path(override)
    target = ROOT / "build/test_file_size_probe"
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "src"),
                    "-o", str(target), str(ROOT / "tools/file_size_probe.c"),
                    str(ROOT / "src/file_size.c")], check=True, cwd=ROOT)
    return target


class FileSizeProbeTests(unittest.TestCase):
    def check_output(self, args, expected, status):
        result = subprocess.run([str(probe()), *map(str, args)], capture_output=True)
        self.assertEqual(result.returncode, status)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, b"")

    def test_size_decimal_and_hex(self):
        vectors = ((0, "00000000"), (1, "00000001"), (65535, "0000FFFF"),
                   (65536, "00010000"), (65537, "00010001"), (1474560, "00168000"))
        with tempfile.TemporaryDirectory() as directory:
            for size, hex_size in vectors:
                with self.subTest(size=size):
                    path = Path(directory) / f"S{size}.BIN"
                    with path.open("wb") as stream:
                        stream.truncate(size)
                    expected = f"SIZE={size}\r\nHEX={hex_size}\r\n".encode("ascii")
                    self.check_output([path], expected, 0)
                    self.assertEqual(path.stat().st_size, size)

    def test_argument_error(self):
        for args in ([], ["one", "two"], [""]):
            with self.subTest(args=args):
                self.check_output(args, b"ERROR=1\r\n", 1)

    def test_open_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.check_output([Path(directory) / "MISSING.BIN"], b"ERROR=2\r\n", 1)


if __name__ == "__main__":
    unittest.main()
