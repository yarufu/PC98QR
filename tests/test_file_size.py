# Phase 4B-2aのサイズ取得だけの試験。
# 転送・フレーム・chunkの計算は含まない。
"""Phase 4B-2a: file size only; no transport, frame or chunk arithmetic."""
from functools import lru_cache
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OK, ARGUMENT, OPEN, SEEK, CLOSE = range(5)
SENTINEL = 0xA55A5AA5


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_FILE_SIZE_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build/test_file_size"
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-o", str(target),
                    str(ROOT / "tests/file_size_main.c")], check=True, cwd=ROOT)
    return target


def call(path, *, faults=0, nulls=0, cwd=ROOT):
    result = subprocess.run([str(driver()), str(path), str(faults), str(nulls)],
                            capture_output=True, text=True, cwd=cwd)
    if result.returncode or result.stderr:
        raise AssertionError(f"file size driver: exit {result.returncode}\n{result.stderr}")
    return json.loads(result.stdout)


class FileSizeTests(unittest.TestCase):
    def assert_result(self, actual, status, size=SENTINEL, calls=(1, 1, 1)):
        self.assertEqual(actual, dict(result=status, size=size,
                                     **dict(zip(("open", "seek", "close"), calls))))

    def check_size(self, length):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SIZE.BIN"
            # 対応環境ではスパースファイルを使い、サイズに比例してメモリを増やさない。
            # Sparse where supported; memory use does not grow with file size.
            with path.open("wb") as stream:
                stream.truncate(length)
            before = path.stat()
            actual = call(path)
            self.assert_result(actual, OK, length)
            if length >= 65536:
                self.assertNotEqual(actual["size"], length & 0xFFFF)
            after = path.stat()
            self.assertEqual(after.st_size, length)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_size_0(self):
        self.check_size(0)

    def test_size_1(self):
        self.check_size(1)

    def test_size_2(self):
        self.check_size(2)

    def test_size_2943(self):
        self.check_size(2943)

    def test_size_2944(self):
        self.check_size(2944)

    def test_size_2945(self):
        self.check_size(2945)

    def test_size_6000(self):
        self.check_size(6000)

    def test_size_65535(self):
        self.check_size(65535)

    def test_size_65536(self):
        self.check_size(65536)

    def test_size_65537(self):
        self.check_size(65537)

    def test_size_1474560(self):
        self.check_size(1474560)

    def test_size_1474944(self):
        self.check_size(1474944)

    def test_size_1474945(self):
        self.check_size(1474945)

    def test_binary_content_does_not_change_size(self):
        vectors = [bytes([value]) for value in (0x00, 0x1A, 0x80, 0xFF, 0x0D, 0x0A)]
        vectors += [b"A\x00\x1a\x80\xff\r\n\x1a", bytes(range(256))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BINARY.BIN"
            for payload in vectors:
                with self.subTest(payload=payload.hex()):
                    path.write_bytes(payload)
                    self.assert_result(call(path), OK, len(payload))
                    self.assertEqual(path.read_bytes(), payload)

    def test_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Sub Dir" / "Mixed.bin"
            path.parent.mkdir()
            path.write_bytes(b"\x1a\x00")
            self.assert_result(call("Sub Dir/Mixed.bin", cwd=directory), OK, 2)

    def test_null_path(self):
        self.assert_result(call("unused", nulls=1), ARGUMENT, calls=(0, 0, 0))

    def test_empty_path(self):
        self.assert_result(call(""), ARGUMENT, calls=(0, 0, 0))

    def test_null_output(self):
        self.assert_result(call("unused", nulls=2), ARGUMENT, calls=(0, 0, 0))

    def test_both_null(self):
        self.assert_result(call("unused", nulls=3), ARGUMENT, calls=(0, 0, 0))

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assert_result(call(Path(directory) / "MISSING.BIN"), OPEN,
                               calls=(1, 0, 0))

    def check_fault(self, faults, status, *, length=65536, calls=(1, 1, 1)):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "FAULT.BIN"
            with path.open("wb") as stream:
                stream.truncate(length)
            self.assert_result(call(path, faults=faults), status, calls=calls)
            self.assertEqual(path.stat().st_size, length)

    def test_open_failure(self):
        self.check_fault(1, OPEN, calls=(1, 0, 0))

    def test_seek_failure_still_closes(self):
        self.check_fault(2, SEEK)

    def test_close_failure_does_not_publish_size(self):
        self.check_fault(4, CLOSE)

    def test_seek_error_survives_close_error(self):
        self.check_fault(6, SEEK)

    def test_empty_close_failure(self):
        self.check_fault(4, CLOSE, length=0)


if __name__ == "__main__":
    unittest.main()
