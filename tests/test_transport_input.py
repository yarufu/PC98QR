# 本番main、ファイルI/O、符号化、描画を通じた単一転送フレームの試験。
"""Single transport frame through production main, file I/O, encoder and render."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import cv2
import numpy as np

import qr_v40_oracle as v40
import test_file_input as legacy
from tools.generate_phase4b1_fixtures import make_fixtures, payload

ROOT = Path(__file__).resolve().parents[1]
# 期待する転送バイト列は独立したリテラルで、Cヘルパーから取得しない。
# Independent literal expected wire bytes; never obtained from the C helper.
HEADER = bytes.fromhex("503938510100000001")
OVERSIZE = "transport file must contain 1-2944 bytes."


class TransportInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        override = os.environ.get("PC98QR_MAIN_DRIVER")
        cls.executable = Path(override) if override else ROOT / "build/test_main_render"
        if not override:
            subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                           stdout=subprocess.PIPE)

    # 既存の画面用生バイト復号器と、厳密なエラー検査を再利用する。
    # Reuse the legacy framebuffer raw-byte decoder and exact error assertions.
    assert_success = legacy.FileInputTests.assert_success
    assert_file_error = legacy.FileInputTests.assert_file_error

    def invoke(self, *arguments, cwd=None, read_plan=None, close_error=False,
               encoder_error=False, header_error=False):
        environment = os.environ.copy()
        for name in (*legacy.FAULT_VARIABLES, "PC98QR_TEST_HEADER_ERROR"):
            environment.pop(name, None)
        if read_plan is not None:
            environment["PC98QR_TEST_READ_PLAN"] = read_plan
        for name, enabled in (("CLOSE", close_error), ("ENCODER", encoder_error),
                              ("HEADER", header_error)):
            if enabled:
                environment[f"PC98QR_TEST_{name}_ERROR"] = "1"
        return subprocess.run([str(self.executable), *arguments], cwd=cwd,
                              env=environment, capture_output=True)

    def read_file(self, raw, *, mode="/T", **faults):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "INPUT.BIN"
            path.write_bytes(raw)
            result = self.invoke(mode, str(path), **faults)
            self.assertEqual(path.read_bytes(), raw)
            return result

    def assert_transport(self, raw, **faults):
        frame = self.assert_success(self.read_file(raw, **faults), HEADER + raw)
        # 独立したV40-L基準で全モジュールと640×400全画素を検査する。
        # 2画素倍率、本体原点、4モジュールのQuiet Zoneも含む。
        # Independent V40-L reference checks all modules and all 640x400 pixels,
        # including 2px scale, body origin and the four-module quiet zone.
        actual = (frame[23:377:2, 143:497:2] == 0).astype(np.uint8)
        self.assertEqual(actual.shape, (177, 177))
        positions, _ = v40.format_positions()
        word = sum(int(actual[y, x]) << bit for bit, (x, y) in enumerate(positions))
        mask = ((word ^ 0x5412) >> 10) & 7
        self.assertEqual(word, v40.format_word(mask))  # ECC level L. / 日本語: 誤り訂正レベルはL。
        reference = np.asarray(v40.reference(HEADER + raw, mask).get_matrix(), np.uint8)
        np.testing.assert_array_equal(actual, reference)
        expected = np.full((400, 640), 255, np.uint8)
        expected[23:377, 143:497] = (1 - reference).repeat(2, 0).repeat(2, 1) * 255
        np.testing.assert_array_equal(frame, expected)
        outer = np.pad(1 - reference, 4, constant_values=1).repeat(2, 0).repeat(2, 1) * 255
        self.assertEqual(outer.shape, (370, 370))
        np.testing.assert_array_equal(frame[15:385, 135:505], outer)
        return frame

    def test_minimum_one_byte_v40_framebuffer_raw_decode(self):
        frame = self.assert_transport(make_fixtures()["T1.BIN"])
        self.save_frame("t1-main-screen.png", frame)

    def test_two_bytes_v40_framebuffer_raw_decode(self):
        self.assert_transport(payload(2))

    def test_2943_bytes_v40_framebuffer_raw_decode(self):
        self.assert_transport(payload(2943))

    def test_maximum_2944_bytes_v40_framebuffer_raw_decode(self):
        fixtures = make_fixtures()
        self.assertEqual(fixtures["TF2944.BIN"], HEADER + fixtures["T2944.BIN"])
        self.assertEqual(len(fixtures["TF2944.BIN"]), 2953)
        frame = self.assert_transport(fixtures["T2944.BIN"])
        self.save_frame("t2944-main-screen.png", frame)

    def save_frame(self, name, frame):
        target = ROOT / "build/phase4b1-fixtures"
        target.mkdir(parents=True, exist_ok=True)
        self.assertTrue(cv2.imwrite(str(target / name), frame))

    def test_legacy_f_same_maximum_fixture_has_no_header(self):
        raw = make_fixtures()["T2944.BIN"]
        frame = self.assert_success(self.read_file(raw, mode="/F"), raw)
        self.save_frame("f2944-main-screen.png", frame)

    def test_one_byte_f_remains_v1_and_t_uses_v40(self):
        raw = make_fixtures()["T1.BIN"]
        frame = self.assert_success(self.read_file(raw, mode="/F"), raw)
        legacy.FileInputTests.assert_profile(self, frame, 1)
        self.assertFalse(np.array_equal(frame, self.assert_transport(raw)))

    def test_empty_file(self):
        self.assert_file_error(self.read_file(b""), "file is empty.")

    def test_2945_bytes_rejected_before_encoder_and_graphics(self):
        self.assert_file_error(self.read_file(make_fixtures()["T2945.BIN"]), OVERSIZE)

    def test_larger_file_stops_after_extra_byte(self):
        self.assert_file_error(self.read_file(payload(6000), read_plan="2944,1"), OVERSIZE)

    def test_each_special_2945th_byte_rejects(self):
        raw = payload(2944)
        for value in (0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A):
            with self.subTest(value=value):
                self.assert_file_error(self.read_file(raw + bytes([value]),
                                                      read_plan="2944,1"), OVERSIZE)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assert_file_error(self.invoke("/T", str(Path(directory) / "MISSING.BIN")),
                                   "could not open file.")

    def test_empty_path(self):
        self.assert_file_error(self.invoke("/T", ""), "file path must not be empty.")

    def test_positive_short_reads_continue_through_eof(self):
        self.assert_transport(payload(2944), read_plan="5,6,1000,1000,933,0")

    def test_maximum_byte_at_a_time_and_eof(self):
        self.assert_transport(payload(2944), read_plan=",".join(["1"] * 2944 + ["0"]))

    def test_maximum_requires_eof(self):
        self.assert_transport(payload(2944), read_plan="2944,0")

    def test_read_error_before_any_data(self):
        self.assert_file_error(self.read_file(payload(1), read_plan="-1"), "file I/O failed.")

    def test_partial_read_error(self):
        self.assert_file_error(self.read_file(payload(2944), read_plan="5,6,-1"), "file I/O failed.")

    def test_error_after_maximum_is_not_eof(self):
        self.assert_file_error(self.read_file(payload(2944), read_plan="2944,-1"), "file I/O failed.")

    def test_close_error_before_header_encoder_and_graphics(self):
        for length in (1, 2944):
            with self.subTest(length=length):
                self.assert_file_error(self.read_file(payload(length), close_error=True), "file I/O failed.")

    def test_header_error_before_encoder_and_graphics(self):
        self.assert_file_error(self.read_file(payload(2944), header_error=True),
                               "transport header generation failed.")

    def test_encoder_error_before_graphics(self):
        self.assert_file_error(self.read_file(payload(2944), encoder_error=True), "binary encoding failed.")

    def test_binary_special_values_magic_and_trailing_bytes_preserved(self):
        for ending in (b"\x00", b"\x1a", b"\xff"):
            raw = bytes.fromhex("001a80ff0d0a") + b"P98Q" + ending
            with self.subTest(ending=ending):
                self.assert_transport(raw)

    def test_single_argument_options_remain_alphanumeric(self):
        for text in ("/T", "/B", "/F", "HELLO"):
            with self.subTest(text=text):
                self.assert_success(self.invoke(text), text.encode("ascii"))

    def test_lowercase_and_extra_arguments_are_usage_errors(self):
        # argc=4は/T INDEX PATH用になったが、argc=5は引き続きusageエラー。
        # argc == 4 now belongs to /T INDEX PATH; argc == 5 is still usage.
        for args in (("/t", "INPUT.BIN"), ("/T", "0", "INPUT.BIN", "extra")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr.startswith(b"Usage: QRTEST "))
                self.assertIn(b"QRTEST /T PATH (transport payload, 1-2944 bytes)\r\n",
                              result.stderr)

    def test_explicit_b_keeps_raw_bytes(self):
        raw = bytes.fromhex("001a80ff0d0a")
        self.assert_success(self.invoke("/B", raw.hex()), raw)

    def test_fixture_wire_sizes_and_binary_boundaries(self):
        fixtures = make_fixtures()
        self.assertEqual({name: len(raw) for name, raw in fixtures.items()}, {
            "T1.BIN": 1, "T2944.BIN": 2944, "T2945.BIN": 2945,
            "TF1.BIN": 10, "TF2944.BIN": 2953,
        })
        self.assertEqual(fixtures["T1.BIN"], b"\x00")
        self.assertEqual(fixtures["TF1.BIN"], HEADER + fixtures["T1.BIN"])
        self.assertEqual(fixtures["TF2944.BIN"], HEADER + fixtures["T2944.BIN"])
        self.assertEqual(fixtures["T2945.BIN"], fixtures["T2944.BIN"] + b"\x00")
        self.assertTrue(fixtures["T2944.BIN"].startswith(bytes.fromhex("001a80ff0d0a") + b"P98Q"))
        self.assertTrue(fixtures["T2944.BIN"].endswith(bytes.fromhex("001aff")))


if __name__ == "__main__":
    unittest.main()
