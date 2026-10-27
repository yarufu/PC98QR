# 独立したv2転送列基準と、別コンパイルした実Cヘルパーの試験。
"""Independent v2 wire oracle and actual separately compiled C helper tests."""
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1l"
OK, ARGUMENT, FILENAME, SIZE, INDEX, LENGTH, CAPACITY = range(7)
SPECIAL = bytes.fromhex("00011a7f80ffc328000d0a414200ff1a")
SIZES = ((1, 1), (100, 1), (2900, 1), (2935, 1), (2936, 2), (2944, 2),
         (2945, 2), (6000, 3), (29198, 10), (70000, 24), (1474560, 501))


def build_driver(target, extra=()):
    command = [*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
               "-Wall", "-Wextra", "-Werror", *extra, "tests/frame_v2_main.c",
               "src/filename_v2.c", "src/frame_v2.c", "src/frame_header.c",
               "src/frame_count.c", "src/frame_offset.c", "-o", str(target)]
    subprocess.run(command, cwd=ROOT, check=True)
    return command


@lru_cache(maxsize=1)
def driver():
    if override := os.environ.get("PC98QR_FRAME_V2_DRIVER"):
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_frame_v2"
    build_driver(target)
    return target


def call(*args):
    result = subprocess.run([str(driver()), *map(str, args)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    if result.returncode or result.stderr:
        raise AssertionError(f"driver {args[0]} exit {result.returncode}: {result.stderr}")
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    return rows[0] if len(rows) == 1 else rows


@lru_cache(maxsize=1)
def constants():
    return call("constants")


def filename(n):
    base = min(8, n - 2)
    return b"A" * n if n <= 8 else b"A" * base + b"." + b"X" * (n - base - 1)


def fixture(size):
    return bytes((37*i + 11*(i >> 8) + 19*(i >> 16) + 3) & 255 for i in range(size))


def count(size, n=8, null=0):
    return call("count", size, n, null)


def layout(size, index, n=8, null=0):
    return call("layout", size, n, index, null)


def oracle_layout(size, index, n):
    # 任意精度整数と容量の累積を使い、Cとは独立に期待値を作る。
    # Cの先頭容量を引く総数計算やindex×2944−Mの位置計算をそのまま写さない。
    # Arbitrary precision and accumulated capacities, independent of C's
    # subtract-first count and index*2944-M offset implementation.
    frames = (size + 1 + n + 2943) // 2944
    capacities = (2943 - n, 2944)
    offset = 0 if index == 0 else capacities[0] + (index - 1)*capacities[1]
    capacity = capacities[index != 0]
    actual = min(capacity, size - offset)
    start = 10 + n if index == 0 else 9
    return dict(result=OK, count=frames, offset=offset, capacity=capacity,
                raw=actual, start=start, complete=start+actual)


def wire(name, size, index, chunk):
    frames = (size + len(name) + 2944) // 2944
    header = b"P98Q\x02" + index.to_bytes(2, "big") + frames.to_bytes(2, "big")
    metadata = bytes([len(name)]) + name if index == 0 else b""
    return header + metadata + chunk


def frame(name, size, index, chunk, *, declared=None, capacity=2953, mask=0, n=None):
    return call("frame", name.hex() or "-", size, index, chunk.hex() or "-",
                len(chunk) if declared is None else declared, capacity, mask,
                len(name) if n is None else n)


class FrameV2Tests(unittest.TestCase):
    def assert_frame(self, name, source, index):
        expected = oracle_layout(len(source), index, len(name))
        chunk = source[expected["offset"]:expected["offset"]+expected["raw"]]
        data = frame(name, len(source), index, chunk)
        raw = bytes.fromhex(data["hex"])
        self.assertEqual((data["result"], data["length"]), (OK, expected["complete"]))
        desired = wire(name, len(source), index, chunk)
        self.assertEqual(raw[:9], desired[:9])
        self.assertEqual(raw[9:expected["start"]], desired[9:expected["start"]])
        self.assertEqual(raw[expected["start"]:], chunk)
        self.assertEqual(raw, desired)
        return raw[expected["start"]:]

    def test_constants_and_abi(self):
        c = constants()
        self.assertEqual({k: c[k] for k in ("version", "header", "max", "payload", "name", "count")},
                         dict(version=2, header=9, max=2953, payload=2944, name=12, count=65535))
        self.assertGreaterEqual(c["ulong_max"], 0xFFFFFFFF)
        self.assertGreaterEqual(c["uint_max"], 65535)
        self.assertEqual((c["qr_max"], c["v1_version"], c["v1_header"]), (2953, 1, 9))

    def test_required_size_counts(self):
        for size, expected in SIZES:
            self.assertEqual(count(size), dict(result=OK, count=expected))

    def test_all_name_lengths_first_capacity_boundaries(self):
        for n in range(1, 13):
            first = 2943 - n
            for size, expected in ((first-1, 1), (first, 1), (first+1, 2),
                                   (first+2943, 2), (first+2944, 2), (first+2945, 3)):
                with self.subTest(n=n, size=size):
                    self.assertEqual(count(size, n), dict(result=OK, count=expected))
                    for i in (0, expected-1):
                        self.assertEqual(layout(size, i, n), oracle_layout(size, i, n))

    def test_maximum_count_each_name_length(self):
        for n in range(1, 13):
            maximum = 65535*2944 - 1 - n
            for size in (maximum-1, maximum):
                self.assertEqual(count(size, n), dict(result=OK, count=65535))
                self.assertEqual(layout(size, 65534, n), oracle_layout(size, 65534, n))
            self.assertEqual(count(maximum+1, n)["result"], SIZE)

    def test_last_count_transition(self):
        for n in range(1, 13):
            size = 65534*2944 - 1 - n
            for delta, expected in ((-1, 65534), (0, 65534), (1, 65535)):
                self.assertEqual(count(size+delta, n), dict(result=OK, count=expected))

    def test_no_candidate_size_limit(self):
        self.assertEqual(count(1474561)["result"], OK)
        self.assertEqual(count(1474944)["count"], 502)

    def test_zero_size(self):
        self.assertEqual(count(0)["result"], SIZE)
        self.assertEqual(layout(0, 0)["result"], SIZE)

    def test_invalid_filename_lengths(self):
        for n in (0, 13, 255, 65535, constants()["uint_max"]):
            self.assertEqual(count(1, n)["result"], FILENAME)
            self.assertEqual(layout(1, 0, n)["result"], FILENAME)

    def test_size_overflow_boundaries(self):
        for top in (0xFFFFFFFF, constants()["ulong_max"]):
            for n in range(1, 13):
                for size in (top, top-1, top-n, top-2943):
                    self.assertEqual(count(size, n)["result"], SIZE)

    def test_integer_oracle_samples(self):
        rng = random.Random(0x4E1F)
        for _ in range(80):
            n = rng.randrange(1, 13)
            size = rng.randrange(1, 65535*2944 - n)
            expected = (size + n + 2944) // 2944
            self.assertEqual(count(size, n), dict(result=OK, count=expected))
            i = rng.randrange(expected)
            self.assertEqual(layout(size, i, n), oracle_layout(size, i, n))

    def test_count_null_output_and_precedence(self):
        self.assertEqual(count(1, null=1)["result"], ARGUMENT)
        self.assertEqual(count(0, null=1)["result"], SIZE)
        self.assertEqual(count(0, 0, 1)["result"], FILENAME)

    def test_fixed_6000_layout(self):
        for index, offset, raw, complete in ((0, 0, 2935, 2953), (1, 2935, 2944, 2953),
                                             (2, 5879, 121, 130)):
            row = layout(6000, index)
            self.assertEqual((row["count"], row["offset"], row["raw"], row["complete"]),
                             (3, offset, raw, complete))

    def test_fixed_29198_layout(self):
        for index in range(10):
            row = layout(29198, index)
            self.assertEqual(row, oracle_layout(29198, index, 8))
            self.assertEqual(row["count"], 10)
        self.assertEqual((row["offset"], row["raw"], row["complete"]), (26487, 2711, 2720))

    def test_required_sizes_last_frame(self):
        for size, frames in SIZES:
            self.assertEqual(layout(size, frames-1), oracle_layout(size, frames-1, 8))

    def test_64k_offsets_and_crossing(self):
        first, last = layout(70000, 22), layout(70000, 23)
        self.assertEqual((first["offset"], first["raw"]), (64759, 2944))
        self.assertLess(first["offset"], 65536)
        self.assertGreater(first["offset"]+first["raw"], 65536)
        self.assertEqual((last["offset"], last["raw"]), (67703, 2297))

    def test_index_validation(self):
        for size, frames in SIZES:
            for index in (frames, min(frames+1, 65535), 65535):
                self.assertEqual(layout(size, index)["result"], INDEX)

    def test_layout_null_and_precedence(self):
        self.assertEqual(layout(1, 0, null=1)["result"], ARGUMENT)
        self.assertEqual(layout(1, 1, null=1)["result"], INDEX)
        self.assertEqual(layout(0, 1, null=1)["result"], SIZE)
        self.assertEqual(layout(0, 1, 0, 1)["result"], FILENAME)

    def test_fixed_frame0_prefix_6000(self):
        chunk = fixture(2935)
        data = frame(b"TEST.MAG", 6000, 0, chunk)
        expected = bytes.fromhex("50393851020000000308544553542e4d4147")
        self.assertEqual(data["length"], 2953)
        self.assertEqual(bytes.fromhex(data["hex"]), expected + chunk)

    def test_6000_all_frames_reassemble(self):
        source = fixture(6000)
        self.assertEqual(b"".join(self.assert_frame(b"TEST.MAG", source, i) for i in range(3)), source)

    def test_29198_all_frames_reassemble(self):
        source = fixture(29198)
        self.assertEqual(b"".join(self.assert_frame(b"TEST.MAG", source, i) for i in range(10)), source)

    def test_70000_all_frames_reassemble(self):
        source = fixture(70000)
        self.assertEqual(b"".join(self.assert_frame(b"TEST.MAG", source, i) for i in range(24)), source)

    def test_1474560_all_frames_reassemble(self):
        source = fixture(1474560)
        self.assertEqual(b"".join(self.assert_frame(b"TEST.MAG", source, i) for i in range(501)), source)

    def test_single_frame_and_transition_sizes(self):
        for size in (1, 100, 2900, 2935, 2936, 2944, 2945):
            source = fixture(size)
            frames = (size+2952)//2944
            self.assertEqual(b"".join(self.assert_frame(b"TEST.MAG", source, i)
                                     for i in range(frames)), source)

    def test_filename_lengths_and_metadata(self):
        for n in range(1, 13):
            name = filename(n)
            source = fixture(2944)
            self.assertEqual(len(name), n)
            self.assertEqual(b"".join(self.assert_frame(name, source, i) for i in range(2)), source)

    def test_complete_big_endian_vectors(self):
        for index, frames in ((254, 255), (255, 256), (256, 501), (65534, 65535)):
            size = (frames-1)*2944 - 9 + 1
            offset = index*2944 - 9
            chunk = fixture(min(2944, size-offset))
            data = frame(b"TEST.MAG", size, index, chunk)
            expected = b"P98Q\x02" + index.to_bytes(2, "big") + frames.to_bytes(2, "big")
            self.assertEqual(data["result"], OK)
            self.assertEqual(bytes.fromhex(data["hex"])[:9], expected)

    def test_special_binary_sequence(self):
        self.assertEqual(self.assert_frame(b"A.BIN", SPECIAL, 0), SPECIAL)

    def test_all_256_byte_values(self):
        source = bytes(range(256))
        self.assertEqual(self.assert_frame(b"A.BIN", source, 0), source)

    def test_binary_at_new_split_boundaries(self):
        source = bytearray(fixture(6000))
        for offset in (0, 2931, 5875):
            source[offset:offset+len(SPECIAL)] = SPECIAL
        source[-2:] = b"\x00\x1a"
        source = bytes(source)
        self.assertEqual(b"".join(self.assert_frame(b"TEST.MAG", source, i) for i in range(3)), source)

    def test_nonzero_frame_has_no_metadata(self):
        chunk = b"\x08TEST.MAG" + fixture(2935)
        data = frame(b"TEST.MAG", 6000, 1, chunk)
        self.assertEqual(bytes.fromhex(data["hex"])[9:], chunk)

    def test_reused_output_long_short_long(self):
        rows = call("reuse")
        source = fixture(6000)
        self.assertEqual([r["length"] for r in rows], [2953, 2953, 130, 2953])
        restored = []
        for row, index in zip(rows, (0, 1, 2, 0)):
            expected = oracle_layout(6000, index, 8)
            chunk = source[expected["offset"]:expected["offset"]+expected["raw"]]
            self.assertEqual(bytes.fromhex(row["hex"]), wire(b"TEST.MAG", 6000, index, chunk))
            restored.append(bytes.fromhex(row["hex"])[expected["start"]:])
        self.assertEqual(b"".join(restored[:3]), source)

    def test_output_capacity_errors_leave_buffer_untouched(self):
        for cap in (0, 1, 8, 9, 17, 2952):
            self.assertEqual(frame(b"TEST.MAG", 6000, 0, fixture(2935), capacity=cap)["result"], CAPACITY)
        self.assertEqual(frame(b"TEST.MAG", 6000, 2, fixture(121), capacity=129)["result"], CAPACITY)

    def test_exact_and_extra_output_capacity(self):
        for capacity in (130, 2953, 2954, 65535):
            self.assertEqual(frame(b"TEST.MAG", 6000, 2, fixture(121), capacity=capacity)["length"], 130)

    def test_zero_short_and_excess_chunk_lengths(self):
        for index, expected in ((0, 2935), (1, 2944), (2, 121)):
            for length in (0, expected-1, expected+1, 2953):
                self.assertEqual(frame(b"TEST.MAG", 6000, index, fixture(length))["result"], LENGTH)

    def test_huge_declared_chunk_length_is_rejected_before_read(self):
        self.assertEqual(frame(b"A", 1, 0, b"x", declared=constants()["uint_max"])["result"], LENGTH)

    def test_invalid_metadata_even_on_nonzero_frame(self):
        for name in (b"", b"test.mag", b"CON.TXT", b"A\0B", b"A/B", b"\x80", b"A"*13):
            for index in (0, 1):
                self.assertEqual(frame(name, 6000, index, b"x")["result"], FILENAME)

    def test_serializer_invalid_name_length_before_read(self):
        for n in (0, 13, constants()["uint_max"]):
            self.assertEqual(frame(b"A", 1, 0, b"x", n=n)["result"], FILENAME)

    def test_serializer_size_and_index_errors(self):
        for size, index, expected in ((0, 0, SIZE), (0xFFFFFFFF, 0, SIZE),
                                       (1, 1, INDEX), (6000, 3, INDEX), (6000, 65535, INDEX)):
            self.assertEqual(frame(b"TEST.MAG", size, index, b"x")["result"], expected)

    def test_serializer_null_arguments(self):
        for mask in range(1, 16):
            self.assertEqual(frame(b"A", 1, 0, b"x", mask=mask)["result"], ARGUMENT)

    def test_serializer_validation_precedence(self):
        cases = ((b"CON", 0, 5, b"", 0, 1, ARGUMENT),
                 (b"CON", 0, 5, b"", 0, 0, FILENAME),
                 (b"A", 0, 5, b"", 0, 0, SIZE),
                 (b"A", 1, 5, b"", 0, 0, INDEX),
                 (b"A", 1, 0, b"", 0, 0, LENGTH),
                 (b"A", 1, 0, b"x", 0, 0, CAPACITY))
        for name, size, index, chunk, cap, mask, expected in cases:
            self.assertEqual(frame(name, size, index, chunk, capacity=cap, mask=mask)["result"], expected)

    def test_exact_input_bounds_without_nul(self):
        self.assertTrue(call("bounds"))

    def test_v1_public_contract_unchanged(self):
        self.assertTrue(call("v1"))


if __name__ == "__main__":
    unittest.main()
