# 実ファイル・実API・独立した転送列と画素の基準によるQMAN統合試験。
# ホスト模擬だけを確認し、生成PNGはPC-98の写真ではない。
# 外部QR実装は固定マスクの比較にだけ使う。
"""QMAN integration: actual binary files, real APIs, independent wire/pixel oracles.

Only host simulation is tested here; generated PNGs are not PC-98 photographs.
The external QR implementation is used solely as a forced-mask test oracle.
"""
from functools import lru_cache
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest
import tempfile
import sys

import cv2
import numpy as np
import zxingcpp

import qr_v40_oracle as oracle
import test_qr_status_probe as font  # Import no TestCase classes into this module. / 日本語: このモジュールへTestCaseクラスを直接取り込まない。

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1i"
ENTER, BS, ESC = "1c0d", "0e08", "001b"
UNCHANGED, EDITED, REJECTED, SELECTED, EXIT, ARGUMENT = range(6)
NONE, RANGE, OVERFLOW, TOO_LONG = range(4)
LABELS = ["current_label", "current", "select_label", "select"]
DRAW = ["clear", "qr", *LABELS]
START = ["size", "count", "info", "start_poll", "size", "state_init", "offset", "chunk", "header", "encode", "init", *DRAW, "show"]
KEY = ["poll", "classify", "input"]
SWITCH = ["size", "offset", "chunk", "header", "encode", *DRAW]


def digits(text):
    return [f"{(10 if c == '0' else int(c)):02x}{ord(c):02x}" for c in text]


def select(text):
    return [*digits(str(text)), ENTER]


@lru_cache(maxsize=8)
def original_file(size):
    # 試験データは全256種類のバイト値を含む独立パターンとする。
    # 65536離れた位置も区別する。
    # 読み取り結果や符号化器出力から期待値を作らない。
    # Independent fixture bytes include all 256 values and distinguish offsets
    # separated by 65536. Neither reads nor encoder output construct expectations.
    i = np.arange(size, dtype=np.uint64)
    return ((37*i + 11*(i >> 8) + 19*(i >> 16) + 3) & 255).astype(np.uint8).tobytes()


def sparse_chunks():
    return {0: original_file(2944), 65534: original_file(2944)[::-1]}


def expected_wire(size, index):
    count = (size + 2943) // 2944
    offset = index * 2944
    assert 0 <= index < count
    length = min(2944, size-offset)
    if size <= 1474560:
        payload = original_file(size)[offset:offset+length]
    else:
        # スパース試験ファイルの先頭と末尾の非ゼロchunkを独立に指定する。
        # Sparse fixture has independently specified first/last nonzero chunks.
        payload = sparse_chunks().get(index, bytes(length))
    return b"P98Q\x01" + index.to_bytes(2, "big") + count.to_bytes(2, "big") + payload


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_MANUAL_FILE_DRIVER")
    if override:
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_manual_file_probe"
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "tests/manual_file_probe_main.c", "src/manual_selection.c",
                    "src/qr_encode.c", "src/frame_header.c", "src/frame_count.c",
                    "src/frame_offset.c", "src/filename_v2.c", "src/frame_v2.c",
                    "-o", str(target)],
                   cwd=ROOT, check=True)
    return target


def capture(events, fault="none", size=70000, path=None, startup=("1e53",), **settings):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
        if path is None:
            path = Path(directory) / "unshortened binary fixture.bin"
            if size <= 1474560:
                path.write_bytes(original_file(size))
            else:
                with path.open("wb") as stream:
                    stream.truncate(size)  # Real sparse file for wire-count boundaries. / 日本語: 転送総数の境界検査に実際のスパースファイルを使う。
                    for index, payload in sparse_chunks().items():
                        if (index+1)*2944 <= size:
                            stream.seek(index*2944)
                            stream.write(payload)
        process = subprocess.run([str(driver()), "capture", str(path), " ".join([*startup, *events]), fault],
                                 env={**{k: v for k, v in os.environ.items() if not k.startswith("QMAN_")},
                                      **{"QMAN_"+k: str(v) for k, v in settings.items()}},
                                 cwd=ROOT, capture_output=True, timeout=120)
    if process.returncode < 0 or (process.returncode == 0 and process.stderr):
        raise AssertionError(f"exit {process.returncode}: {process.stderr.decode(errors='replace')}")
    if b"Sanitizer" in process.stderr or b"runtime error:" in process.stderr:
        raise AssertionError(process.stderr.decode(errors="replace"))
    stream = io.BytesIO(process.stdout)

    def take(size):
        data = stream.read(size)
        assert len(data) == size
        return data

    data = {"rows": [], "exit": process.returncode, "size": size, "stderr": process.stderr, "path": str(path)}
    while line := stream.readline():
        row = json.loads(line)
        if row["kind"] == "summary":
            data["info"] = row
            assert not stream.read()
        elif row["kind"] == "startup":
            data["startup"] = row
            data["information"] = take(row["length"])
        else:
            assert row["kind"] == "snapshot"
            row["planes"] = np.frombuffer(take(4 * 32000), np.uint8).reshape(4, 400, 80)
            row["matrix"] = take(3917)
            buffer = take(2953)
            row.update(json.loads(stream.readline()))
            row["wire"] = buffer[:row["complete_length"]]
            data["rows"].append(row)
    assert data["info"]["result"] == process.returncode
    assert data["info"]["snapshots"] == len(data["rows"])
    return data


def unpack_matrix(packed):
    return np.unpackbits(np.frombuffer(packed, np.uint8))[:31329].reshape(177, 177)


@lru_cache(maxsize=192)
def reference_matrix(size, index, packed):
    actual = unpack_matrix(packed)
    positions, _ = oracle.format_positions()
    word = sum(int(actual[y, x]) << bit for bit, (x, y) in enumerate(positions))
    mask = ((word ^ 0x5412) >> 10) & 7
    assert word == oracle.format_word(mask)  # ECC L, BCH and format XOR. / 日本語: ECC L、BCH、形式情報のXORを適用する。
    reference = oracle.reference(expected_wire(size, index), mask)
    assert reference.version == 40
    expected = np.asarray(reference.get_matrix(), np.uint8)
    np.testing.assert_array_equal(actual, expected)
    assert np.packbits(expected.reshape(-1)).tobytes() == packed
    return expected


def overlay(pixels, text, y):
    for cell, character in enumerate(text):
        pixels[y:y+16, 520+cell*8:528+cell*8] = (1-font.glyph(character))*255


@lru_cache(maxsize=512)
def expected_planes(size, index, packed, selection):
    pixels = np.full((400, 640), 255, np.uint8)
    qr = reference_matrix(size, index, packed)
    pixels[23:377, 143:497] = (1-qr).repeat(2, 0).repeat(2, 1)*255
    for text, y in (("CURRENT", 32), (f"{index+1}/{(size+2943)//2944}".ljust(11), 48),
                    ("SELECT", 80), (selection, 96)):
        overlay(pixels, text, y)
    return np.packbits(pixels == 255, axis=1)


@lru_cache(maxsize=512)
def decode(plane):
    pixels = np.unpackbits(np.frombuffer(plane, np.uint8).reshape(400, 80), axis=1)*255
    result = zxingcpp.read_barcode(pixels, formats=zxingcpp.BarcodeFormat.QRCode,
                                  try_downscale=False)
    assert result is not None and result.valid and isinstance(result.bytes, bytes)
    return result.bytes


def info_screen(path, size):
    count = (size + 2943) // 2944
    return (f"PC98QR - Manual Transfer\r\n\r\nFile       : {path}\r\n"
            f"File size  : {size} bytes\r\nQR frames  : {count}\r\n"
            f"Frame range: 1 - {count}\r\n\r\nS   : Start\r\nEsc : Cancel\r\n").encode()


class ManualFileTests(unittest.TestCase):
    def check_success(self, data):
        self.assertEqual(data["exit"], 0)
        info, rows, size = data["info"], data["rows"], data["size"]
        for name in ("inits", "shows", "shutdowns"):
            self.assertEqual(info[name], 1)
        selected = sum(row["result"] == SELECTED for row in rows)
        edited = sum(row["result"] == EDITED for row in rows)
        for name in ("headers", "encodes", "clears", "redraws", "chunks", "offsets"):
            self.assertEqual(info[name], selected + 1)
        self.assertEqual(info["texts"], 4*(selected+1) + edited)
        self.assertEqual(info["edits"], edited)
        self.assertEqual(info["counts"], 1)
        self.assertEqual(info["sizes"], selected + 2)
        self.assertEqual(data["startup"]["internal"], 0)  # FINISHED, distinct from CANCELLED. / 日本語: 開始前のCANCELLEDとは異なる、開始後のFINISHED。
        self.assertEqual(data["information"], info_screen(data["path"], size))
        self.assertEqual(info["io"][0], 2*(selected+1)+1)
        self.assertEqual(info["io"][1], 2*(selected+1)+1)
        self.assertEqual(info["io"][3], 2*(selected+1)+1)
        matrices = {}
        for at, row in enumerate(rows):
            index = row["displayed_index"]
            self.assertEqual(row["encoded_index"], index)
            self.assertEqual(row["wire"][:9], expected_wire(size, index)[:9])
            self.assertEqual(row["wire"][9:], expected_wire(size, index)[9:])
            self.assertEqual(row["wire"], expected_wire(size, index))
            self.assertEqual(row["current"], f"{index+1}/{(size+2943)//2944}".ljust(11))
            self.assertEqual(row["select"], row["spelling"])
            self.assertEqual(len(row["select"]), 5)
            self.assertEqual(row["length"], len(row["digits"]))
            self.assertEqual(row["matrix"][-1] & 127, 0)
            if index in matrices:
                self.assertEqual(row["matrix"], matrices[index])
            matrices[index] = row["matrix"]
            expected = expected_planes(size, index, row["matrix"], row["select"])
            for plane in row["planes"]:
                np.testing.assert_array_equal(plane, expected)
            self.assertEqual(decode(row["planes"][0].tobytes()), expected_wire(size, index))
            pixels = np.unpackbits(row["planes"][0], axis=1)
            for area in (pixels[15:23, 135:505], pixels[377:385, 135:505],
                         pixels[23:377, 135:143], pixels[23:377, 497:505], pixels[:, 512:520]):
                self.assertTrue(np.all(area == 1))
            if row["initial"] or row["result"] == SELECTED:
                black = int(np.count_nonzero(unpack_matrix(row["matrix"])))
                self.assertEqual(row["writes"], [32000 + black*4 + 464]*4)
            elif row["result"] == EDITED:
                self.assertEqual(row["writes"], [80]*4)
            else:
                self.assertEqual(row["writes"], [0]*4)
            if at:
                previous = rows[at-1]
                if row["result"] != SELECTED:
                    for name in ("sizes", "chunks", "offsets", "encodes", "clears"):
                        self.assertEqual(row[name], previous[name])
                    self.assertEqual(row["matrix"], previous["matrix"])
                    self.assertEqual(row["wire"], previous["wire"])
                    self.assertEqual(row["current"], previous["current"])
                    outside = np.ones((400, 80), bool)
                    if row["result"] == EDITED:
                        outside[96:112, 65:70] = False
                    for p in range(4):
                        np.testing.assert_array_equal(row["planes"][p][outside], previous["planes"][p][outside])
        return data

    def run_keys(self, *events, size=70000, fault="none"):
        return self.check_success(capture([*events, ESC], fault=fault, size=size))

    def committed(self, text, index):
        data = self.run_keys(*select(text))
        row = data["rows"][-2]
        self.assertEqual((row["result"], row["selected"], row["displayed_index"]),
                         (SELECTED, index+1, index))
        self.assertEqual((row["digits"], row["length"], row["error"]), ("", 0, NONE))
        self.assertEqual(row["select"], str(text).ljust(5))
        return data

    def test_initial_real_file_frame(self):
        row = self.run_keys()["rows"][0]
        self.assertEqual((row["displayed_index"], row["selected"], row["select"]), (0, 0, "-    "))
        self.assertEqual(row["wire"][:9].hex(), "503938510100000018")
        self.assertTrue({0, 26, 128, 255, 13, 10} <= set(row["wire"][9:]))

    def test_all_required_sizes_last_lengths_and_return_to_long(self):
        for size, count, offset, payload, complete in (
                (1, 1, 0, 1, 10), (2944, 1, 0, 2944, 2953),
                (2945, 2, 2944, 1, 10), (6000, 3, 5888, 112, 121),
                (70000, 24, 67712, 2288, 2297), (1474560, 501, 1472000, 2560, 2569)):
            with self.subTest(size=size):
                data = self.run_keys(*select(count), *select(1), size=size)
                row = next(r for r in data["rows"] if r["result"] == SELECTED)
                self.assertEqual((row["offset"], len(row["wire"])-9, len(row["wire"])),
                                 (offset, payload, complete))
                self.assertEqual(row["current"], f"{count}/{count}".ljust(11))
                for frame in (data["rows"][0], row):
                    index = frame["displayed_index"]
                    (ARTIFACTS / f"file-{size}-frame-{index}.bin").write_bytes(expected_wire(size, index))
                    pixels = np.unpackbits(frame["planes"][0], axis=1)*255
                    self.assertTrue(cv2.imwrite(str(ARTIFACTS / f"qman-host-{size}-{index}.png"), pixels))

    def test_6000_1_to_2_to_3_to_1_and_receiver(self):
        data = self.run_keys(*select(2), *select(3), *select(1), size=6000)
        frames = [data["rows"][0], *[r for r in data["rows"] if r["result"] == SELECTED]]
        self.assertEqual([r["displayed_index"] for r in frames], [0, 1, 2, 0])
        photos = []
        for row in frames:
            index = row["displayed_index"]
            photo = ARTIFACTS / f"qman-host-6000-{index}.png"
            self.assertTrue(cv2.imwrite(str(photo), np.unpackbits(row["planes"][0], axis=1)*255))
            photos.append(photo)
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
            out = Path(directory) / "restored.bin"
            process = subprocess.run([sys.executable, str(ROOT/"windows/reassemble_photos.py"),
                                      "--output", str(out), str(photos[2]), str(photos[0]),
                                      str(photos[1]), str(photos[3])], capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(out.read_bytes(), original_file(6000))
            self.assertEqual(hashlib.sha256(out.read_bytes()).digest(), hashlib.sha256(original_file(6000)).digest())

    def test_formatter_full_domain_and_guards(self):
        result = subprocess.run([str(driver()), "format"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertIn(b"196605", result.stdout)

    def test_maximum_wire_count_current_and_long_to_short(self):
        data = self.run_keys(*select(65535), *select(1), size=192935040)
        commits = [r for r in data["rows"] if r["result"] == SELECTED]
        self.assertEqual([r["current"] for r in commits], ["65535/65535", "1/65535    "])

    def test_1_enter(self):
        self.committed(1, 0)

    def test_2_enter(self):
        self.committed(2, 1)

    def test_23_enter(self):
        self.committed(23, 22)

    def test_24_enter(self):
        self.committed(24, 23)

    def test_leading_zero_spelling_survives_redraw(self):
        self.committed("00023", 22)

    def test_edit_23_keeps_initial_qr_and_current(self):
        rows = self.run_keys(*digits("23"))["rows"]
        self.assertEqual([r["select"] for r in rows[1:-1]], ["2    ", "23   "])
        self.assertTrue(all(r["displayed_index"] == 0 for r in rows))

    def test_rejected_25_preserves_selected_frame(self):
        row = self.run_keys(*select(23), *select(25))["rows"][-2]
        self.assertEqual((row["result"], row["error"], row["selected"], row["displayed_index"]),
                         (REJECTED, RANGE, 23, 22))
        self.assertEqual(row["select"], "25   ")

    def test_range_and_overflow_boundaries(self):
        for text, error in (("0", RANGE), ("00000", RANGE), ("25", RANGE),
                            ("65535", RANGE), ("65536", OVERFLOW), ("99999", OVERFLOW)):
            with self.subTest(text=text):
                row = self.run_keys(*select(text))["rows"][-2]
                self.assertEqual((row["result"], row["error"], row["displayed_index"]), (REJECTED, error, 0))

    def test_rejected_25_backspace_correction_to_24(self):
        row = self.run_keys(*select(25), BS, *select(4))["rows"][-2]
        self.assertEqual((row["displayed_index"], row["select"]), (23, "24   "))

    def test_sixth_digit_latches_rejection(self):
        rows = self.run_keys(*digits("000231"), ENTER, ENTER, *digits("4"))["rows"]
        for row in rows[6:-1]:
            self.assertEqual((row["select"], row["error"], row["displayed_index"]), ("00023", TOO_LONG, 0))
        self.assertEqual([r["result"] for r in rows[6:-1]], [REJECTED, REJECTED, REJECTED, UNCHANGED])

    def test_too_long_backspace_deletes_saved_digit_and_recovers(self):
        rows = self.run_keys(*digits("000231"), ENTER, BS, ENTER)["rows"]
        self.assertEqual((rows[-3]["digits"], rows[-3]["error"]), ("0002", NONE))
        self.assertEqual((rows[-2]["displayed_index"], rows[-2]["select"]), (1, "0002 "))

    def test_new_digits_replace_committed_spelling_only(self):
        row = self.run_keys(*select("00023"), *digits("4"))["rows"][-2]
        self.assertEqual((row["select"], row["current"], row["displayed_index"]), ("4    ", "23/24      ", 22))

    def test_backspace_erases_old_select_cells(self):
        rows = self.run_keys(*digits("00023"), *([BS]*5))["rows"]
        self.assertEqual(rows[-2]["select"], "-    ")
        np.testing.assert_array_equal(rows[-2]["planes"], rows[0]["planes"])

    def test_backspace_immediately_after_commit_is_unchanged(self):
        row = self.run_keys(*select("00023"), BS)["rows"][-2]
        self.assertEqual((row["result"], row["select"]), (UNCHANGED, "00023"))

    def test_same_number_retyped_regenerates(self):
        data = self.run_keys(*select(23), *select(23), *select("00023"))
        commits = [r for r in data["rows"] if r["result"] == SELECTED]
        self.assertEqual(data["info"]["encodes"], 4)
        self.assertEqual(len(commits), 3)
        self.assertEqual(commits[0]["matrix"], commits[2]["matrix"])
        self.assertEqual(commits[2]["select"], "00023")

    def test_empty_enter_after_commit_does_not_regenerate(self):
        data = self.run_keys(*([ENTER]*3), *select(23), *([ENTER]*8))
        self.assertEqual(data["info"]["encodes"], 2)
        self.assertTrue(all(r["result"] == UNCHANGED for r in data["rows"][-9:-1]))

    def test_24_to_2_to_1_erases_qr_and_current(self):
        data = self.run_keys(*select(24), *select(2), *select(1))
        commits = [r for r in data["rows"] if r["result"] == SELECTED]
        self.assertEqual([r["current"] for r in commits], ["24/24      ", "2/24       ", "1/24       "])
        for previous, new in zip(commits, commits[1:]):
            vanished = (unpack_matrix(previous["matrix"]) == 1) & (unpack_matrix(new["matrix"]) == 0)
            self.assertTrue(np.any(vanished))
            for plane in new["planes"]:
                pixels = np.unpackbits(plane, axis=1)
                self.assertTrue(np.all(pixels[23:377, 143:497][vanished.repeat(2, 0).repeat(2, 1)] == 1))
            self.assertTrue(np.all(new["planes"][:, 48:64, 69] == 255))

    def test_all_24_real_frames(self):
        data = self.run_keys(*(event for number in range(1, 25) for event in select(number)))
        commits = [r for r in data["rows"] if r["result"] == SELECTED]
        self.assertEqual([r["displayed_index"] for r in commits], list(range(24)))
        self.assertEqual(len({r["wire"] for r in commits}), 24)
        self.assertEqual(len({r["matrix"] for r in commits}), 24)
        self.assertNotEqual(original_file(70000)[67712:70000], original_file(70000)[2176:4464])

    def test_reverse_order_and_revisit_are_deterministic(self):
        numbers = [24, 23, 2, 1, 24, 2, 23, 1]
        data = self.run_keys(*(event for number in numbers for event in select(number)))
        self.assertEqual([r["displayed_index"] for r in data["rows"] if r["result"] == SELECTED],
                         [n-1 for n in numbers])

    def test_exact_lifecycle_and_redraw_order(self):
        data = self.run_keys(*select(23), *select(2))
        expected = START + (KEY+["edit"])*2 + KEY+SWITCH + KEY+["edit"] + KEY+SWITCH + KEY+["shutdown"]
        self.assertEqual(data["info"]["trace"].split(','), expected)

    def test_empty_polls_and_stale_peek_escape(self):
        data = self.run_keys("-", *digits("2"), "-", ENTER, "-")
        self.assertEqual((data["info"]["polls"], data["info"]["reads"]), (6, 3))
        self.assertEqual(data["rows"][-2]["displayed_index"], 1)

    def test_keypad_digits(self):
        row = self.run_keys("4e30", "4b32", "4c33", ENTER)["rows"][-2]
        self.assertEqual((row["select"], row["displayed_index"]), ("023  ", 22))

    def test_unrelated_and_control_alias_keys(self):
        events = ["1e61", "6200", "0f09", "2d0d", "2308", "1b1b", "000d", "01c7"]
        rows = self.run_keys(*digits("23"), *events, ENTER)["rows"]
        self.assertTrue(all(r["result"] == UNCHANGED for r in rows[3:-2]))
        self.assertEqual(rows[-2]["displayed_index"], 22)

    def test_repeated_digits_and_autorepeat_not_deduplicated(self):
        self.committed("22", 21)
        row = self.run_keys(*(digits("2")*12), ENTER)["rows"][-2]
        self.assertEqual((row["select"], row["error"], row["displayed_index"]), ("22222", TOO_LONG, 0))

    def test_queued_events_and_startup_enter_are_not_flushed(self):
        data = self.run_keys(ENTER, ENTER, *select(2), ENTER, *select(3))
        self.assertEqual([r["selected"] for r in data["rows"] if r["result"] == SELECTED], [2, 3])

    def test_escape_from_initial_edit_selected_and_rejected_states(self):
        for events in ([], digits("2"), select(23), select(25), digits("000231")):
            with self.subTest(events=events):
                data = self.run_keys(*events)
                self.assertEqual(data["rows"][-1]["result"], EXIT)

    def test_escape_does_not_consume_later_events(self):
        data = self.check_success(capture([ESC, *select(24)]))
        self.assertEqual((data["info"]["polls"], data["info"]["reads"]), (1, 1))

    def fault(self, fault, code, events=(), size=70000, path=None):
        data = capture([*events, ESC, *select(24)], fault, size=size, path=path)
        self.assertEqual(data["exit"], code)
        info = data["info"]
        self.assertEqual(info["shutdowns"], info["inits"])
        self.assertEqual(info["trace"].split(",")[-1], "error")
        if info["inits"]:
            self.assertEqual(info["trace"].split(",")[-2], "shutdown")
        self.assertTrue(data["stderr"].startswith((b"Error: ", b"Usage: ")))
        self.assertTrue(data["stderr"].endswith(b"\r\n"))
        return data

    def test_invalid_cli_and_empty_path(self):
        for fault, path in (("usage_none", None), ("usage_extra", None), ("none", "")):
            with self.subTest(fault=fault):
                data = self.fault(fault, 1, path=path)
                self.assertEqual(data["info"]["trace"], "error")
                self.assertEqual(data["info"]["io"], [0, 0, 0, 0])

    def test_missing_file(self):
        data = self.fault("none", 2, path=ARTIFACTS/"absent-file.bin")
        self.assertEqual(data["info"]["io"], [1, 0, 0, 0])

    def test_empty_file_and_excess_wire_count(self):
        for size in (0, 192935041):
            with self.subTest(size=size):
                data = self.fault("none", 3, size=size)
                self.assertEqual(data["info"]["trace"], "size,count,error")
                self.assertEqual(data["info"]["io"], [1, 1, 0, 1])

    def test_initialization_header_encoder_and_offset_failures(self):
        for fault, code, trace in (("init", 4, START[:6]), ("offset1", 6, START[:7]),
                                  ("offset_end1", 6, START[:7]), ("header1", 8, START[:9]),
                                  ("encoder1", 9, START[:10])):
            with self.subTest(fault=fault):
                data = self.fault(fault, code)
                self.assertEqual(data["info"]["inits"], 0)
                self.assertEqual(data["info"]["trace"].split(","), trace+["error"])

    def test_size_io_failures_initial_and_runtime(self):
        for number in (1, 2, 3):
            for operation in ("open", "seek", "close"):
                with self.subTest(number=number, operation=operation):
                    data = self.fault(f"size_{operation}{number}", 2, select(2) if number == 3 else [])
                    self.assertEqual(data["info"]["chunks"], int(number == 3))
                    self.assertEqual(data["info"]["io"][3], (0, 1, 3)[number-1]+(operation != "open"))
                    self.assertEqual(data["info"]["clears"], int(number == 3))
                    if number == 3: self.assert_preserved(data)

    def test_chunk_io_failures_initial_and_runtime(self):
        for number in (1, 2):
            for operation in ("open", "seek", "wrong_seek", "read", "partial_error", "close", "short", "zero"):
                with self.subTest(number=number, operation=operation):
                    data = self.fault(f"chunk_{operation}{number}", 7, select(2) if number == 2 else [])
                    self.assertEqual(data["info"]["headers"], number-1)
                    self.assertEqual(data["info"]["io"][3], 2*number+1-(operation == "open"))
                    self.assertEqual(data["info"]["clears"], number-1)
                    if number == 2: self.assert_preserved(data)

    def test_positive_partial_reads_continue_to_capacity_and_final_eof(self):
        data = self.run_keys(*select(3), *select(1), size=6000, fault="partial")
        self.assertEqual(data["info"]["io"][2], 63 + 4 + 63)

    def assert_preserved(self, data):
        last, previous = data["rows"][-1], data["rows"][-2]
        np.testing.assert_array_equal(last["planes"], previous["planes"])
        self.assertEqual(last["current"], previous["current"])
        self.assertEqual(last["clears"], previous["clears"])
        self.assertEqual(last["displayed_index"], previous["displayed_index"])

    def test_file_delete_shrink_and_grow_at_recheck(self):
        for fault, code in (("delete", 2), ("shrink", 5), ("grow", 5)):
            with self.subTest(fault=fault):
                data = self.fault(fault, code, select(1), size=6000)
                self.assert_preserved(data)
                self.assertEqual(data["info"]["chunks"], 1)

    def test_file_change_after_size_before_final_read(self):
        for fault in ("delete_after_size", "shrink_after_size", "grow_after_size"):
            with self.subTest(fault=fault):
                data = self.fault(fault, 7, select(3), size=6000)
                self.assert_preserved(data)
                self.assertEqual(data["info"]["headers"], 1)

    def test_runtime_prepare_failures_preserve_screen(self):
        for fault, code in (("offset2", 6), ("offset_end2", 6), ("header2", 8), ("encoder2", 9)):
            with self.subTest(fault=fault):
                self.assert_preserved(self.fault(fault, code, select(2)))

    def test_encode_failure_after_successful_switch(self):
        data = self.fault("encoder3", 9, [*select(24), *select(2)])
        self.assert_preserved(data)
        self.assertEqual(data["info"]["clears"], 2)

    def test_each_initial_and_runtime_status_failure(self):
        for number in (1, 2):
            for step, role in enumerate(LABELS):
                with self.subTest(number=number, role=role):
                    data = self.fault(f"{role}{number}", 10, select(2) if number == 2 else [])
                    info, last = data["info"], data["rows"][-1]
                    self.assertEqual(info["redraws"], number-1)
                    self.assertEqual(info["shows"], int(number > 1))
                    index = last["encoded_index"]
                    pixels = np.full((400, 640), 255, np.uint8)
                    qr = reference_matrix(70000, index, last["matrix"])
                    pixels[23:377, 143:497] = (1-qr).repeat(2, 0).repeat(2, 1)*255
                    strings = [("CURRENT", 32), (f"{index+1}/24".ljust(11), 48),
                               ("SELECT", 80), (last["spelling"], 96)]
                    for text, y in strings[:step]: overlay(pixels, text, y)
                    for plane in last["planes"]:
                        np.testing.assert_array_equal(plane, np.packbits(pixels == 255, axis=1))

    def test_select_edit_failure_before_and_after_switch(self):
        for fault, events in (("edit1", digits("2")), ("edit2", [*select(2), *digits("3")])):
            with self.subTest(fault=fault):
                self.assert_preserved(self.fault(fault, 10, events))

    def test_bios_poll_and_input_api_failures(self):
        for fault, code, events in (("poll1", 11, []), ("none", 11, ["!02"]),
                                    ("none", 11, [*select(2), "!ff"]),
                                    ("input1", 12, digits("2")), ("input3", 12, [*select(2), *digits("3")])):
            with self.subTest(fault=fault, events=events):
                self.assert_preserved(self.fault(fault, code, events))

    def test_error_output_failure_preserves_exit_code(self):
        data = capture([ESC], fault="output", size=0)
        self.assertEqual(data["exit"], 3)
        self.assertEqual(data["stderr"], b"")
        self.assertEqual(data["info"]["inits"], 0)

    def test_information_all_sizes_cancel_without_payload_or_graphics(self):
        for size in (1, 2944, 2945, 6000, 70000, 1474560, 192935040):
            with self.subTest(size=size):
                data = capture([ESC], size=size, startup=())
                self.assertEqual(data["exit"], 0)
                self.assertEqual(data["startup"]["internal"], 1)  # CANCELLED / 日本語: 開始前のキャンセル。
                self.assertEqual(data["information"], info_screen(data["path"], size)+b"\r\nCancelled.\r\n")
                self.assertEqual(data["info"]["io"], [1, 1, 0, 1])
                for key in ("headers", "encodes", "inits", "shows", "shutdowns", "chunks", "offsets", "inputs"):
                    self.assertEqual(data["info"][key], 0)
                self.assertEqual(data["rows"], [])

    def test_uppercase_and_lowercase_start(self):
        for key in ("1e53", "1e73"):
            data = self.check_success(capture([ESC], size=6000, startup=(key,)))
            self.assertEqual(data["startup"]["consumed"], [int(key, 16), int(ESC, 16)])
            self.assertEqual((data["startup"]["polls"], data["startup"]["reads"]), (1, 1))
            self.assertEqual(data["rows"][0]["select"], "-    ")

    def test_information_ignores_enter_digits_backspace_and_other_keys(self):
        keys = [ENTER, *digits("23"), BS, "6200", "1e13", "1ec4", "000d", "1b1b", "1e53"]
        data = self.check_success(capture([ESC], startup=keys))
        self.assertEqual(data["startup"]["consumed"], [int(k, 16) for k in [*keys, ESC]])
        self.assertEqual(data["startup"]["reads"], len(keys))
        self.assertEqual(data["info"]["encodes"], 1)
        self.assertEqual(data["rows"][0]["select"], "-    ")

    def test_enter_repeat_and_empty_polls_before_start(self):
        keys = ["-", ENTER]*200 + ["1e53"]
        data = self.check_success(capture([ESC], startup=keys))
        self.assertEqual(data["startup"]["polls"], 401)
        self.assertEqual(data["startup"]["reads"], 201)

    def test_start_autorepeat_is_ignored_in_qr_loop(self):
        data = self.run_keys("1e53", "1e73", "1e53", ENTER)
        self.assertEqual(data["info"]["encodes"], 1)
        self.assertTrue(all(r["result"] == UNCHANGED for r in data["rows"][1:-1]))
        self.assertEqual(data["startup"]["reads"], 1)

    def test_start_queued_digits_enter_and_escape_in_consumption_order(self):
        events = [*select(2), ESC, *select(3)]
        data = self.check_success(capture(events, size=6000))
        self.assertEqual(data["rows"][0]["select"], "-    ")
        self.assertEqual(data["rows"][-2]["displayed_index"], 1)
        self.assertEqual(data["startup"]["consumed"], [0x1e53, *[int(k, 16) for k in [*select(2), ESC]]])

    def test_start_immediate_escape_displays_frame_zero_once(self):
        data = self.run_keys()
        self.assertEqual(data["info"]["encodes"], 1)
        self.assertEqual(data["info"]["shows"], 1)
        self.assertEqual(data["startup"]["consumed"], [0x1e53, 0x001b])

    def test_cancel_does_not_consume_start_or_later_keys(self):
        data = capture([ESC, "1e53", *select(2)], startup=())
        self.assertEqual(data["exit"], 0)
        self.assertEqual(data["startup"]["consumed"], [0x001b])
        self.assertEqual(data["info"]["sizes"], 1)

    def test_startup_empty_poll_stale_escape_does_not_cancel(self):
        data = self.check_success(capture([ESC], startup=("-", "-", "1e53")))
        self.assertEqual(data["startup"]["polls"], 3)
        self.assertEqual(data["startup"]["reads"], 1)

    def test_startup_bios_error_stops_before_size_recheck(self):
        for keys, fault in ((("!02", "1e53"), "none"), (("1e53",), "start_poll")):
            data = capture([ESC], startup=keys, fault=fault)
            self.assertEqual(data["exit"], 11)
            self.assertEqual(data["startup"]["reads"], 0)
            self.assertEqual(data["info"]["sizes"], 1)
            self.assertEqual(data["info"]["inits"], 0)
            self.assertEqual(data["info"]["shutdowns"], 0)

    def test_size_changed_or_deleted_while_waiting_never_reads_payload(self):
        for fault, code in (("grow_start", 5), ("shrink_start", 5), ("delete_start", 2)):
            with self.subTest(fault=fault):
                data = self.fault(fault, code, size=6000)
                self.assertEqual(data["information"], info_screen(data["path"], 6000))
                self.assertEqual(data["info"]["sizes"], 2)
                self.assertEqual(data["info"]["counts"], 1)
                for name in ("chunks", "offsets", "headers", "encodes", "inits", "shutdowns"):
                    self.assertEqual(data["info"][name], 0)
                self.assertEqual(data["startup"]["consumed"], [0x1e53])

    def test_same_size_content_change_is_not_identity_detection(self):
        # 契約上の制限を検証するため、情報表示後に先頭バイトを実際に変える。
        # サイズが同じなら拒否されない。
        # 期待バイトも独立に変更する。
        # Contract limitation: the first byte really changes after the prompt.
        # Equal size does not reject it. Expected bytes are independently altered.
        data = capture([ESC], size=6000, fault="same_size_change")
        self.assertEqual(data["exit"], 0)
        expected = bytearray(expected_wire(6000, 0)); expected[9] = 0xF3
        self.assertEqual(data["rows"][0]["wire"], bytes(expected))
        self.assertEqual(decode(data["rows"][0]["planes"][0].tobytes()), bytes(expected))

    def test_frame_count_api_failure(self):
        data = self.fault("count", 3)
        self.assertEqual(data["information"], b"")
        self.assertEqual(data["startup"]["reads"], 0)
        self.assertEqual(data["info"]["trace"], "size,count,error")

    def test_current_formatter_failure_before_init_and_during_switch(self):
        # 符号化後の総数を人工的に壊す処理は試験ハーネスだけに置く。
        # Synthetic corruption of the live count after encode, ONLY in harness.
        for number in (1, 2):
            data = self.fault(f"format{number}", 10, select(2) if number == 2 else [])
            self.assertEqual(data["info"]["inits"], number-1)
            self.assertEqual(data["info"]["clears"], number-1)
            if number == 2: self.assert_preserved(data)

    def test_information_short_writes_preserve_all_bytes(self):
        for limit in (1, 2, 7):
            self.check_success(capture([ESC], size=6000, OUTPUT_LIMIT=limit))

    def test_information_failure_at_every_byte_stops_before_poll(self):
        # 固定パスで正確な出力長を安定させる。
        # サイズ取得・openの失敗は別途検証し、ここでは同じ実ファイルを使い続ける。
        # A fixed explicit path keeps the exact output length stable. Size/open
        # failure is separately tested; here use one actual fixture throughout.
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
            path = Path(directory)/"T6000.BIN"; path.write_bytes(original_file(6000))
            expected = info_screen(str(path), 6000)
            for zero in (False, True):
                for budget in range(len(expected)):
                    with self.subTest(zero=zero, budget=budget):
                        settings = {"OUTPUT_ZERO": 1} if zero else {}
                        data = capture([ESC], size=6000, path=path, OUTPUT_BUDGET=budget, **settings)
                        self.assertEqual(data["exit"], 13)
                        self.assertEqual(data["information"], expected[:budget])
                        self.assertEqual(data["startup"]["reads"], 0)
                        self.assertEqual(data["info"]["sizes"], 1)
                        self.assertEqual(data["info"]["inits"], 0)
                        self.assertEqual(data["info"]["shutdowns"], 0)
                        self.assertEqual(data["info"]["chunks"], 0)

    def test_cancellation_output_failure_returns_13_without_graphics(self):
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
            path = Path(directory)/"T6000.BIN"; path.write_bytes(original_file(6000))
            base = info_screen(str(path), 6000); tail = b"\r\nCancelled.\r\n"
            for zero in (False, True):
                for budget in range(len(tail)):
                    settings = {"OUTPUT_ZERO": 1} if zero else {}
                    data = capture([ESC, "1e53"], startup=(), size=6000, path=path,
                                   OUTPUT_BUDGET=len(base)+budget, **settings)
                    self.assertEqual(data["exit"], 13)
                    self.assertEqual(data["information"], base+tail[:budget])
                    self.assertEqual(data["startup"]["consumed"], [0x001b])
                    self.assertEqual(data["info"]["inits"], 0)
                    self.assertEqual(data["info"]["sizes"], 1)

    def test_diagnostic_output_failure_preserves_all_prior_error_codes(self):
        cases = [("usage_none", 1, ()), ("size_open1", 2, ()), ("count", 3, ()),
                 ("init", 4, ()), ("grow_start", 5, ()), ("offset1", 6, ()),
                 ("chunk_read1", 7, ()), ("header1", 8, ()), ("encoder1", 9, ()),
                 ("current1", 10, ()), ("start_poll", 11, ()), ("input1", 12, digits("2"))]
        for fault, code, events in cases:
            with self.subTest(fault=fault):
                data = capture([*events, ESC], fault=fault, ERROR_OUTPUT_FAIL=1)
                self.assertEqual(data["exit"], code)
                self.assertEqual(data["stderr"], b"")
                self.assertEqual(data["info"]["shutdowns"], data["info"]["inits"])
        data = capture([ESC], OUTPUT_BUDGET=0, ERROR_OUTPUT_FAIL=1)
        self.assertEqual(data["exit"], 13)
        self.assertEqual(data["stderr"], b"")

    def test_decimal_unsigned_long_boundaries(self):
        for value in (0, 1, 9, 10, 65535, 65536, 70000, 1474560, 4294967295, "max"):
            result = subprocess.run([str(driver()), "decimal", str(value)], capture_output=True)
            expected = (1 << (8*ctypes.sizeof(ctypes.c_ulong)))-1 if value == "max" else value
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, str(expected).encode(), b""))

    def test_public_api_rejects_invalid_arguments_before_any_side_effect(self):
        result = subprocess.run([str(driver()), "arguments"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"eight invalid arguments", result.stdout)

    def test_larger_caller_buffers_and_canaries(self):
        self.check_success(capture([*select(3), *select(1), ESC], size=6000, fault="large_buffers"))

    def test_binary_file_unmodified_by_start_and_cancel(self):
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as directory:
            path = Path(directory)/"unchanged.bin"; payload = original_file(6000); path.write_bytes(payload)
            for start in ((), ("1e53",)):
                self.assertEqual(capture([ESC], startup=start, path=path, size=6000)["exit"], 0)
                self.assertEqual(path.read_bytes(), payload)

    def test_required_70000_boundary_final_and_return_sequence(self):
        data = self.run_keys(*select(23), *select(24), *select(2))
        rows = [data["rows"][0], *[r for r in data["rows"] if r["result"] == SELECTED]]
        self.assertEqual([(r["displayed_index"], r["offset"], len(r["wire"])) for r in rows],
                         [(0, 0, 2953), (22, 64768, 2953), (23, 67712, 2297), (1, 2944, 2953)])


if __name__ == "__main__":
    unittest.main()
