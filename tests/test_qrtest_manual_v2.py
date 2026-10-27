# 別のフレーム生成器ではなく、本番v2 CLIと制御部の統合試験。
# 実ファイルヘルパー・直列化・符号化・選択を観測し、期待値は独立基準と固定ベクトルから作る。
"""V2 production CLI/controller integration, not an alternate frame generator.

The host harness observes real file helpers, serializer, encoder and selection.
Expected bytes come from the independent wire oracle and fixed vectors.
"""
from contextlib import chdir
from pathlib import Path
import sys
import tempfile
import unittest

import test_qrtest_manual as qtest
import frame_v2_wire_oracle as wire_oracle

sys.path.insert(0, str(qtest.ROOT))
from windows import reassemble_photos as receiver


class QRTestManualV2Tests(unittest.TestCase):
    setUp = qtest.QRTestManualTests.setUp
    manual = qtest.QRTestManualTests.manual
    check_frames = qtest.QRTestManualTests.check_frames

    def test_6000_fixed_prefix_lengths_and_receiver(self):
        _, rows = self.check_frames(6000, [1, 2, 3])
        frames = [bytes.fromhex(row["wire"]) for row in rows]
        self.assertEqual(frames[0][:18], bytes.fromhex("50393851020000000308544553542e4d4147"))
        self.assertEqual([row["offset"] for row in rows], [0, 2935, 5879])
        self.assertEqual(list(map(len, frames)), [2953, 2953, 130])
        self.restore(frames)

    def restore(self, frames):
        # 空の出力先でファイル名の自動復元を使う。
        # 入力フレームはハーネスを通じて実際の本番mainが出力したもの。
        # Use automatic filename restoration in an empty destination. These
        # are frames emitted by the real production main through the harness.
        with tempfile.TemporaryDirectory(dir=self.directory.name) as directory, chdir(directory):
            summary = receiver.reassemble_frames([*reversed(frames), frames[0]])
            self.assertEqual(tuple(summary), (len(frames), 1, self.path.stat().st_size))
            self.assertEqual(Path("TEST.MAG").read_bytes(), self.path.read_bytes())

    def test_29198_all_ten_frames_and_receiver(self):
        _, rows = self.check_frames(29198, list(range(1, 11)))
        frames = [bytes.fromhex(row["wire"]) for row in rows]
        self.assertEqual(rows[-1]["offset"], 26487)
        self.assertEqual(len(frames[-1]), 2720)
        self.assertEqual(frames[-1][:9], bytes.fromhex("50393851020009000a"))
        self.restore(frames)

    def test_70000_crossing_and_above_64k(self):
        _, rows = self.check_frames(70000, [1, 23, 24, 2, 1])
        self.assertEqual([r["offset"] for r in rows], [0, 64759, 67703, 2935, 0])
        self.assertLess(64759, 65536)
        self.assertGreater(64759 + 2944, 65536)
        self.assertEqual(len(bytes.fromhex(rows[2]["wire"])), 2306)

    def test_1474560_first_last_and_return(self):
        _, rows = self.check_frames(1474560, [1, 501, 1])
        final = bytes.fromhex(rows[1]["wire"])
        self.assertEqual((rows[1]["offset"], len(final)), (1471991, 2578))
        self.assertEqual(final[:9], bytes.fromhex("503938510201f401f5"))

    def test_all_filename_lengths_first_capacity_boundaries(self):
        for n, name in enumerate(wire_oracle.NAMES, 1):
            self.path = Path(self.directory.name) / name.decode("ascii")
            capacity = 2943 - n
            for size in (capacity-1, capacity, capacity+1):
                with self.subTest(name=name, size=size):
                    numbers = [1, 2, 1] if size > capacity else [1]
                    _, rows = self.check_frames(size, numbers)
                    first = bytes.fromhex(rows[0]["wire"])
                    self.assertEqual(first[9:10+n], bytes([n]) + name)
                    self.assertEqual(len(first), 2952 if size < capacity else 2953)
                    if size > capacity:
                        self.assertEqual(len(bytes.fromhex(rows[1]["wire"])), 10)

    def test_normal_capacity_and_one_byte_final(self):
        for size, lengths in ((5879, [2953, 2953]), (5880, [2953, 2953, 10])):
            _, rows = self.check_frames(size, list(range(1, len(lengths)+1)))
            self.assertEqual([len(bytes.fromhex(r["wire"])) for r in rows], lengths)

    def test_all_binary_values_and_special_split_bytes(self):
        raw = bytearray((bytes(range(256))*24)[:6000])
        for offset in (0, 2932, 5876, 5994):
            raw[offset:offset+6] = bytes.fromhex("001a80ff0d0a")
        _, rows = self.check_frames(6000, [1, 2, 3, 1], raw=bytes(raw))
        frames = [bytes.fromhex(r["wire"]) for r in rows[:3]]
        self.assertEqual(set(raw), set(range(256)))
        self.restore(frames)

    def test_reverse_reselect_and_frame_zero_return(self):
        data, rows = self.check_frames(6000, [1, 3, 2, 2, 1, 3, 1])
        self.assertEqual(data["chunks"], 7)
        self.assertEqual(rows[2]["wire"], rows[3]["wire"])
        self.assertEqual(rows[0]["wire"], rows[4]["wire"])

    def test_dos_path_spelling_preserved_at_io_boundary(self):
        expected = wire_oracle.split(self.path.read_bytes())[0]
        for path in (r"b:\test.mag", r"B:test.mag", r"\DIR\test.mag",
                     r".\test.mag", r"..\DIR\test.mag", r"B:\DIR\..\test.mag"):
            with self.subTest(path=path):
                data = qtest.run(["/M", path], ("1e53", qtest.ESC), DISK_PATH=self.path)
                self.assertEqual(data["result"], 0)
                self.assertEqual(data["information"], qtest.info_screen(path, 6000))
                self.assertEqual(bytes.fromhex(data["rows"][0]["wire"]), expected)

    def test_invalid_paths_stop_before_file_io_and_graphics(self):
        paths = [r"B:\日本語\TEST.MAG", r"B:\画像.MAG", r"B:\LONGDIRNAME\TEST.MAG",
                 r"B:\TEST FILE.MAG", r"B:\LST.BIN", r"B:\CONIN$.TXT", r"B:\CONOUT$",
                 r"B:\NUL\TEST.MAG", "/tmp/TEST.MAG", r"B:\TEST.MAG" + "\\", r"B:\\TEST.MAG",
                 r"\\HOST\SHARE\TEST.MAG", r"B:\A*.MAG", "B:", r"B:\ABCDEFGHI.MAG"]
        for path in paths:
            with self.subTest(path=path):
                data = qtest.run(["/M", path], DISK_PATH=self.path)
                self.assertEqual((data["result"], data["internal"]), (1, 2))
                self.assertEqual([data[k] for k in ("sizes", "chunks", "encodes", "inits", "shutdowns")], [0]*5)
                self.assertEqual(data["trace"], "filename,error")

    def test_null_and_small_capacities_precede_filename(self):
        for bad in ("path", "frame", "matrix", "fc", "mc"):
            with self.subTest(bad=bad):
                data = self.manual(BAD_ARGUMENT=bad)
                self.assertEqual((data["result"], data["internal"], data["sizes"]), (1, 2, 0))
                self.assertEqual(data["trace"], "error")

    def test_short_positive_reads_preserve_all_bytes(self):
        self.check_frames(6000, [1, 2, 3], READ_LIMIT=17)

    def test_exact_and_larger_api_capacities_keep_guards(self):
        for capacity in ("exact", "larger"):
            self.check_frames(6000, [1, 2, 3, 1], GUARDS=capacity)
            for fault in ("readpartial2", "header2", "encode2"):
                data = self.manual(("1e53", *qtest.oracle.select(3), qtest.ESC),
                                   GUARDS=capacity, FAULT=fault)
                self.assertEqual((data["result"], data["clears"], data["shutdowns"]), (1, 1, 1))

    def test_final_growth_and_shrink_between_size_and_read(self):
        for fault in ("grow2", "shrink2", "delete2"):
            self.path.write_bytes(wire_oracle.fixture(6000))
            data = self.manual(("1e53", *qtest.oracle.select(3), qtest.ESC), FAULT=fault)
            self.assertEqual((data["result"], data["internal"]), (1, 8))
            self.assertEqual((data["encodes"], data["clears"], data["shutdowns"]), (1, 1, 1))
            self.assertEqual(data["trace"].split(",")[-2:], ["shutdown", "error"])

    def test_partial_read_and_close_failures_never_redraw_scratch(self):
        for at in (1, 2):
            for fault in ("readsys", "readpartial", "close"):
                with self.subTest(at=at, fault=fault):
                    data = self.manual(("1e53", *qtest.oracle.select(2), qtest.ESC), FAULT=f"{fault}{at}")
                    self.assertEqual((data["result"], data["internal"]), (1, 8))
                    self.assertEqual((data["encodes"], data["clears"], data["shutdowns"]), (at-1,)*3)

    def test_serializer_and_length_failures_never_encode_or_redraw(self):
        for at in (1, 2):
            for fault in ("header", "badlength"):
                with self.subTest(at=at, fault=fault):
                    data = self.manual(("1e53", *qtest.oracle.select(2), qtest.ESC), FAULT=f"{fault}{at}")
                    self.assertEqual((data["result"], data["internal"]), (1, 9))
                    self.assertEqual((data["encodes"], data["clears"], data["shutdowns"]), (at-1,)*3)

    def test_layout_count_mismatch_stops_before_read(self):
        for at in (1, 2):
            data = self.manual(("1e53", *qtest.oracle.select(2), qtest.ESC), FAULT=f"layoutcount{at}")
            self.assertEqual((data["result"], data["internal"], data["chunks"]), (1, 7, at-1))
            self.assertEqual((data["clears"], data["shutdowns"]), (at-1,)*2)

    def test_actual_size_change_at_start_and_selection(self):
        for at in (2, 3):
            self.path.write_bytes(wire_oracle.fixture(6000))
            data = self.manual(("1e53", *qtest.oracle.select(2), qtest.ESC), FAULT=f"sizegrow{at}")
            self.assertEqual((data["result"], data["internal"]), (1, 6))
            self.assertEqual((data["chunks"], data["clears"], data["shutdowns"]), (at-2,)*3)

    def test_nonoverlap_and_scratch_order_at_observed_boundaries(self):
        data, _ = self.check_frames(6000, [1, 3, 1])
        trace = data["trace"].split(",")
        self.assertEqual(trace[:4], ["filename", "size", "count", "poll"])
        for start, event in enumerate(trace):
            if event == "chunk":
                self.assertEqual(trace[start-1:start+3], ["layout", "chunk", "serialize", "encode"])
        self.assertNotIn("header", trace)  # No v1 header patching. / 日本語: v1ヘッダーの書き換えは行わない。

    def test_empty_enter_and_leading_zero_selection(self):
        data = self.manual(("1e53", *qtest.oracle.select("00003"), qtest.ENTER, qtest.ESC))
        self.assertEqual((data["result"], data["chunks"]), (0, 2))
        screen = [r for r in data["rows"] if r["kind"] == "display"][-1]
        self.assertEqual((screen["current"], screen["select"]), ("3/3        ", "00003"))

    def test_escape_immediately_after_start_and_while_editing(self):
        for keys in (("1e53", qtest.ESC), ("1e53", *qtest.oracle.digits("2"), qtest.ESC)):
            data = self.manual(keys)
            self.assertEqual((data["result"], data["chunks"], data["shutdowns"]), (0, 1, 1))


if __name__ == "__main__":
    unittest.main()
