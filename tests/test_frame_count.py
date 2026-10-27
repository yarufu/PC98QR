# Phase 4B-2bのサイズ専用API、固定ベクトル、整数の独立基準による検証。
"""Phase 4B-2b: size-only public API, fixed vectors and integer oracle."""
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
OK, ARGUMENT, SIZE = range(3)
SENTINEL = 0xA55A
VECTORS = (
    (0, None),
    (1, 1), (2, 1), (2943, 1), (2944, 1), (2945, 2),
    (5887, 2), (5888, 2), (5889, 3), (6000, 3),
    (65535, 23), (65536, 23), (65537, 23),
    (1474560, 501), (1474561, 501), (1474944, 501), (1474945, 502),
    (192932096, 65534), (192932097, 65535), (192935040, 65535),
    (192935041, None), (0xFFFFFFFF, None),
)


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_FRAME_COUNT_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build/test_frame_count"
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-o", str(target),
                    str(ROOT / "tests/frame_count_main.c"),
                    str(ROOT / "src/frame_count.c")], check=True, cwd=ROOT)
    return target


def call(*args):
    result = subprocess.run([str(driver()), *map(str, args)],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode or result.stderr:
        raise AssertionError(f"frame count driver: exit {result.returncode}\n{result.stderr}")
    return json.loads(result.stdout)


def oracle(size):
    # Pythonの加算は任意精度なので、Cの計算方式とは独立した基準になる。
    # Python addition is arbitrary precision, independent of the C algorithm.
    count = (size + 2943) // 2944
    return count if size > 0 and 1 <= count <= 65535 else None


class FrameCountTests(unittest.TestCase):
    def assert_count(self, size, expected):
        actual = call(size)
        if expected is None:
            self.assertEqual(actual, {"result": SIZE, "count": SENTINEL})
        else:
            self.assertEqual(actual, {"result": OK, "count": expected})

    def test_constants_and_host_widths(self):
        actual = call("constants")
        self.assertEqual(actual["payload_max"], 2944)
        self.assertEqual(actual["count_max"], 65535)
        self.assertEqual(actual["ushort_max"], 65535)
        self.assertGreaterEqual(actual["ulong_max"], 0xFFFFFFFF)
        self.assertEqual(actual["decoded_max"], 2953)
        self.assertEqual(actual["header_size"], 9)
        self.assertEqual(actual["payload_max"],
                         actual["decoded_max"] - actual["header_size"])

    def test_fixed_boundary_vectors(self):
        for size, expected in VECTORS:
            with self.subTest(size=size):
                self.assert_count(size, expected)
                self.assertEqual(oracle(size), expected)

    def test_null_output_for_valid_sizes(self):
        for size, expected in VECTORS:
            if expected is not None:
                with self.subTest(size=size):
                    self.assertEqual(call(size, "null"),
                                     {"result": ARGUMENT, "count": SENTINEL})

    def test_size_error_before_null_output(self):
        for size in (0, 192935041, 192937984, 0xFFFFFFFF,
                     call("constants")["ulong_max"]):
            with self.subTest(size=size):
                self.assertEqual(call(size, "null"),
                                 {"result": SIZE, "count": SENTINEL})

    def test_unsigned_long_maximum_edges(self):
        maximum = call("constants")["ulong_max"]
        # Cでsize+2943が周回し得るULONG_MAX付近を試す。
        # Probe near ULONG_MAX, where size+2943 can wrap in C.
        for delta in (0, 1, 2942, 2943, 2944):
            with self.subTest(delta=delta):
                self.assert_count(maximum - delta, None)

    def test_deterministic_payload_boundaries(self):
        rng = random.Random(0x4B2B)
        counts = [1, 2, 3, 22, 23, 500, 501, 502, 32767, 32768, 65534, 65535, 65536]
        counts += [rng.randrange(1, 65536) for _ in range(32)]
        for count in counts:
            for delta in (-1, 0, 1):
                size = count * 2944 + delta
                with self.subTest(size=size):
                    self.assert_count(size, oracle(size))

    def test_deterministic_unsigned_long_samples(self):
        maximum = call("constants")["ulong_max"]
        rng = random.Random(0xFFFFFFFF)
        sizes = [rng.randrange(0x100000000) for _ in range(32)]
        sizes += [rng.randrange(maximum + 1) for _ in range(32)]
        for size in sizes:
            with self.subTest(size=size):
                self.assert_count(size, oracle(size))


if __name__ == "__main__":
    unittest.main()
