# Phase 4B-2cの指定オフセット、生バイト、システムコール、エラー契約の試験。
"""Phase 4B-2c: one supplied offset, raw bytes, syscall and error contracts."""
from functools import lru_cache
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OK, ARGUMENT, OFFSET, OPEN, SEEK, READ, CLOSE, NO_DATA = range(8)
SENTINEL = 0xA55A
FAIL_OPEN, FAIL_SEEK, FAIL_READ, FAIL_CLOSE, WRONG_POSITION, PARTIAL = (1, 2, 4, 8, 16, 32)


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_FILE_CHUNK_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build/test_file_chunk"
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-o", str(target),
                    str(ROOT / "tests/file_chunk_main.c")], check=True, cwd=ROOT)
    return target


def run_driver(*args, cwd=ROOT):
    result = subprocess.run([str(driver()), *map(str, args)],
                            capture_output=True, text=True, cwd=cwd)
    if result.returncode or result.stderr:
        raise AssertionError(f"file chunk driver: exit {result.returncode}\n{result.stderr}")
    return json.loads(result.stdout)


def call(path, offset=0, capacity=2944, *, faults=0, nulls=0, plan=0, cwd=ROOT):
    return run_driver(path, offset, capacity, faults, nulls, plan, cwd=cwd)


def pattern(offset, length):
    # 上位のオフセットビットも含め、16ビットへ切り捨てたseekでは基準値に一致しないようにする。
    # Include upper offset bits: a 16-bit-truncated seek cannot match this oracle.
    return bytes((i * 37 + (i >> 8) * 11 + (i >> 16) * 17 + 3) & 255
                 for i in range(offset, offset + length))


class FileChunkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "CHUNK.BIN"

    def fixture(self, size):
        # ホスト側で範囲を制限した確保を行う。
        # 本番ヘルパーはこの試験データを生成しない。
        # Bounded host allocation; runtime helper never creates these fixtures.
        with self.path.open("wb") as stream:
            for start in range(0, size, 4096):
                stream.write(pattern(start, min(4096, size - start)))
        return self.path

    def assert_error(self, actual, status, *, calls=(1, 1, 1, 1), total=0):
        self.assertEqual(actual["result"], status)
        self.assertEqual(actual["length"], SENTINEL)
        self.assertEqual(tuple(actual[k] for k in ("open", "seek", "read", "close")), calls)
        self.assertEqual(actual["total"], total)
        self.assertEqual(actual["hex"], "")

    def check_data(self, size, offset=0, capacity=2944, *, plan=0):
        path = self.fixture(size)
        before = path.stat()
        expected = pattern(offset, min(capacity, size - offset))
        # 独立した通常ファイルのseek/readが固定パターンと一致することを確認する。
        # Independent ordinary-file seek/read agrees with the fixed pattern.
        with path.open("rb") as stream:
            stream.seek(offset)
            self.assertEqual(stream.read(capacity), expected)
        actual = call(path, offset, capacity, plan=plan)
        self.assertEqual(actual["result"], OK)
        self.assertEqual(actual["length"], len(expected))
        self.assertEqual(actual["total"], len(expected))
        self.assertEqual(bytes.fromhex(actual["hex"]), expected)
        self.assertEqual((actual["open"], actual["seek"], actual["close"]), (1, 1, 1))
        if plan == 0:
            self.assertEqual(actual["read"], 1 if len(expected) == capacity else 2)
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        return actual

    def test_constants_and_host_types(self):
        actual = run_driver("constants")
        self.assertEqual(actual["payload_max"], 2944)
        self.assertEqual(actual["off_signed"], 1)
        self.assertGreaterEqual(actual["off_bits"], 32)
        self.assertEqual(actual["long_max"], (1 << (actual["off_bits"] - 1)) - 1)
        self.assertGreaterEqual(actual["ulong_max"], 0xFFFFFFFF)
        self.assertGreaterEqual(actual["uint_max"], 2944)

    def test_one_byte(self):
        self.check_data(1)

    def test_exact_capacity_file(self):
        self.check_data(2944)

    def test_larger_file_is_not_oversize(self):
        self.check_data(2945)

    def test_6000_offset_0(self):
        self.check_data(6000, 0)

    def test_6000_offset_2943(self):
        self.check_data(6000, 2943)

    def test_6000_offset_2944(self):
        self.check_data(6000, 2944)

    def test_6000_offset_5888(self):
        actual = self.check_data(6000, 5888)
        self.assertEqual(actual["length"], 112)

    def test_offset_65535_crosses_boundary(self):
        actual = self.check_data(70000, 65535)
        self.assertTrue(actual["hex"].startswith("d314395e"))

    def test_offset_65536(self):
        actual = self.check_data(70000, 65536)
        self.assertTrue(actual["hex"].startswith("14395e83"))
        self.assertNotEqual(bytes.fromhex(actual["hex"]), pattern(0, 2944))

    def test_offset_65537(self):
        actual = self.check_data(70000, 65537)
        self.assertTrue(actual["hex"].startswith("395e83a8"))
        self.assertNotEqual(bytes.fromhex(actual["hex"]), pattern(1, 2944))

    def test_offset_1472000_final_2560(self):
        actual = self.check_data(1474560, 1472000)
        self.assertEqual(actual["length"], 2560)
        self.assertNotEqual(bytes.fromhex(actual["hex"]), pattern(1472000 & 65535, 2560))

    def test_empty_no_data(self):
        self.assert_error(call(self.fixture(0)), NO_DATA)

    def test_one_byte_exact_eof(self):
        self.assert_error(call(self.fixture(1), 1), NO_DATA)

    def test_6000_exact_eof(self):
        self.assert_error(call(self.fixture(6000), 6000), NO_DATA)

    def test_6000_beyond_eof(self):
        self.assert_error(call(self.fixture(6000), 6001), NO_DATA)

    def test_capacity_boundaries(self):
        for capacity in (1, 16, 2943, 2944):
            with self.subTest(capacity=capacity):
                actual = self.check_data(6000, 2943, capacity)
                self.assertEqual(actual["requests"], [capacity])
                self.assertEqual(actual["counts"], [capacity])

    def test_invalid_capacity_before_io(self):
        for capacity in (0, 2945, 65535):
            with self.subTest(capacity=capacity):
                self.assert_error(call("unused", capacity=capacity), ARGUMENT, calls=(0, 0, 0, 0))

    def test_null_arguments_before_io(self):
        for nulls in range(1, 8):
            with self.subTest(nulls=nulls):
                self.assert_error(call("unused", nulls=nulls), ARGUMENT, calls=(0, 0, 0, 0))

    def test_empty_path_before_io(self):
        self.assert_error(call(""), ARGUMENT, calls=(0, 0, 0, 0))

    def test_argument_precedes_offset(self):
        offset = run_driver("constants")["ulong_max"]
        for nulls in range(1, 8):
            with self.subTest(nulls=nulls):
                self.assert_error(call("unused", offset, nulls=nulls), ARGUMENT, calls=(0, 0, 0, 0))
        for capacity in (0, 2945):
            self.assert_error(call("unused", offset, capacity), ARGUMENT, calls=(0, 0, 0, 0))
        self.assert_error(call("", offset), ARGUMENT, calls=(0, 0, 0, 0))

    def test_unrepresentable_offsets_before_io(self):
        limits = run_driver("constants")
        for offset in (limits["long_max"] + 1, limits["ulong_max"] - 1, limits["ulong_max"]):
            with self.subTest(offset=offset):
                self.assert_error(call("unused", offset), OFFSET, calls=(0, 0, 0, 0))

    def test_representable_limits_synthetic_seek(self):
        limits = run_driver("constants")
        path = self.fixture(1)
        for offset in (65536, 1472000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF,
                       limits["long_max"] - 1, limits["long_max"]):
            with self.subTest(offset=offset):
                # LP64ホストはia16のLONG_MAXを超える値を受け付けるため、ABI境界として扱う。
                # 人工的なseekによる境界試験であり、大容量ファイルの実I/O試験ではない。
                # LP64 host accepts values beyond ia16 LONG_MAX. This is an ABI
                # boundary test using a synthetic seek, not large-file I/O.
                if offset <= limits["long_max"]:
                    self.assert_error(call(path, offset, plan=2), NO_DATA)
                else:
                    self.assert_error(call(path, offset, plan=2), OFFSET, calls=(0, 0, 0, 0))

    def test_positive_short_reads_fill_capacity(self):
        actual = self.check_data(6000, 2944, plan=1)
        self.assertEqual(actual["counts"], [5, 6, 1000, 1000, 933])
        self.assertEqual(actual["requests"], [2944, 2939, 2933, 1933, 933])

    def test_positive_short_reads_then_eof(self):
        actual = self.check_data(6000, 5888, plan=1)
        self.assertEqual(actual["counts"], [5, 6, 101, 0])
        self.assertEqual(actual["requests"], [2944, 2939, 2933, 2832])

    def test_raw_binary_bytes(self):
        for payload in (b"\x00\x1a\x80\xff\r\n\x1a", bytes(range(256)), b"\x00", b"\x1a"):
            with self.subTest(payload=payload.hex()):
                self.path.write_bytes(payload)
                actual = call(self.path)
                self.assertEqual(actual["result"], OK)
                self.assertEqual(actual["length"], len(payload))
                self.assertEqual(bytes.fromhex(actual["hex"]), payload)
                self.assertEqual(self.path.read_bytes(), payload)

    def test_relative_path_with_spaces(self):
        path = Path(self.directory.name) / "Sub Dir" / "Mixed.bin"
        path.parent.mkdir()
        path.write_bytes(b"A\x00\x1a\xff")
        actual = call("Sub Dir/Mixed.bin", 1, cwd=self.directory.name)
        self.assertEqual((actual["result"], actual["length"], actual["hex"]), (OK, 3, "001aff"))

    def test_missing_file(self):
        self.assert_error(call(self.path), OPEN, calls=(1, 0, 0, 0))

    def test_open_fault(self):
        self.assert_error(call(self.fixture(6000), faults=FAIL_OPEN), OPEN, calls=(1, 0, 0, 0))

    def test_seek_fault_closes(self):
        self.assert_error(call(self.fixture(6000), faults=FAIL_SEEK), SEEK, calls=(1, 1, 0, 1))

    def test_wrong_seek_position(self):
        for offset in (0, 65536):
            with self.subTest(offset=offset):
                self.assert_error(call(self.fixture(70000), offset, faults=WRONG_POSITION),
                                  SEEK, calls=(1, 1, 0, 1))

    def test_read_immediate_fault(self):
        self.assert_error(call(self.fixture(6000), faults=FAIL_READ), READ)

    def test_read_partial_fault(self):
        actual = call(self.fixture(6000), faults=PARTIAL)
        self.assert_error(actual, READ, calls=(1, 1, 2, 1), total=5)
        self.assertEqual(actual["counts"], [5, -1])

    def test_close_fault_after_data(self):
        for size, reads in ((1, 2), (6000, 1)):
            with self.subTest(size=size):
                self.assert_error(call(self.fixture(size), faults=FAIL_CLOSE), CLOSE,
                                  calls=(1, 1, reads, 1), total=min(2944, size))

    def test_seek_error_precedes_close(self):
        for fault in (FAIL_SEEK, WRONG_POSITION):
            self.assert_error(call(self.fixture(6000), faults=fault | FAIL_CLOSE), SEEK,
                              calls=(1, 1, 0, 1))

    def test_read_error_precedes_close(self):
        self.assert_error(call(self.fixture(6000), faults=FAIL_READ | FAIL_CLOSE), READ)
        self.assert_error(call(self.path, faults=PARTIAL | FAIL_CLOSE), READ,
                          calls=(1, 1, 2, 1), total=5)

    def test_no_data_precedes_close(self):
        self.assert_error(call(self.fixture(0), faults=FAIL_CLOSE), NO_DATA)
        self.assert_error(call(self.fixture(6000), 6000, faults=FAIL_CLOSE), NO_DATA)


if __name__ == "__main__":
    unittest.main()
