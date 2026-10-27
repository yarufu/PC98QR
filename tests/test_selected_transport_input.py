# 本番main、実ヘルパー、I/O、QR、画素を通じた選択フレームの試験。
"""Selected frame through production main, real helpers, I/O, QR and pixels."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import cv2
import numpy as np

import qr_v40_oracle as v40
import test_file_input as legacy
from test_file_chunk import pattern

ROOT = Path(__file__).resolve().parents[1]
INDEX_ERROR = "transport frame index must be 0-65534."
RANGE_ERROR = "transport frame index is out of range."
SIZE_ERROR = "transport file size is not supported."
IO_ERROR = "file I/O failed."
ORDER = "size,count,offset,chunk,header,encoder,graphics"


class SelectedTransportInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        override = os.environ.get("PC98QR_MAIN_DRIVER")
        cls.executable = Path(override) if override else ROOT / "build/test_main_render"
        if not override:
            subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                           stdout=subprocess.PIPE)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    assert_success = legacy.FileInputTests.assert_success
    assert_file_error = legacy.FileInputTests.assert_file_error

    def fixture(self, size, name="INPUT.BIN"):
        path = self.directory / name
        with path.open("wb") as stream:
            for offset in range(0, size, 8192):
                stream.write(pattern(offset, min(8192, size - offset)))
        return path

    def invoke(self, *arguments, **faults):
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith("PC98QR_TEST_")}
        trace = self.directory / "trace.json"
        environment["PC98QR_TEST_TRACE"] = str(trace)
        for key, value in faults.items():
            environment["PC98QR_TEST_" + key.upper()] = str(value)
        result = subprocess.run([str(self.executable), *map(str, arguments)],
                                cwd=self.directory, env=environment, capture_output=True)
        self.assertTrue(trace.exists(), result.stderr)
        observation = json.loads(trace.read_text())
        trace.unlink()
        return result, observation

    def assert_stopped(self, result, observation, message, order, io=None):
        self.assert_file_error(result, message)
        self.assertEqual(observation["order"], order)
        stages = order.split(",") if order else []
        self.assertEqual(observation["calls"],
                         [int(s in stages) for s in ORDER.split(",")[:-1]] + [0])
        if io is not None:
            self.assertEqual(observation["io"], io)

    def assert_frame(self, size, index, count, offset, length, header, **faults):
        path = self.fixture(size)
        result, observation = self.invoke("/T", index, path, **faults)
        expected = bytes.fromhex(header) + pattern(offset, length)
        frame = self.assert_success(result, expected)  # zxing-cpp result.bytes / 日本語: zxing-cppのresult.bytesを使う。
        self.assertEqual(observation["order"], ORDER)
        self.assertEqual(observation["calls"], [1, 1, 1, 1, 1, 1, 5])
        self.assertEqual([observation[k] for k in ("size", "index", "count", "offset", "payload")],
                         [size, int(index), count, offset, length])
        self.assertEqual(observation["io"][0:2], [2, 2])
        self.assertEqual(observation["io"][3], 2)
        self.assertEqual(path.stat().st_size, size)
        with path.open("rb") as stream:
            stream.seek(offset)
            self.assertEqual(stream.read(length), expected[9:])
        # 独立した基準で、選択マスク・形式情報・全モジュール・全画素を確認する。
        # Independent reference: chosen mask/format, all modules and pixels.
        actual = (frame[23:377:2, 143:497:2] == 0).astype(np.uint8)
        self.assertEqual(actual.shape, (177, 177))
        positions, _ = v40.format_positions()
        word = sum(int(actual[y, x]) << bit for bit, (x, y) in enumerate(positions))
        mask = ((word ^ 0x5412) >> 10) & 7
        self.assertEqual(word, v40.format_word(mask))
        reference = np.asarray(v40.reference(expected, mask).get_matrix(), np.uint8)
        np.testing.assert_array_equal(actual, reference)
        pixels = np.full((400, 640), 255, np.uint8)
        pixels[23:377, 143:497] = (1 - reference).repeat(2, 0).repeat(2, 1) * 255
        np.testing.assert_array_equal(frame, pixels)
        # ホスト上の証拠だけであり、PC-98実機写真ではない。
        # Host evidence only; never a PC-98 photograph.
        target = ROOT / "build/phase4b3-fixtures"
        target.mkdir(parents=True, exist_ok=True)
        stem = f"size{size}-index{index}"
        self.assertTrue(cv2.imwrite(str(target / (stem + ".png")), frame))
        (target / (stem + ".json")).write_text(json.dumps({
            **observation, "header": header, "decoded_length": len(expected),
            "selected_mask": mask, "raw_bytes_equal": True,
        }, indent=2) + "\n")
        return result, observation

    def test_6000_frame0(self):
        self.assert_frame(6000, 0, 3, 0, 2944, "503938510100000003")

    def test_6000_frame1(self):
        self.assert_frame(6000, 1, 3, 2944, 2944, "503938510100010003")

    def test_6000_frame2(self):
        self.assert_frame(6000, 2, 3, 5888, 112, "503938510100020003")

    def test_2945_frame0(self):
        self.assert_frame(2945, 0, 2, 0, 2944, "503938510100000002")

    def test_2945_frame1(self):
        self.assert_frame(2945, 1, 2, 2944, 1, "503938510100010002")

    def test_2945_legacy_reject(self):
        result, _ = self.invoke("/T", self.fixture(2945))
        self.assert_file_error(result, "transport file must contain 1-2944 bytes.")

    def test_1474560_frame500(self):
        self.assert_frame(1474560, 500, 501, 1472000, 2560, "503938510101f401f5")

    def test_64k_offset_not_truncated(self):
        self.assert_frame(70000, 23, 24, 67712, 2288, "503938510100170018")

    def test_single_frame_legacy_matches_decoded_mask_matrix_framebuffer(self):
        for size in (1, 2944):
            with self.subTest(size=size):
                selected, _ = self.assert_frame(size, 0, 1, 0, size, "503938510100000001")
                old, _ = self.invoke("/T", self.directory / "INPUT.BIN")
                self.assert_success(old, bytes.fromhex("503938510100000001") + pattern(0, size))
                # PGMの厳密一致には両方の形式情報と選択マスクを含める。
                # 全セル、Quiet Zone、倍率、余白、全画素を照合する。
                # Exact PGM equality includes both format fields/selected mask,
                # every matrix cell, quiet zone, scale, margins and all pixels.
                self.assertEqual(old.stdout, selected.stdout)

    def test_leading_zero_index(self):
        self.assert_frame(6000, "00002", 3, 5888, 112, "503938510100020003")

    def test_zero_filename_still_legacy(self):
        path = self.fixture(1, "0")
        result, trace = self.invoke("/T", path.name)
        self.assert_success(result, bytes.fromhex("503938510100000001") + pattern(0, 1))
        self.assertEqual(trace["order"], "")
        self.assertEqual(trace["calls"][:4], [0, 0, 0, 0])
        self.assertEqual(trace["io"], [1, 0, 2, 1])

    def test_zero_missing_filename_still_legacy_open_error(self):
        result, trace = self.invoke("/T", "0")
        self.assert_file_error(result, "could not open file.")
        self.assertEqual(trace["order"], "")
        self.assertEqual(trace["io"], [1, 0, 0, 0])

    def test_out_of_range_before_chunk(self):
        result, trace = self.invoke("/T", "3", self.fixture(6000))
        self.assert_stopped(result, trace, RANGE_ERROR, "size,count,offset", [1, 1, 0, 1])
        self.assertEqual(trace["count"], 3)

    def test_maximum_index_accepted_by_parser(self):
        result, trace = self.invoke("/T", "65534", self.fixture(6000))
        self.assert_stopped(result, trace, RANGE_ERROR, "size,count,offset", [1, 1, 0, 1])
        self.assertEqual(trace["index"], 65534)

    def test_invalid_index_before_any_file_io(self):
        for index in ("", "-1", "+1", "0x1", "0x10", "A", "1A", "65535", "65536",
                      "999999999999", "9" * 10000, " 1", "1 ", "1\n", "１", "١"):
            with self.subTest(index=index[:20]):
                result, trace = self.invoke("/T", index, "NOFILE.BIN")
                self.assert_stopped(result, trace, INDEX_ERROR, "", [0, 0, 0, 0])

    def test_invalid_index_precedes_empty_path(self):
        result, trace = self.invoke("/T", "-1", "")
        self.assert_stopped(result, trace, INDEX_ERROR, "", [0, 0, 0, 0])

    def test_empty_path(self):
        result, trace = self.invoke("/T", "0", "")
        self.assert_stopped(result, trace, "file path must not be empty.", "", [0, 0, 0, 0])

    def test_empty_file_count_size_error(self):
        result, trace = self.invoke("/T", "0", self.fixture(0))
        self.assert_stopped(result, trace, SIZE_ERROR, "size,count", [1, 1, 0, 1])

    def test_missing_file(self):
        result, trace = self.invoke("/T", "0", "NOFILE.BIN")
        self.assert_stopped(result, trace, "could not open file.", "size", [1, 0, 0, 0])

    def test_size_beyond_wire_count_before_chunk(self):
        path = self.directory / "SPARSE.BIN"
        with path.open("wb") as stream:
            stream.truncate(192935041)
        result, trace = self.invoke("/T", "0", path)
        self.assert_stopped(result, trace, SIZE_ERROR, "size,count", [1, 1, 0, 1])

    def test_size_io_failures(self):
        for fault, io in (("size_open", [1, 0, 0, 0]), ("size_seek", [1, 1, 0, 1]),
                          ("size_close", [1, 1, 0, 1])):
            with self.subTest(fault=fault):
                result, trace = self.invoke("/T", "0", self.fixture(6000), io_fault=fault)
                self.assert_stopped(result, trace, "could not open file." if fault == "size_open"
                                    else IO_ERROR, "size", io)

    def test_chunk_io_failures(self):
        for fault, io in (("chunk_open", [2, 1, 0, 1]), ("chunk_seek", [2, 2, 0, 2]),
                          ("chunk_wrong_seek", [2, 2, 0, 2]), ("chunk_close", [2, 2, 1, 2])):
            with self.subTest(fault=fault):
                result, trace = self.invoke("/T", "1", self.fixture(6000), io_fault=fault)
                self.assert_stopped(result, trace, "could not open file." if fault == "chunk_open"
                                    else IO_ERROR, "size,count,offset,chunk", io)

    def test_positive_short_reads_continue_without_capacity_probe(self):
        self.assert_frame(6000, 1, 3, 2944, 2944, "503938510100010003",
                          read_plan="5,6,1000,1000,933")

    def test_final_positive_short_reads_and_eof(self):
        self.assert_frame(6000, 2, 3, 5888, 112, "503938510100020003",
                          read_plan="5,6,101,0")

    def test_chunk_read_failure_before_data(self):
        result, trace = self.invoke("/T", "1", self.fixture(6000), read_plan="-1")
        self.assert_stopped(result, trace, IO_ERROR, "size,count,offset,chunk", [2, 2, 1, 2])

    def test_chunk_read_failure_after_prefix(self):
        result, trace = self.invoke("/T", "1", self.fixture(6000), read_plan="5,6,-1")
        self.assert_stopped(result, trace, IO_ERROR, "size,count,offset,chunk", [2, 2, 3, 2])

    def test_nonfinal_short_chunk_rejected(self):
        result, trace = self.invoke("/T", "1", self.fixture(6000), resize=3000)
        self.assertEqual(trace["payload"], 56)
        self.assert_stopped(result, trace, IO_ERROR, "size,count,offset,chunk", [2, 2, 2, 2])

    def test_final_short_chunk_rejected(self):
        result, trace = self.invoke("/T", "2", self.fixture(6000), resize=5999)
        self.assertEqual(trace["payload"], 111)
        self.assert_stopped(result, trace, IO_ERROR, "size,count,offset,chunk", [2, 2, 2, 2])

    def test_final_grown_chunk_rejected(self):
        result, trace = self.invoke("/T", "2", self.fixture(6000), resize=6001)
        self.assertEqual(trace["payload"], 113)
        self.assert_stopped(result, trace, IO_ERROR, "size,count,offset,chunk", [2, 2, 2, 2])

    def test_chunk_disappears_after_size(self):
        result, trace = self.invoke("/T", "2", self.fixture(6000), resize=5888)
        self.assert_stopped(result, trace, IO_ERROR, "size,count,offset,chunk", [2, 2, 1, 2])

    def test_header_failure_before_encoder_graphics(self):
        result, trace = self.invoke("/T", "1", self.fixture(6000), header_error=1)
        self.assert_stopped(result, trace, "transport header generation failed.",
                            "size,count,offset,chunk,header", [2, 2, 1, 2])

    def test_encoder_failure_before_graphics(self):
        result, trace = self.invoke("/T", "1", self.fixture(6000), encoder_error=1)
        self.assert_stopped(result, trace, "binary encoding failed.",
                            "size,count,offset,chunk,header,encoder", [2, 2, 1, 2])

    def test_raw_special_bytes_are_preserved(self):
        path = self.directory / "BINARY.BIN"
        raw = bytes.fromhex("001a80ff0d0a") + b"P98Q" + b"\x00\x1a"
        path.write_bytes(raw)
        result, trace = self.invoke("/T", "0", path)
        self.assert_success(result, bytes.fromhex("503938510100000001") + raw)
        self.assertEqual(trace["order"], ORDER)

    def test_lowercase_wrong_arity_and_non_t_are_usage(self):
        for args in (("/t", "0", "FILE"), ("/T", "0", "FILE", "extra"),
                     ("/B", "0", "FILE"), ("/F", "0", "FILE")):
            with self.subTest(args=args):
                result, trace = self.invoke(*args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr.startswith(b"Usage: QRTEST "))
                self.assertIn(b"QRTEST /T INDEX PATH (selected transport frame, index 0-65534)\r\n",
                              result.stderr)
                self.assertEqual(trace["calls"], [0] * 7)
                self.assertEqual(trace["io"], [0] * 4)


if __name__ == "__main__":
    unittest.main()
