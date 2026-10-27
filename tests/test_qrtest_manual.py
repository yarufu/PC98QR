# 実main・制御部・符号化器・生ファイルI/OによるQRTEST /M統合試験。
# v2の期待値は独立chunkから作る。
# QMANの画素補助は維持するが、そのmainやv1期待値を実装として使わない。
"""QRTEST /M integration, with real main/controller/encoder and raw file I/O.

V2 wire expectations consume independent chunks; QMAN's pixel utilities remain
unchanged. Its main and v1 expectations are not used as this implementation.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

import numpy as np
import test_manual_file_probe as oracle
import frame_v2_wire_oracle as wire_oracle

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1n/host"
ESC, ENTER = "001b", "1c0d"
WRAPPED = ("pc98qr_key_poll pc98qr_file_size pc98qr_frame_count_for_size "
           "pc98qr_frame_offset_for_index pc98qr_read_file_chunk "
           "pc98qr_frame_header_serialize pc98qr_manual_selection_init "
           "pc98qr_manual_selection_input qr_encode_v40l_bytes qr_encode_v1q_bytes "
           "pc98qr_status_text write open read close pc98qr_filename_v2_from_path "
           "pc98qr_frame_v2_count_for_size pc98qr_frame_v2_layout_for_index "
           "pc98qr_frame_v2_serialize").split()


def build_driver(target, extra=()):
    command = [*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99", "-O2",
               "-Wall", "-Wextra", "-Werror", *extra, "-Isrc",
               "tests/qrtest_manual_main.c", "src/manual_transfer.c",
               "src/manual_selection.c", "src/qr_encode.c", "src/frame_header.c",
               "src/file_size.c", "src/frame_count.c", "src/frame_offset.c",
               "src/file_chunk.c", "src/filename_v2.c", "src/frame_v2.c",
               *("-Wl,--wrap=" + name for name in WRAPPED),
               "-o", str(target)]
    subprocess.run(command, cwd=ROOT, check=True)
    return command


@lru_cache(maxsize=1)
def driver():
    if override := os.environ.get("PC98QR_QRTEST_MANUAL_DRIVER"):
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_qrtest_manual"
    build_driver(target)
    return target


def run(args, keys=(ESC,), *, cwd=None, **settings):
    args = list(args)
    # ホストの一時ディレクトリをDOSパスの字句検証へ渡さない。
    # Keep the host's temporary directory out of DOS lexical validation.
    if len(args) == 2 and args[0] == "/M" and isinstance(args[1], Path):
        cwd, args[1] = args[1].parent, args[1].name
    env = {k: v for k, v in os.environ.items() if not k.startswith("QRTEST_")}
    env.update({"QRTEST_" + k: str(v) for k, v in settings.items()})
    env["QRTEST_KEYS"] = " ".join(keys)
    process = subprocess.run([str(driver()), *map(str, args)], cwd=cwd or ROOT,
                             env=env, capture_output=True, timeout=120)
    if process.returncode not in (0, 1):
        raise AssertionError((process.returncode, process.stderr.decode(errors="replace")))
    if b"Sanitizer" in process.stderr or b"runtime error:" in process.stderr:
        raise AssertionError(process.stderr.decode(errors="replace"))
    rows = [json.loads(line) for line in process.stdout.splitlines()]
    summary = rows.pop()
    assert summary["kind"] == "summary" and summary["result"] == process.returncode
    summary["information"] = bytes.fromhex(summary["information"])
    summary.update(stderr=process.stderr, rows=rows)
    return summary


def info_screen(path, size, name=b"TEST.MAG"):
    count = (size + 1 + len(name) + 2943) // 2944
    return (f"PC98QR - Manual Transfer\r\n\r\nFile       : {path}\r\n"
            f"File size  : {size} bytes\r\nQR frames  : {count}\r\n"
            f"Frame range: 1 - {count}\r\n\r\nS   : Start\r\nEsc : Cancel\r\n").encode()


def expected_planes(wire, packed, current, selection):
    actual = oracle.unpack_matrix(packed)
    positions, _ = oracle.oracle.format_positions()
    word = sum(int(actual[y, x]) << bit for bit, (x, y) in enumerate(positions))
    mask = ((word ^ 0x5412) >> 10) & 7
    assert word == oracle.oracle.format_word(mask)
    qr = np.asarray(oracle.oracle.reference(wire, mask).get_matrix(), np.uint8)
    np.testing.assert_array_equal(actual, qr)
    assert np.packbits(qr.reshape(-1)).tobytes() == packed
    pixels = np.full((400, 640), 255, np.uint8)
    pixels[23:377, 143:497] = (1-qr).repeat(2, 0).repeat(2, 1)*255
    for text, y in (("CURRENT", 32), (current, 48), ("SELECT", 80), (selection, 96)):
        oracle.overlay(pixels, text, y)
    return np.packbits(pixels == 255, axis=1)


class QRTestManualTests(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ARTIFACTS)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "TEST.MAG"
        self.path.write_bytes(oracle.original_file(6000))

    def manual(self, keys=(ESC,), **settings):
        data = run(["/M", self.path], keys, **settings)
        self.assertEqual(data["manual"], 1)
        self.assertEqual(data["capacity"], [2954, 3917])
        self.assertEqual(data["waits"], 0)
        return data

    def check_frames(self, size, numbers, start="1e53", *, raw=None, **settings):
        raw = oracle.original_file(size) if raw is None else raw
        self.assertEqual(len(raw), size)
        self.path.write_bytes(raw)
        name = self.path.name.upper().encode("ascii")
        expected_frames = wire_oracle.split(raw, name)
        keys = [start, *(key for number in numbers[1:] for key in oracle.select(number)), ESC]
        data = self.manual(keys, **settings)
        self.assertEqual((data["result"], data["internal"], data["stderr"]), (0, 0, b""))
        self.assertEqual(data["information"], info_screen(self.path.name, size, name))
        self.assertEqual([data[n] for n in ("inits", "shows", "shutdowns")], [1, 1, 1])
        self.assertEqual(data["sizes"], len(numbers) + 1)
        self.assertEqual([data[n] for n in ("chunks", "encodes", "clears")], [len(numbers)] * 3)
        encoded = [r for r in data["rows"] if r["kind"] == "encode"]
        displayed = [r for r in data["rows"] if r["kind"] == "display"]
        self.assertEqual(len(encoded), len(numbers))
        self.assertEqual(len(displayed), len(numbers))
        for at, (number, frame, screen) in enumerate(zip(numbers, encoded, displayed)):
            with self.subTest(number=number, at=at):
                index = number - 1
                wire, matrix = bytes.fromhex(frame["wire"]), bytes.fromhex(frame["matrix"])
                self.assertEqual(wire, expected_frames[index])
                self.assertEqual(frame["offset"], 0 if index == 0 else index * 2944 - 1 - len(name))
                self.assertEqual(screen["length"], len(wire))
                self.assertEqual(screen["current"], f"{number}/{len(expected_frames)}".ljust(11))
                self.assertEqual(screen["select"], ("-" if at == 0 else str(number)).ljust(5))
                plane = bytes.fromhex(screen["plane"])
                expected = expected_planes(wire, matrix, screen["current"], screen["select"])
                np.testing.assert_array_equal(np.frombuffer(plane, np.uint8).reshape(400, 80), expected)
                self.assertEqual(oracle.decode(plane), wire)
                self.assertEqual(bytes.fromhex(frame["tail"])[-1:], b"\xa5")
        trace = data["trace"].split(",")
        self.assertLess(trace.index("encode"), trace.index("init"))
        self.assertLess(trace.index("status"), trace.index("show"))
        self.assertEqual(trace[-1], "shutdown")
        return data, encoded

    def test_information_escape_reads_no_qr_and_never_starts_graphics(self):
        data = self.manual()
        self.assertEqual((data["result"], data["internal"]), (0, 1))
        self.assertEqual(data["information"], info_screen(self.path.name, 6000) + b"\r\nCancelled.\r\n")
        self.assertEqual(data["sizes"], 1)
        self.assertEqual([data[n] for n in ("chunks", "encodes", "inits", "shows", "shutdowns")], [0]*5)

    def test_s_starts_first_frame(self):
        self.check_frames(6000, [1])

    def test_lowercase_s_starts_first_frame(self):
        self.check_frames(6000, [1], "1e73")

    def test_6000_1_2_3_1_complete_bytes_and_stale_tail_excluded(self):
        _, frames = self.check_frames(6000, [1, 2, 3, 1])
        short = frames[2]
        self.assertEqual(len(bytes.fromhex(short["wire"])), 130)
        # 以前の長いフレームの残りが、呼出し側領域に実際に残っている状態を作る。
        # 符号化長、独立行列、復号QRのいずれにも、その残りが含まれないことを確認する。
        # The preceding long frame really remains in caller storage, yet the
        # encoder length, independent matrix and decoded QR exclude that tail.
        self.assertEqual(bytes.fromhex(short["tail"])[:-1], bytes.fromhex(frames[1]["wire"])[130:])
        restored = b"".join(bytes.fromhex(f["wire"])[18 if i == 0 else 9:] for i, f in enumerate(frames[:3]))
        self.assertEqual(restored, self.path.read_bytes())
        self.assertTrue({0, 26, 128, 255, 13, 10} <= set(restored))

    def test_70000_1_23_24_2_wide_offsets_and_final_length(self):
        _, frames = self.check_frames(70000, [1, 23, 24, 2])
        self.assertEqual([f["offset"] for f in frames], [0, 64759, 67703, 2935])
        self.assertEqual([len(bytes.fromhex(f["wire"])) for f in frames], [2953, 2953, 2306, 2953])

    def test_single_byte_and_short_final_frame(self):
        for size, numbers in ((1, [1]), (2944, [1]), (2945, [1, 2, 1])):
            with self.subTest(size=size):
                self.check_frames(size, numbers)

    def test_same_static_buffer_as_legacy_file_mode(self):
        tiny = Path(self.directory.name) / "small.bin"
        tiny.write_bytes(b"\x00\x1a\x80\xff\r\n")
        data = self.manual(("1e53", ESC), REUSE_FILE=tiny)
        self.assertEqual((data["result"], data["reuse"]), (0, 1))

    def test_startup_ignores_digits_enter_and_backspace(self):
        data = self.manual((*oracle.select(2), "0e08", ESC))
        self.assertEqual((data["result"], data["internal"], data["encodes"], data["inits"]), (0, 1, 0, 0))

    def test_all_pre_graphics_errors_are_one(self):
        cases = {"size1": 3, "count1": 4, "key1": 12, "size2": 3,
                 "changed2": 6, "init1": 5, "offset1": 7, "read1": 8,
                 "short1": 8, "header1": 9, "encode1": 10}
        for fault, expected in cases.items():
            with self.subTest(fault=fault):
                data = self.manual(("1e53", ESC), FAULT=fault)
                self.assertEqual((data["result"], data["internal"]), (1, expected))
                self.assertEqual((data["inits"], data["shows"], data["shutdowns"]), (0, 0, 0))
                self.assertEqual(data["stderr"], b"Error: manual transfer failed.\r\n")

    def test_all_post_graphics_errors_shutdown_once(self):
        cases = {"status1": 11, "status4": 11, "status5": 11, "status6": 11,
                 "key2": 12, "input1": 13, "size3": 3, "changed3": 6,
                 "offset2": 7, "read2": 8, "short2": 8, "header2": 9, "encode2": 10}
        for fault, expected in cases.items():
            with self.subTest(fault=fault):
                data = self.manual(("1e53", *oracle.select(2), ESC), FAULT=fault)
                self.assertEqual((data["result"], data["internal"]), (1, expected))
                self.assertEqual((data["inits"], data["shutdowns"]), (1, 1))
                self.assertEqual(data["shows"], 0 if fault in ("status1", "status4") else 1)
                self.assertTrue(data["trace"].endswith("shutdown,error"))

    def test_empty_path_reaches_real_argument_validation(self):
        data = run(["/M", ""])
        self.assertEqual((data["manual"], data["result"], data["internal"], data["sizes"]), (1, 1, 2, 0))

    def test_missing_and_empty_files(self):
        for path in (self.path, self.path.with_name("missing.bin")):
            self.path.write_bytes(b"")
            data = run(["/M", path])
            self.assertEqual((data["manual"], data["result"], data["inits"]), (1, 1, 0))

    def test_output_failure_and_zero_are_errors_even_if_stderr_fails(self):
        for extra in ({}, {"STDOUT_ZERO": 1}, {"STDERR_FAIL": 1}, {"STDERR_ZERO": 1}):
            with self.subTest(extra=extra):
                data = self.manual(OUTPUT_BUDGET=0, **extra)
                self.assertEqual((data["result"], data["internal"], data["inits"]), (1, 14, 0))

    def test_cancel_message_output_failure_is_not_success(self):
        budget = len(info_screen(self.path.name, 6000))
        data = self.manual(OUTPUT_BUDGET=budget)
        self.assertEqual((data["result"], data["internal"]), (1, 14))

    def test_partial_stdout_and_stderr_writes(self):
        data = self.manual(WRITE_LIMIT=1)
        self.assertEqual((data["result"], data["internal"]), (0, 1))
        self.assertEqual(data["information"], info_screen(self.path.name, 6000) + b"\r\nCancelled.\r\n")
        data = self.manual(FAULT="size1", WRITE_LIMIT=1)
        self.assertEqual(data["result"], 1)
        self.assertEqual(data["stderr"], b"Error: manual transfer failed.\r\n")

    def test_legacy_modes_never_call_manual(self):
        small = self.path.with_name("small.bin")
        small.write_bytes(b"\x00\x1a\x80\xff\r\n")
        for args in (["/M"], ["HELLO"], ["/B", "001A80FF"], ["/F", small],
                     ["/T", small], ["/T", "1", self.path]):
            with self.subTest(args=args):
                data = run(args)
                self.assertEqual((data["result"], data["manual"]), (0, 0))
                self.assertEqual([data[n] for n in ("inits", "shows", "waits", "shutdowns")], [1]*4)
        self.path.write_bytes(oracle.original_file(70000))
        self.assertEqual(run(["/T", "22", self.path])["manual"], 0)

    def test_legacy_slash_m_decodes_as_text(self):
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True, stdout=subprocess.PIPE)
        image = subprocess.check_output([str(ROOT / "build/test_main_render"), "/M"])
        import cv2
        import zxingcpp
        result = zxingcpp.read_barcode(cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_GRAYSCALE))
        self.assertIsNotNone(result)
        self.assertEqual(result.bytes, b"/M")

    def test_invalid_cli_and_no_arguments_keep_legacy_rejection(self):
        for args in ([], ["/M", self.path, "EXTRA"], ["/m", self.path],
                     ["/MM", self.path], ["/X", self.path], ["/M", "", "EXTRA"]):
            with self.subTest(args=args):
                data = run(args)
                self.assertEqual((data["result"], data["manual"], data["inits"]), (1, 0, 0))
                self.assertTrue(data["stderr"].startswith(b"Usage: QRTEST"))


def mapping_case(value):
    def test(self):
        for extra in ({}, {"STDERR_FAIL": 1}, {"STDERR_ZERO": 1}):
            data = self.manual(RESULT=value, **extra)
            self.assertEqual(data["result"], 0 if value in (0, 1) else 1)
            self.assertEqual(data["internal"], value)
            self.assertEqual([data[n] for n in ("sizes", "chunks", "inits", "waits", "shutdowns")], [0]*5)
    return test


for _result in [*range(15), -1, 99]:
    setattr(QRTestManualTests, f"test_errorlevel_mapping_{_result}", mapping_case(_result))
