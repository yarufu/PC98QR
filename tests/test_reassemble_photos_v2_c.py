# Cの転送列を独立に検証し、Pythonで復元する相互運用試験。
# ホストCコンパイラーまたは既存ドライバー指定が必要なため、移植可能なWindows試験とは分ける。
# 本番からテストを取り込まない。
"""Host C interoperability: independently check wire, then restore with Python.

Separate from the portable Windows suite: this module needs a host C compiler
or the existing PC98QR_FRAME_V2_DRIVER override. No production imports of tests.
"""
from pathlib import Path
import sys
import tempfile
import unittest

import frame_v2_wire_oracle as oracle
import test_frame_v2 as c_helper

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows import reassemble_photos as receiver


class ReceiverV2CHelperTests(unittest.TestCase):
    def compare_name(self, name, expected):
        actual = c_helper.call("validate", name.hex() or "-", len(name), 0)["result"]
        self.assertEqual(actual, expected, name)
        if expected == 0:
            receiver._validate_filename_v2(name)
        else:
            with self.assertRaises(receiver.ReceiveError) as caught:
                receiver._validate_filename_v2(name)
            self.assertEqual(caught.exception.category,
                             "filename_reserved" if expected == 4 else "filename_invalid")

    def test_all_256_filename_byte_values_against_independent_set(self):
        allowed = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~.")
        for byte in range(256):
            with self.subTest(byte=byte):
                self.compare_name(b"A" + bytes([byte]) + b"B", 0 if byte in allowed else 3)

    def test_filename_grammar_and_reserved_names(self):
        for name in (b"", b".", b"..", b"A.", b".A", b"A..B", b"A.B.C", b"ABCDEFGHI",
                     b"A.ABCD", b"A" * 13, b"test.mag", b"A/B", b"A\\B", b"B:A", b"A\x00B"):
            self.compare_name(name, 3)
        reserved = [b"CON", b"CONIN$", b"CONOUT$", b"PRN", b"AUX", b"NUL", b"CLOCK$",
                    b"COM", b"LPT", b"LST"]
        reserved += [f"{prefix}{i}".encode("ascii") for prefix in ("COM", "LPT") for i in range(10)]
        for base in reserved:
            for ext in (b"", b".MAG"):
                self.compare_name(base + ext, 4)
        for name in (b"CONSOLE.TXT", b"COMPRESS.DAT", b"CONIN$1.TXT", b"CONOUT$1.TXT"):
            self.compare_name(name, 0)

    def test_all_83_lengths_agree(self):
        for base in range(1, 9):
            for extension in range(4):
                self.compare_name(b"A" * base + (b"." + b"X" * extension if extension else b""), 0)

    def check_transfer(self, source, name):
        expected = oracle.split(source, name)
        actual = []
        for index, wire in enumerate(expected):
            start = 10 + len(name) if index == 0 else 9
            row = c_helper.frame(name, len(source), index, wire[start:])
            self.assertEqual(row["result"], 0)
            self.assertEqual(row["length"], len(wire))
            actual.append(bytes.fromhex(row["hex"]))
            self.assertEqual(actual[-1], wire)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "received.bin"
            summary = receiver.reassemble_frames(reversed(actual), output)
            self.assertEqual(tuple(summary), (len(expected), 0, len(source)))
            self.assertEqual(output.read_bytes(), source)
        return actual

    def test_c_6000_fixed_vector_and_receiver(self):
        source = oracle.fixture(6000)
        frames = self.check_transfer(source, b"TEST.MAG")
        self.assertEqual(frames, [oracle.PREFIX_6000 + source[:2935],
                                  bytes.fromhex("503938510200010003") + source[2935:5879],
                                  bytes.fromhex("503938510200020003") + source[5879:]])

    def test_c_29198_fixed_vector_and_receiver(self):
        source = oracle.fixture(29198)
        frames = self.check_transfer(source, b"TEST.MAG")
        self.assertEqual(len(frames), 10)
        self.assertEqual(frames[-1], bytes.fromhex("50393851020009000a") + source[26487:])
        self.assertEqual(len(frames[-1]), 2720)

    def test_c_every_filename_length_capacity_boundaries(self):
        for n, name in enumerate(oracle.NAMES, 1):
            for size in (1, 2943 - n, 2944 - n):
                with self.subTest(n=n, size=size):
                    self.check_transfer(oracle.fixture(size), name)

    def test_c_all_values_and_special_binary(self):
        self.check_transfer(bytes(range(256)) + oracle.SPECIAL, b"ALL.BIN")


if __name__ == "__main__":
    unittest.main()
