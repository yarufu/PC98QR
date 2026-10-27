# 実QINPUTのイベント統合、SELECTの背景込み更新、完全なバイナリQRを検証する。
"""Real QINPUT event integration, opaque SELECT updates and complete binary QR."""
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

# 独立して手書きした字形基準と、変更しない本番/QSTATUSドライバーを使用する。
# テスト重複検出を避けるため、TestCaseクラスではなくモジュールを取り込む。
# Independent hand-written glyph oracle and unchanged production/QSTATUS drivers.
# Import the module, not its TestCase classes, to avoid duplicate test discovery.
import test_qr_status_probe as qstatus

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1e"
HEADER = bytes.fromhex("503938510100000018")
WIRE = HEADER + bytes((i * 37 + (i >> 8) * 11 + 3) & 255 for i in range(2944))
WIRE_SHA256 = "1c3d4971bb24ec58e796da27e25ecb0aff11a3e7c149b21376d60dd275bbba73"
ENTER, BS, ESC = "1c0d", "0e08", "001b"
UNCHANGED, EDITED, REJECTED, SELECTED, EXIT, ARGUMENT = range(6)
NONE, RANGE, OVERFLOW, TOO_LONG = range(4)
START = ["state_init", "header", "encode", "init", "clear", "qr", "fixed", "show"]


def digits(text):
    return [f"{(10 if c == '0' else int(c)):02x}{ord(c):02x}" for c in text]


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_INPUT_DRIVER")
    if override:
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_qr_input_probe"
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "tests/qr_input_probe_main.c", "src/manual_selection.c",
                    "src/qr_encode.c", "src/frame_header.c", "-o", str(target)],
                   cwd=ROOT, check=True)
    return target


def capture(events, fault="none"):
    process = subprocess.run([str(driver()), "capture", " ".join(events), fault],
                             cwd=ROOT, capture_output=True, timeout=30)
    if process.stderr or process.returncode < 0:
        raise AssertionError(f"exit {process.returncode}: {process.stderr.decode(errors='replace')}")
    stream = io.BytesIO(process.stdout)

    def take(size):
        raw = stream.read(size)
        assert len(raw) == size
        return raw

    data = {"rows": [], "exit": process.returncode}
    while line := stream.readline():
        row = json.loads(line)
        if row["kind"] == "summary":
            data["info"] = row
            assert not stream.read()
        elif row["kind"] == "qr":
            assert "qr" not in data
            data["qr"] = np.frombuffer(take(4*32000), np.uint8).reshape(4, 400, 80)
        else:
            assert row["kind"] == "snapshot"
            row["planes"] = np.frombuffer(take(4*32000), np.uint8).reshape(4, 400, 80)
            row["matrix"] = take(3917)
            row["wire"] = take(2953)
            data["rows"].append(row)
    assert data["info"]["result"] == process.returncode
    assert data["info"]["snapshots"] == len(data["rows"])
    return data


@lru_cache(maxsize=128)
def expected_planes(display):
    expected = qstatus.production().copy()
    for text, y in (("CURRENT", 32), ("1/24", 48), ("SELECT", 80), (display, 96)):
        for cell, character in enumerate(text):
            expected[y:y+16, 520+cell*8:528+cell*8] = (1-qstatus.glyph(character))*255
    return np.packbits(expected == 255, axis=1)


@lru_cache(maxsize=256)
def decode_plane(plane):
    # Enter反復等のバイト単位で同一の画面はキャッシュする。
    # それでも全スナップショットを後段の厳密な生バイト比較に含める。
    # Cache identical byte-for-byte screens (e.g. Enter repeat); every snapshot
    # still participates in the exact raw-byte comparison below.
    pixels = np.unpackbits(np.frombuffer(plane, np.uint8).reshape(400, 80), axis=1)*255
    result = zxingcpp.read_barcode(pixels, formats=zxingcpp.BarcodeFormat.QRCode,
                                  try_downscale=False)
    assert result is not None and result.valid
    assert isinstance(result.bytes, bytes)
    return result.bytes


class QInputTests(unittest.TestCase):
    def check_capture(self, data, code=0):
        self.assertEqual(data["exit"], code)
        info, rows = data["info"], data["rows"]
        self.assertEqual(info["shutdowns"], info["inits"])
        if "qr" in data:
            production = np.packbits(qstatus.production() == 255, axis=1)
            for p in range(4):
                np.testing.assert_array_equal(data["qr"][p], production)
        if rows:
            np.testing.assert_array_equal(np.unpackbits(rows[0]["planes"][0], axis=1)*255,
                                          qstatus.image("probe"))
            body = qstatus.production()[23:377:2, 143:497:2]
            matrix = np.packbits((body == 0).astype(np.uint8).reshape(-1)).tobytes()
            outside = np.ones((400, 80), bool)
            outside[96:112, 65:70] = False
            for row in rows:
                self.assertEqual(row["matrix"], matrix)
                self.assertEqual(row["matrix"][-1] & 0x7F, 0)
                self.assertEqual(row["wire"], WIRE)
                self.assertEqual(len(row["display"]), 5)
                self.assertEqual(len(row["buffer"]), 5)
                self.assertEqual(row["length"], len(row["digits"]))
                for p in range(4):
                    np.testing.assert_array_equal(row["planes"][p], expected_planes(row["display"]))
                    np.testing.assert_array_equal(row["planes"][p][outside], rows[0]["planes"][p][outside])
                # 初期・編集・確定・拒否・無視・未入力の各画面を含む。
                # Escと表示後の失敗画面も含め、常にフレーム全バイトを検証する。
                # Includes initial, edited, committed, rejected, ignored, empty,
                # Esc and post-show failure snapshots. Always full frame bytes.
                self.assertEqual(decode_plane(row["planes"][0].tobytes()), WIRE)
                if row["initial"]:
                    self.assertEqual(row["writes"], [288]*4)
                else:
                    success = row["result"] == EDITED and not (code == 6 and row is rows[-1])
                    self.assertEqual(row["writes"], [80 if success else 0]*4)
        return data

    def run_keys(self, *events):
        return self.check_capture(capture([*events, ESC]))

    def state(self, row, text, display=None, result=EDITED, selected=0, error=NONE, exited=0):
        expected_display = (text or '-').ljust(5) if display is None else display
        self.assertEqual({k: row[k] for k in ("digits", "length", "display", "buffer",
                                             "result", "selected", "error", "exited")},
                         dict(digits=text, length=len(text), display=expected_display,
                              buffer=expected_display, result=result, selected=selected,
                              error=error, exited=exited))

    def test_initial_select_and_escape(self):
        data = self.run_keys()
        self.state(data["rows"][0], "", result=UNCHANGED)
        self.state(data["rows"][1], "", result=EXIT, exited=1)
        self.assertEqual(data["info"]["updates"], 0)

    def test_each_digit_is_visible(self):
        rows = self.run_keys(*digits("23"))["rows"]
        self.state(rows[1], "2")
        self.state(rows[2], "23")
        for character in "0123456789":
            with self.subTest(digit=character):
                self.state(self.run_keys(*digits(character))["rows"][1], character)

    def test_backspace_then_24_commit(self):
        rows = self.run_keys(*digits("23"), BS, *digits("4"), ENTER)["rows"]
        for row, text in zip(rows[1:5], ("2", "23", "2", "24")):
            self.state(row, text)
        self.state(rows[5], "", "24   ", SELECTED, 24)
        self.assertTrue(np.all(rows[3]["planes"][:, 96:112, 66:70] == 255))

    def test_all_digits_deleted_to_dash(self):
        rows = self.run_keys(*digits("23"), BS, BS, BS)["rows"]
        self.state(rows[-3], "")
        self.state(rows[-2], "", result=UNCHANGED)

    def test_leading_zero_spelling_survives_commit(self):
        rows = self.run_keys(*digits("00023"), ENTER)["rows"]
        for i in range(1, 6):
            self.state(rows[i], "00023"[:i])
        self.state(rows[6], "", "00023", SELECTED, 23)

    def test_new_digit_replaces_committed_five_cells(self):
        rows = self.run_keys(*digits("00023"), ENTER, *digits("4"))["rows"]
        self.state(rows[-2], "4", selected=23)
        self.assertTrue(np.all(rows[-2]["planes"][:, 96:112, 66:70] == 255))

    def test_backspace_after_commit_keeps_display(self):
        rows = self.run_keys(*digits("23"), ENTER, BS, BS)["rows"]
        self.state(rows[-2], "", "23   ", UNCHANGED, 23)

    def test_empty_enter(self):
        self.state(self.run_keys(ENTER)["rows"][1], "", result=UNCHANGED)

    def test_empty_backspace(self):
        self.state(self.run_keys(BS)["rows"][1], "", result=UNCHANGED)

    def check_reject(self, text, error):
        rows = self.run_keys(*digits(text), ENTER)["rows"]
        self.state(rows[-2], text, result=REJECTED, error=error)
        self.assertEqual(rows[-2]["writes"], [0]*4)

    def test_zero_rejected(self):
        self.check_reject("0", RANGE)

    def test_five_zeroes_rejected(self):
        self.check_reject("00000", RANGE)

    def test_25_rejected(self):
        self.check_reject("25", RANGE)

    def test_65535_is_range_not_overflow(self):
        self.check_reject("65535", RANGE)

    def test_65536_overflow(self):
        self.check_reject("65536", OVERFLOW)

    def test_99999_overflow(self):
        self.check_reject("99999", OVERFLOW)

    def test_valid_boundaries_and_repeated_selections(self):
        rows = self.run_keys(*digits("1"), ENTER, *digits("24"), ENTER)["rows"]
        self.state(rows[2], "", "1    ", SELECTED, 1)
        self.state(rows[5], "", "24   ", SELECTED, 24)

    def test_sixth_digit_keeps_five_saved_digits(self):
        rows = self.run_keys(*digits("123456"))["rows"]
        self.state(rows[-2], "12345", result=REJECTED, error=TOO_LONG)

    def test_too_long_cannot_commit_valid_prefix(self):
        rows = self.run_keys(*digits("000231"), ENTER, ENTER, *digits("4"))["rows"]
        self.state(rows[6], "00023", result=REJECTED, error=TOO_LONG)
        self.state(rows[7], "00023", result=REJECTED, error=TOO_LONG)
        self.state(rows[8], "00023", result=REJECTED, error=TOO_LONG)
        self.state(rows[9], "00023", result=UNCHANGED, error=TOO_LONG)

    def test_too_long_backspace_removes_saved_digit_and_recovers(self):
        rows = self.run_keys(*digits("000231"), ENTER, BS, ENTER)["rows"]
        self.state(rows[-3], "0002")
        self.state(rows[-2], "", "0002 ", SELECTED, 2)

    def test_range_reject_can_be_corrected(self):
        rows = self.run_keys(*digits("25"), ENTER, BS, *digits("4"), ENTER)["rows"]
        self.state(rows[3], "25", result=REJECTED, error=RANGE)
        self.state(rows[4], "2")
        self.state(rows[6], "", "24   ", SELECTED, 24)

    def test_overflow_can_be_deleted_and_reentered(self):
        rows = self.run_keys(*digits("65536"), ENTER, *([BS]*5), *digits("24"), ENTER)["rows"]
        self.state(rows[7], "6553")
        self.state(rows[11], "")
        self.state(rows[-2], "", "24   ", SELECTED, 24)

    def test_append_after_zero_reject(self):
        rows = self.run_keys(*digits("0"), ENTER, *digits("1"), ENTER)["rows"]
        self.state(rows[3], "01")
        self.state(rows[4], "", "01   ", SELECTED, 1)

    def test_reject_preserves_previous_selection(self):
        for text, error in (("25", RANGE), ("65536", OVERFLOW), ("000231", TOO_LONG)):
            with self.subTest(text=text):
                rows = self.run_keys(*digits("23"), ENTER, *digits(text), ENTER)["rows"]
                self.state(rows[-2], text[:5], result=REJECTED, selected=23, error=error)

    def test_clear_edit_does_not_resurrect_previous_selection(self):
        rows = self.run_keys(*digits("23"), ENTER, *digits("4"), BS)["rows"]
        self.state(rows[-2], "", selected=23)

    def test_enter_repeats_do_not_redraw_or_recommit(self):
        rows = self.run_keys(*([ENTER]*8), *digits("23"), *([ENTER]*8))["rows"]
        self.assertEqual(sum(r["result"] == SELECTED for r in rows), 1)
        self.assertEqual(sum(r["result"] == EDITED for r in rows), 2)
        self.state(rows[-2], "", "23   ", UNCHANGED, 23)

    def test_equal_digits_are_not_deduplicated(self):
        for text in ("11", "22"):
            with self.subTest(text=text):
                self.state(self.run_keys(*digits(text), ENTER)["rows"][-2],
                           "", text.ljust(5), SELECTED, int(text))

    def test_digit_autorepeat_latches_too_long(self):
        data = self.run_keys(*(digits("2")*30), *([ENTER]*10))
        self.assertEqual(data["info"]["updates"], 5)
        self.state(data["rows"][-2], "22222", result=REJECTED, error=TOO_LONG)

    def test_unrelated_keys_and_control_aliases_do_not_redraw(self):
        events = ["1e61", "6200", "0f09", "2d0d", "2308", "1b1b", "000d", "01c7"]
        rows = self.run_keys(*digits("25"), ENTER, *events)["rows"]
        for row in rows[4:-1]:
            self.state(row, "25", result=UNCHANGED, error=RANGE)

    def test_empty_polls_ignore_stale_escape(self):
        data = self.run_keys("-", "-", *digits("2"), "-", ENTER)
        self.assertEqual((data["info"]["polls"], data["info"]["reads"]), (6, 3))
        self.state(data["rows"][1], "", result=-1)
        self.state(data["rows"][4], "2", result=-1)
        self.state(data["rows"][5], "", "2    ", SELECTED, 2)

    def test_consumed_ax_not_peek_ax_is_classified(self):
        data = self.run_keys(*digits("23"), ENTER)
        self.assertEqual([r["ax"] for r in data["rows"][1:]], [0x0232, 0x0333, 0x1C0D, 0x001B])
        self.assertEqual(data["info"]["classifications"], 4)

    def test_keypad_digits(self):
        self.state(self.run_keys("4e30", "4b32", "4c33", ENTER)["rows"][-2],
                   "", "023  ", SELECTED, 23)

    def test_escape_from_all_input_states(self):
        for events in ([], digits("2"), [*digits("23"), ENTER],
                       [*digits("25"), ENTER], [*digits("99999"), ENTER], digits("000231")):
            with self.subTest(events=events):
                data = self.run_keys(*events)
                self.assertEqual(data["rows"][-1]["result"], EXIT)
                self.assertEqual(data["rows"][-1]["exited"], 1)
                self.assertEqual(data["info"]["shutdowns"], 1)

    def test_escape_leaves_later_events_unconsumed(self):
        data = self.check_capture(capture([ESC, *digits("24"), ENTER]))
        self.assertEqual((data["info"]["polls"], data["info"]["reads"]), (1, 1))

    def test_startup_enter_and_new_digit_repeat_limitation(self):
        data = self.run_keys(ENTER, ENTER, *digits("2"), ENTER, ENTER, *digits("3"), ENTER)
        self.assertEqual([r["selected"] for r in data["rows"] if r["result"] == SELECTED], [2, 3])

    def test_queued_digits_are_not_flushed(self):
        self.state(self.run_keys(*digits("22"), ENTER)["rows"][-2], "", "22   ", SELECTED, 22)

    def test_exact_lifecycle_and_no_wait_key(self):
        data = self.run_keys(*digits("23"), ENTER)
        info = data["info"]
        expected = START + ["poll", "classify", "input", "update"]*2
        expected += ["poll", "classify", "input"]*2 + ["shutdown"]
        self.assertEqual(info["trace"].split(','), expected)
        for name in ("state_inits", "headers", "encodes", "inits", "clears", "qr_passes",
                     "initials", "shows", "shutdowns"):
            self.assertEqual(info[name], 1, name)
        self.assertEqual(info["updates"], 2)

    def test_fixture_sha256_and_artifacts(self):
        data = self.run_keys()
        self.assertEqual(hashlib.sha256(data["rows"][0]["wire"]).hexdigest(), WIRE_SHA256)
        self.assertEqual(hashlib.sha256(WIRE).hexdigest(), WIRE_SHA256)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "QINPUT.BIN").write_bytes(WIRE)
        (ARTIFACTS / "QINPUT.HEX").write_text(WIRE.hex().upper() + '\n')

    def test_quiet_zone_all_pixels_and_snapshot_photos(self):
        data = self.run_keys(*digits("23"), BS, *digits("4"), ENTER,
                             *digits("00023"), ENTER, *digits("25"), ENTER, BS)
        for i, row in enumerate(data["rows"]):
            pixels = np.unpackbits(row["planes"][0], axis=1)*255
            np.testing.assert_array_equal(pixels[15:385, 135:505], qstatus.production()[15:385, 135:505])
            for area in (pixels[15:23, 135:505], pixels[377:385, 135:505],
                         pixels[23:377, 135:143], pixels[23:377, 497:505]):
                self.assertTrue(np.all(area == 255))
            self.assertTrue(cv2.imwrite(str(ARTIFACTS / f"qinput-host-{i:02d}.png"), pixels))

    def test_five_cells_erased_even_when_stores_are_white(self):
        rows = self.run_keys(*digits("00023"), *([BS]*5))["rows"]
        self.state(rows[-2], "")
        self.assertEqual(rows[-2]["writes"], [80]*4)
        np.testing.assert_array_equal(rows[-2]["planes"], rows[0]["planes"])

    def check_fault(self, fault, code, trace, events=()):
        data = self.check_capture(capture([*events, ESC], fault), code)
        self.assertEqual(data["info"]["trace"].split(',') if trace else data["info"]["trace"],
                         trace if trace else "")
        return data

    def test_usage_rejected_before_state_or_graphics(self):
        self.check_fault("usage", 1, [])

    def test_state_initialization_failure(self):
        self.check_fault("init", 2, START[:1])

    def test_header_failure(self):
        self.check_fault("header", 3, START[:2])

    def test_encoder_failure(self):
        self.check_fault("encoder", 4, START[:3])

    def test_initial_font_failure_shuts_down_without_show(self):
        self.check_fault("initial", 5, START[:7] + ["shutdown"])

    def test_first_select_update_failure(self):
        data = self.check_fault("update1", 6, START + ["poll", "classify", "input", "update", "shutdown"], digits("2"))
        self.assertEqual(data["rows"][-1]["buffer"], "2    ")
        self.assertEqual(data["rows"][-1]["display"], "-    ")

    def test_later_select_update_failure(self):
        self.check_fault("update2", 6, START + ["poll", "classify", "input", "update"]*2 + ["shutdown"], digits("23"))

    def test_bios_argument_error(self):
        data = self.check_fault("key_argument", 7, START + ["poll", "shutdown"])
        self.assertEqual(data["info"]["peeks"], 0)

    def test_unexpected_bh_never_reads_or_classifies(self):
        for bh in (2, 127, 255):
            with self.subTest(bh=bh):
                data = self.check_capture(capture([f"!{bh:02x}", *digits("24"), ENTER, ESC]), 7)
                self.assertEqual(data["info"]["trace"].split(','), START + ["poll", "shutdown"])
                self.assertEqual((data["info"]["peeks"], data["info"]["reads"], data["info"]["inputs"]), (1, 0, 0))

    def test_bios_error_after_update(self):
        data = self.check_capture(capture([*digits("2"), "!02", *digits("3"), ESC]), 7)
        self.assertEqual(data["info"]["trace"].split(','),
                         START + ["poll", "classify", "input", "update", "poll", "shutdown"])
        self.assertEqual(data["rows"][-1]["display"], "2    ")

    def test_input_argument_error(self):
        self.check_fault("input1", 8, START + ["poll", "classify", "input", "shutdown"], digits("2"))

    def test_input_error_after_update(self):
        self.check_fault("input2", 8, START + ["poll", "classify", "input", "update",
                                             "poll", "classify", "input", "shutdown"], digits("23"))


if __name__ == '__main__':
    unittest.main()
