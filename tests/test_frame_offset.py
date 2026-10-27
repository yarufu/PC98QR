# Phase 4B-2dのindex/総数専用APIを独立した整数計算で検証する。
"""Phase 4B-2d: index/count-only public API and independent integer oracle."""
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
OK, COUNT, INDEX, ARGUMENT = range(4)
SENTINEL = 0xA55A5AA5
VALID = (
    (0, 1, 0), (0, 2, 0), (1, 2, 2944),
    (0, 3, 0), (1, 3, 2944), (2, 3, 5888),
    (21, 23, 61824), (22, 23, 64768), (23, 24, 67712),
    (499, 501, 1469056), (500, 501, 1472000),
    (500, 502, 1472000), (501, 502, 1474944),
    (65533, 65535, 192929152), (65534, 65535, 192932096),
)
INVALID = (
    (0, 0, COUNT), (1, 0, COUNT), (65535, 0, COUNT),
    (1, 1, INDEX), (2, 2, INDEX), (3, 3, INDEX),
    (500, 500, INDEX), (501, 501, INDEX), (65535, 65535, INDEX),
)


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_FRAME_OFFSET_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build/test_frame_offset"
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-o", str(target),
                    str(ROOT / "tests/frame_offset_main.c"),
                    str(ROOT / "src/frame_offset.c")], check=True, cwd=ROOT)
    return target


def call(*args):
    result = subprocess.run([str(driver()), *map(str, args)],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode or result.stderr:
        raise AssertionError(f"frame offset driver: exit {result.returncode}\n{result.stderr}")
    return json.loads(result.stdout)


def oracle(index, count):
    if not 1 <= count <= 65535:
        return {"result": COUNT, "offset": SENTINEL}
    if not 0 <= index < count:
        return {"result": INDEX, "offset": SENTINEL}
    return {"result": OK, "offset": index * 2944}


class FrameOffsetTests(unittest.TestCase):
    def test_constants_and_host_widths(self):
        actual = call("constants")
        self.assertEqual(actual["payload_max"], 2944)
        self.assertEqual(actual["count_max"], 65535)
        self.assertEqual(actual["char_bit"], 8)
        self.assertEqual(actual["ushort_max"], 65535)
        self.assertGreaterEqual(actual["ulong_max"], 0xFFFFFFFF)

    def test_fixed_valid_vectors(self):
        for index, count, offset in VALID:
            with self.subTest(index=index, count=count):
                expected = {"result": OK, "offset": offset}
                self.assertEqual(call(index, count), expected)
                self.assertEqual(oracle(index, count), expected)

    def test_fixed_invalid_vectors_preserve_output(self):
        for index, count, result in INVALID:
            with self.subTest(index=index, count=count):
                expected = {"result": result, "offset": SENTINEL}
                self.assertEqual(call(index, count), expected)
                self.assertEqual(oracle(index, count), expected)

    def test_count_error_before_index_and_null(self):
        for index in (0, 1, 65535):
            with self.subTest(index=index):
                self.assertEqual(call(index, 0, "null"),
                                 {"result": COUNT, "offset": SENTINEL})

    def test_index_error_before_null(self):
        for index, count, result in INVALID + ((2, 1, INDEX), (65535, 1, INDEX)):
            if result == INDEX:
                with self.subTest(index=index, count=count):
                    self.assertEqual(call(index, count, "null"),
                                     {"result": INDEX, "offset": SENTINEL})

    def test_null_output_for_valid_inputs(self):
        for index, count, _ in VALID:
            with self.subTest(index=index, count=count):
                self.assertEqual(call(index, count, "null"),
                                 {"result": ARGUMENT, "offset": SENTINEL})

    def test_cross_64k_without_truncation(self):
        offsets = [call(index, 24)["offset"] for index in (21, 22, 23)]
        self.assertEqual(offsets, [61824, 64768, 67712])
        self.assertLess(offsets[1], 65536)
        self.assertGreater(offsets[2], 65535)
        self.assertEqual(offsets[2] - offsets[1], 2944)

    def test_deterministic_valid_samples(self):
        rng = random.Random(0x4B2D)
        counts = [1, 2, 3, 23, 24, 501, 502, 32768, 65535]
        counts += [rng.randrange(1, 65536) for _ in range(32)]
        for count in counts:
            for index in sorted({0, count - 1, rng.randrange(count)}):
                with self.subTest(index=index, count=count):
                    self.assertEqual(call(index, count), oracle(index, count))

    def test_deterministic_index_equals_count(self):
        rng = random.Random(0x4B2D01)
        for count in [rng.randrange(1, 65536) for _ in range(32)]:
            with self.subTest(count=count):
                self.assertEqual(call(count, count), oracle(count, count))

    def test_deterministic_index_greater_than_count(self):
        rng = random.Random(0x4B2D02)
        pairs = [(65535, 1), (65535, 65534)]
        for _ in range(32):
            count = rng.randrange(1, 65535)
            pairs.append((rng.randrange(count + 1, 65536), count))
        for index, count in pairs:
            with self.subTest(index=index, count=count):
                self.assertEqual(call(index, count), oracle(index, count))


if __name__ == "__main__":
    unittest.main()
