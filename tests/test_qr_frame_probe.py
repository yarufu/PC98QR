# 実入力・符号化と独立した転送列・画素基準によるQFRAME統合試験。
# ホスト模擬だけで、PNGは実機写真ではない。
# 外部QR実装は固定マスク比較専用。
"""QFRAME integration: real input/encoding, independent wire and pixel oracles.

Only host simulation is tested here; generated PNGs are not PC-98 photographs.
The external QR implementation is used solely as a forced-mask test oracle.
"""
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

import cv2
import numpy as np
import zxingcpp

import qr_v40_oracle as oracle
import test_qr_status_probe as font  # Import no TestCase classes into this module. / 日本語: このモジュールへTestCaseクラスを直接取り込まない。

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1f"
ENTER, BS, ESC = "1c0d", "0e08", "001b"
UNCHANGED, EDITED, REJECTED, SELECTED, EXIT, ARGUMENT = range(6)
NONE, RANGE, OVERFLOW, TOO_LONG = range(4)
LABELS = ["current_label", "current", "select_label", "select"]
DRAW = ["clear", "qr", *LABELS]
START = ["state_init", "header", "encode", "init", *DRAW, "show"]
KEY = ["poll", "classify", "input"]
SWITCH = ["header", "encode", *DRAW]
INITIAL_SHA = "1c3d4971bb24ec58e796da27e25ecb0aff11a3e7c149b21376d60dd275bbba73"


def digits(text):
    return [f"{(10 if c == '0' else int(c)):02x}{ord(c):02x}" for c in text]


def select(text):
    return [*digits(str(text)), ENTER]


def expected_wire(index):
    # 任意精度演算とBE16変換で独立した期待値を作る。
    # Cの出力や復号結果からコピーしない。
    # ここでのoffsetはフレーム内の位置。
    # Independent arbitrary-precision arithmetic and BE16 conversion, never
    # copied from the C program's output or a decoder. Offset is local to a frame.
    assert 0 <= index < 24
    header = b"P98Q" + bytes([1]) + index.to_bytes(2, "big") + (24).to_bytes(2, "big")
    return header + bytes((37 * i + 11 * (i // 256) + 3 + index) % 256 for i in range(2944))


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_FRAME_DRIVER")
    if override:
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_qr_frame_probe"
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "tests/qr_frame_probe_main.c", "src/manual_selection.c",
                    "src/qr_encode.c", "src/frame_header.c", "-o", str(target)],
                   cwd=ROOT, check=True)
    return target


def capture(events, fault="none"):
    process = subprocess.run([str(driver()), "capture", " ".join(events), fault],
                             cwd=ROOT, capture_output=True, timeout=120)
    if process.stderr or process.returncode < 0:
        raise AssertionError(f"exit {process.returncode}: {process.stderr.decode(errors='replace')}")
    stream = io.BytesIO(process.stdout)

    def take(size):
        data = stream.read(size)
        assert len(data) == size
        return data

    data = {"rows": [], "exit": process.returncode}
    while line := stream.readline():
        row = json.loads(line)
        if row["kind"] == "summary":
            data["info"] = row
            assert not stream.read()
        else:
            assert row["kind"] == "snapshot"
            row["planes"] = np.frombuffer(take(4 * 32000), np.uint8).reshape(4, 400, 80)
            row["matrix"] = take(3917)
            row["wire"] = take(2953)
            data["rows"].append(row)
    assert data["info"]["result"] == process.returncode
    assert data["info"]["snapshots"] == len(data["rows"])
    return data


def unpack_matrix(packed):
    return np.unpackbits(np.frombuffer(packed, np.uint8))[:31329].reshape(177, 177)


@lru_cache(maxsize=192)
def reference_matrix(index, packed):
    actual = unpack_matrix(packed)
    positions, _ = oracle.format_positions()
    word = sum(int(actual[y, x]) << bit for bit, (x, y) in enumerate(positions))
    mask = ((word ^ 0x5412) >> 10) & 7
    assert word == oracle.format_word(mask)  # ECC L, BCH and format XOR. / 日本語: ECC L、BCH、形式情報のXORを適用する。
    reference = oracle.reference(expected_wire(index), mask)
    assert reference.version == 40
    expected = np.asarray(reference.get_matrix(), np.uint8)
    np.testing.assert_array_equal(actual, expected)
    assert np.packbits(expected.reshape(-1)).tobytes() == packed
    return expected


def overlay(pixels, text, y):
    for cell, character in enumerate(text):
        pixels[y:y+16, 520+cell*8:528+cell*8] = (1-font.glyph(character))*255


@lru_cache(maxsize=512)
def expected_planes(index, packed, selection):
    pixels = np.full((400, 640), 255, np.uint8)
    qr = reference_matrix(index, packed)
    pixels[23:377, 143:497] = (1-qr).repeat(2, 0).repeat(2, 1)*255
    for text, y in (("CURRENT", 32), (f"{index+1}/24".ljust(5), 48),
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


class QFrameTests(unittest.TestCase):
    def check_success(self, data):
        self.assertEqual(data["exit"], 0)
        info, rows = data["info"], data["rows"]
        for name in ("inits", "shows", "shutdowns"):
            self.assertEqual(info[name], 1)
        selected = sum(row["result"] == SELECTED for row in rows)
        edited = sum(row["result"] == EDITED for row in rows)
        for name in ("headers", "encodes", "clears", "redraws"):
            self.assertEqual(info[name], selected + 1)
        self.assertEqual(info["texts"], 4*(selected+1) + edited)
        self.assertEqual(info["edits"], edited)
        matrices = {}
        for at, row in enumerate(rows):
            index = row["displayed_index"]
            self.assertEqual(row["encoded_index"], index)
            self.assertEqual(row["wire"][:9], expected_wire(index)[:9])
            self.assertEqual(row["wire"][9:], expected_wire(index)[9:])
            self.assertEqual(row["wire"], expected_wire(index))
            self.assertEqual(row["current"], f"{index+1}/24".ljust(5))
            self.assertEqual(row["select"], row["spelling"])
            self.assertEqual(len(row["select"]), 5)
            self.assertEqual(row["length"], len(row["digits"]))
            self.assertEqual(row["matrix"][-1] & 127, 0)
            if index in matrices:
                self.assertEqual(row["matrix"], matrices[index])
            matrices[index] = row["matrix"]
            expected = expected_planes(index, row["matrix"], row["select"])
            for plane in row["planes"]:
                np.testing.assert_array_equal(plane, expected)
            self.assertEqual(decode(row["planes"][0].tobytes()), expected_wire(index))
            pixels = np.unpackbits(row["planes"][0], axis=1)
            for area in (pixels[15:23, 135:505], pixels[377:385, 135:505],
                         pixels[23:377, 135:143], pixels[23:377, 497:505], pixels[:, 512:520]):
                self.assertTrue(np.all(area == 1))
            if row["initial"] or row["result"] == SELECTED:
                black = int(np.count_nonzero(unpack_matrix(row["matrix"])))
                self.assertEqual(row["writes"], [32000 + black*4 + 368]*4)
            elif row["result"] == EDITED:
                self.assertEqual(row["writes"], [80]*4)
            else:
                self.assertEqual(row["writes"], [0]*4)
            if at:
                previous = rows[at-1]
                if row["result"] != SELECTED:
                    self.assertEqual(row["matrix"], previous["matrix"])
                    self.assertEqual(row["wire"], previous["wire"])
                    self.assertEqual(row["current"], previous["current"])
                    outside = np.ones((400, 80), bool)
                    if row["result"] == EDITED:
                        outside[96:112, 65:70] = False
                    for p in range(4):
                        np.testing.assert_array_equal(row["planes"][p][outside], previous["planes"][p][outside])
        return data

    def run_keys(self, *events):
        return self.check_success(capture([*events, ESC]))

    def committed(self, text, index):
        data = self.run_keys(*select(text))
        row = data["rows"][-2]
        self.assertEqual((row["result"], row["selected"], row["displayed_index"]),
                         (SELECTED, index+1, index))
        self.assertEqual((row["digits"], row["length"], row["error"]), ("", 0, NONE))
        self.assertEqual(row["select"], str(text).ljust(5))
        return data

    def test_initial_frame_and_fixture_hash(self):
        row = self.run_keys()["rows"][0]
        self.assertEqual((row["displayed_index"], row["selected"], row["select"]), (0, 0, "-    "))
        self.assertEqual(row["wire"][:9].hex(), "503938510100000018")
        self.assertEqual(hashlib.sha256(row["wire"]).hexdigest(), INITIAL_SHA)
        self.assertEqual(hashlib.sha256(expected_wire(0)).hexdigest(), INITIAL_SHA)

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
        self.assertEqual((row["select"], row["current"], row["displayed_index"]), ("4    ", "23/24", 22))

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
        self.assertEqual([r["current"] for r in commits], ["24/24", "2/24 ", "1/24 "])
        for previous, new in zip(commits, commits[1:]):
            vanished = (unpack_matrix(previous["matrix"]) == 1) & (unpack_matrix(new["matrix"]) == 0)
            self.assertTrue(np.any(vanished))
            for plane in new["planes"]:
                pixels = np.unpackbits(plane, axis=1)
                self.assertTrue(np.all(pixels[23:377, 143:497][vanished.repeat(2, 0).repeat(2, 1)] == 1))
            self.assertTrue(np.all(new["planes"][:, 48:64, 69] == 255))

    def test_all_24_frames_and_independent_artifacts(self):
        data = self.run_keys(*(event for number in range(1, 25) for event in select(number)))
        commits = [r for r in data["rows"] if r["result"] == SELECTED]
        self.assertEqual([r["displayed_index"] for r in commits], list(range(24)))
        self.assertEqual(len({r["wire"] for r in commits}), 24)
        self.assertEqual(len({r["matrix"] for r in commits}), 24)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for index, row in enumerate(commits):
            wire = expected_wire(index)
            self.assertTrue({0, 0x1A, 0x80, 0xFF} <= set(wire[9:]))
            self.assertEqual(set(wire[9:265]), set(range(256)))
            (ARTIFACTS / f"frame-{index:02d}.bin").write_bytes(wire)
            (ARTIFACTS / f"frame-{index:02d}.hex").write_text(wire.hex().upper() + "\n")
            pixels = np.unpackbits(row["planes"][0], axis=1)*255
            self.assertTrue(cv2.imwrite(str(ARTIFACTS / f"qframe-host-{index:02d}.png"), pixels))
            hashes[str(index)] = hashlib.sha256(wire).hexdigest()
        (ARTIFACTS / "fixture-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")

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

    def fault(self, fault, code, expected_trace, events=()):
        data = capture([*events, ESC, *select(24)], fault)
        self.assertEqual(data["exit"], code)
        info = data["info"]
        self.assertEqual(info["trace"].split(',') if info["trace"] else [], expected_trace)
        self.assertEqual(info["shutdowns"], info["inits"])
        return data

    def test_usage_failure_before_graphics(self):
        self.fault("usage", 1, [])

    def test_state_initialization_failure(self):
        self.fault("init", 2, START[:1])

    def test_initial_header_failure(self):
        self.fault("header1", 3, START[:2])

    def test_initial_encoder_failure(self):
        self.fault("encoder1", 4, START[:3])

    def check_prepare_failure(self, fault, code, stages, events, prefix):
        data = self.fault(fault, code, prefix + KEY + stages + ["shutdown"], events)
        last, previous = data["rows"][-1], data["rows"][-2]
        np.testing.assert_array_equal(last["planes"], previous["planes"])
        self.assertEqual(last["matrix"], previous["matrix"])
        self.assertEqual(last["current"], previous["current"])
        self.assertEqual(last["clears"], previous["clears"])
        self.assertEqual(last["displayed_index"], previous["displayed_index"])

    def test_runtime_header_failure_before_clear(self):
        self.check_prepare_failure("header2", 3, ["header"], select(2), START+KEY+["edit"])

    def test_runtime_encoder_failure_before_clear(self):
        self.check_prepare_failure("encoder2", 4, ["header", "encode"], select(2), START+KEY+["edit"])

    def test_prepare_failure_after_successful_switch(self):
        prefix = START+(KEY+["edit"])*2+KEY+SWITCH+KEY+["edit"]
        self.check_prepare_failure("encoder3", 4, ["header", "encode"], [*select(23), *select(2)], prefix)

    def check_status_failure(self, number, events, prefix, code):
        for step, role in enumerate(LABELS):
            with self.subTest(role=role):
                data = self.fault(f"{role}{number}", code, prefix+LABELS[:step+1]+["shutdown"], events)
                info, last = data["info"], data["rows"][-1]
                self.assertEqual(info["redraws"], number-1)
                self.assertEqual(info["shows"], int(number > 1))
                # 先行呼出しは描画済みの可能性があるため、部分的な画面を正確に比較する。
                # 失敗した呼出しと、それ以降の呼出しは何も書き込んでいないことを確認する。
                # Earlier calls may have written. Compare precisely the partial
                # screen; a failed call and all later calls have written nothing.
                index = last["encoded_index"]
                pixels = np.full((400, 640), 255, np.uint8)
                qr = reference_matrix(index, last["matrix"])
                pixels[23:377, 143:497] = (1-qr).repeat(2, 0).repeat(2, 1)*255
                strings = [("CURRENT", 32), (f"{index+1}/24".ljust(5), 48),
                           ("SELECT", 80), (last["spelling"], 96)]
                for text, y in strings[:step]:
                    overlay(pixels, text, y)
                for plane in last["planes"]:
                    np.testing.assert_array_equal(plane, np.packbits(pixels == 255, axis=1))

    def test_each_initial_status_failure_stops_without_show(self):
        self.check_status_failure(1, [], START[:6], 5)

    def test_each_runtime_status_failure_stops_and_shuts_down(self):
        prefix = START+KEY+["edit"]+KEY+["header", "encode", "clear", "qr"]
        self.check_status_failure(2, select(2), prefix, 6)

    def test_first_select_edit_failure_preserves_screen(self):
        data = self.fault("edit1", 6, START+KEY+["edit", "shutdown"], digits("2"))
        np.testing.assert_array_equal(data["rows"][-1]["planes"], data["rows"][0]["planes"])

    def test_edit_failure_after_successful_switch(self):
        prefix = START+KEY+["edit"]+KEY+SWITCH
        data = self.fault("edit2", 6, prefix+KEY+["edit", "shutdown"], [*select(2), *digits("3")])
        np.testing.assert_array_equal(data["rows"][-1]["planes"], data["rows"][-2]["planes"])

    def test_bios_argument_error_before_peek(self):
        data = self.fault("poll1", 7, START+["poll", "shutdown"])
        self.assertEqual(data["info"]["peeks"], 0)

    def test_bad_bh_never_reads_or_classifies(self):
        for bh in (2, 127, 255):
            with self.subTest(bh=bh):
                data = self.fault("none", 7, START+["poll", "shutdown"], [f"!{bh:02x}"])
                self.assertEqual((data["info"]["reads"], data["info"]["classifications"]), (0, 0))

    def test_bios_error_after_successful_switch(self):
        prefix = START+KEY+["edit"]+KEY+SWITCH
        data = self.fault("none", 7, prefix+["poll", "shutdown"], [*select(2), "!02"])
        self.assertEqual(data["rows"][-1]["displayed_index"], 1)

    def test_input_argument_error(self):
        self.fault("input1", 8, START+KEY+["shutdown"], digits("2"))

    def test_input_error_after_successful_switch(self):
        prefix = START+KEY+["edit"]+KEY+SWITCH
        self.fault("input3", 8, prefix+KEY+["shutdown"], [*select(2), *digits("3")])


if __name__ == "__main__":
    unittest.main()
