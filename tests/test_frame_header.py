# Phase 4A-1の独立した転送バイト列基準と、公開ヘッダーAPI契約だけを検証する。
"""Phase 4A-1: independent wire oracle and public header API contracts only."""
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
OK, ARGUMENT, LENGTH, MAGIC, VERSION, COUNT, INDEX = range(7)
VECTORS = (
    (0, 1, "503938510100000001"),
    (0, 501, "5039385101000001f5"),
    (500, 501, "503938510101f401f5"),
    (254, 255, "503938510100fe00ff"),
    (255, 256, "503938510100ff0100"),
    (256, 501, "5039385101010001f5"),
    (65534, 65535, "5039385101fffeffff"),
)
SPECIAL = bytes.fromhex("001a80ff0d0a") + b"P98Q"


def header(index, count):
    return b"P98Q" + bytes([1]) + index.to_bytes(2, "big") + count.to_bytes(2, "big")


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_FRAME_HEADER_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build/test_frame_header"
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-o", str(target),
                    str(ROOT / "tests/frame_header_main.c"),
                    str(ROOT / "src/frame_header.c")], check=True, cwd=ROOT)
    return target


def call(*args):
    result = subprocess.run([str(driver()), *map(str, args)],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode or result.stderr:
        raise AssertionError(f"header driver: exit {result.returncode}\n{result.stderr}")
    return json.loads(result.stdout)


class FrameHeaderTests(unittest.TestCase):
    def assert_parse(self, raw, index, count):
        self.assertEqual(call("parse", raw.hex()),
                         {"result": OK, "index": index, "count": count})

    def assert_parse_error(self, raw, error, null_mask=0):
        self.assertEqual(call("parse", raw.hex(), null_mask),
                         {"result": error, "index": 0xA55A, "count": 0x5AA5})

    def assert_serialize_error(self, index, count, error, null_out=False):
        args = ("null",) if null_out else ()
        self.assertEqual(call("serialize", index, count, *args),
                         {"result": error, "bytes": "a5" * 9})

    def test_constants_and_host_integer_widths(self):
        self.assertEqual(call("constants"), {"size": 9, "version": 1,
                         "uchar_max": 255, "ushort_max": 65535,
                         "char_bit": 8, "ushort_size": 2})

    def test_serialize_exact_vectors(self):
        for index, count, expected in VECTORS:
            with self.subTest(index=index, count=count):
                self.assertEqual(header(index, count), bytes.fromhex(expected))
                self.assertEqual(call("serialize", index, count),
                                 {"result": OK, "bytes": expected})

    def test_parse_exact_vectors(self):
        for index, count, expected in VECTORS:
            with self.subTest(index=index, count=count):
                self.assert_parse(bytes.fromhex(expected), index, count)

    def test_parse_vectors_with_binary_payload(self):
        for index, count, _ in VECTORS:
            with self.subTest(index=index, count=count):
                self.assert_parse(header(index, count) + SPECIAL, index, count)

    def test_parse_all_payload_byte_values(self):
        self.assert_parse(header(0, 1) + bytes(range(256)), 0, 1)

    def test_no_full_frame_length_validation(self):
        for index in (0, 500):
            for payload_length in (0, 1, 2943, 2944, 2945, 4096):
                with self.subTest(index=index, payload_length=payload_length):
                    self.assert_parse(header(index, 501) + b"\xff" * payload_length,
                                      index, 501)

    def test_no_read_beyond_header_even_with_uint_max_length(self):
        self.assertTrue(call("read-bound"))

    def test_independent_oracle_across_unsigned_16_bit_range(self):
        rng = random.Random(0x4A1)
        pairs = [(0, 502), (32767, 32768), (32768, 32769), (0, 65535)]
        for _ in range(64):
            count = rng.randrange(1, 65536)
            pairs.append((rng.randrange(count), count))
        for index, count in pairs:
            with self.subTest(index=index, count=count):
                expected = header(index, count)
                self.assertEqual(call("serialize", index, count),
                                 {"result": OK, "bytes": expected.hex()})
                self.assert_parse(expected, index, count)

    def test_serialize_count_zero_output_unchanged(self):
        for index in (0, 1, 65535):
            self.assert_serialize_error(index, 0, COUNT)

    def test_serialize_index_equal_count_output_unchanged(self):
        for count in (1, 255, 256, 501, 65535):
            self.assert_serialize_error(count, count, INDEX)

    def test_serialize_index_above_count_output_unchanged(self):
        for index, count in ((1, 0), (2, 1), (256, 255), (65535, 501)):
            self.assert_serialize_error(index, count, COUNT if count == 0 else INDEX)

    def test_serialize_null_output_valid_fields(self):
        for index, count, _ in VECTORS:
            self.assert_serialize_error(index, count, ARGUMENT, null_out=True)

    def test_serialize_count_error_before_null_output(self):
        for index in (0, 65535):
            self.assert_serialize_error(index, 0, COUNT, null_out=True)

    def test_serialize_index_error_before_null_output(self):
        for index, count in ((1, 1), (65535, 501)):
            self.assert_serialize_error(index, count, INDEX, null_out=True)

    def test_parse_lengths_zero_through_eight_outputs_unchanged(self):
        for length in range(9):
            with self.subTest(length=length):
                self.assert_parse_error(header(0, 1)[:length], LENGTH)

    def test_parse_short_length_before_all_null_combinations(self):
        for length in range(9):
            for null_mask in range(1, 8):
                with self.subTest(length=length, null_mask=null_mask):
                    self.assert_parse_error(header(0, 1)[:length], LENGTH, null_mask)

    def test_parse_null_arguments_at_valid_lengths(self):
        for payload in (b"", SPECIAL):
            for null_mask in range(1, 8):
                with self.subTest(length=9 + len(payload), null_mask=null_mask):
                    self.assert_parse_error(header(0, 1) + payload, ARGUMENT, null_mask)

    def test_parse_each_magic_byte_outputs_unchanged(self):
        for offset in range(4):
            with self.subTest(offset=offset):
                raw = bytearray(header(0, 1))
                raw[offset] ^= 0xFF
                self.assert_parse_error(raw, MAGIC)

    def test_parse_unsupported_versions_outputs_unchanged(self):
        for version in (0, 2, 255):
            with self.subTest(version=version):
                raw = bytearray(header(0, 1))
                raw[4] = version
                self.assert_parse_error(raw, VERSION)

    def test_parse_count_zero_outputs_unchanged(self):
        for index in (0, 1, 65535):
            self.assert_parse_error(header(index, 0), COUNT)

    def test_parse_index_equal_count_outputs_unchanged(self):
        for count in (1, 255, 256, 501, 65535):
            self.assert_parse_error(header(count, count), INDEX)

    def test_parse_index_above_count_outputs_unchanged(self):
        for index, count in ((2, 1), (256, 255), (65535, 501)):
            self.assert_parse_error(header(index, count), INDEX)

    def test_parse_validation_precedence(self):
        raw = bytearray(header(65535, 0))
        raw[0] = 0
        raw[4] = 255
        self.assert_parse_error(raw[:8], LENGTH)
        for null_mask in range(1, 8):
            self.assert_parse_error(raw, ARGUMENT, null_mask)
        self.assert_parse_error(raw, MAGIC)
        raw[0] = 0x50
        self.assert_parse_error(raw, VERSION)
        raw[4] = 1
        self.assert_parse_error(raw, COUNT)


if __name__ == "__main__":
    unittest.main()
