# QUPDの1実行中の5画面、背景込み更新、QR保護、生バイトを検証する。
"""QUPD: five displays in one run, opaque updates, protected QR and raw bytes."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

import cv2
import numpy as np
import zxingcpp

# 独立して手書きした線描基準と、変更しない比較用ドライバーを再利用する。
# TestCaseクラスや本番Cの字形表は再利用しない。
# Reuse the independent hand-written stroke oracle and unchanged reference
# drivers, not their TestCase classes or the production C glyph table.
import test_qr_status_probe as qstatus

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1d"
HEADER = bytes.fromhex("503938510100000018")
WIRE = HEADER + bytes((i * 37 + (i >> 8) * 11 + 3) & 255 for i in range(2944))
WIRE_SHA256 = "1c3d4971bb24ec58e796da27e25ecb0aff11a3e7c149b21376d60dd275bbba73"
SELECT = ("-    ", "23   ", "2    ", "00023", "-    ")
EVENTS = ["header", "encode", "init", "clear", "qr", "initial", "show",
          "wait1", "update1", "wait2", "update2", "wait3", "update3",
          "wait4", "update4", "wait5", "shutdown"]


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_STATUS_UPDATE_DRIVER")
    if override:
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_qr_status_update_probe"
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "tests/qr_status_update_probe_main.c", "src/qr_encode.c",
                    "src/frame_header.c", "-o", str(target)], cwd=ROOT, check=True)
    return target


@lru_cache(maxsize=1)
def capture():
    result = subprocess.run([str(driver()), "capture"], capture_output=True,
                            check=True, timeout=30)
    assert result.stderr == b""
    line, raw = result.stdout.split(b'\n', 1)
    position = 0

    def take(size):
        nonlocal position
        block = raw[position:position+size]
        assert len(block) == size
        position += size
        return block

    data = {"info": json.loads(line), "wire": take(2953), "matrix": take(3917)}
    data["qr"] = np.frombuffer(take(4*32000), np.uint8).reshape(4, 400, 80)
    data["planes"] = np.frombuffer(take(5*4*32000), np.uint8).reshape(5, 4, 400, 80)
    data["matrices"] = np.frombuffer(take(5*3917), np.uint8).reshape(5, 3917)
    data["stores"] = np.frombuffer(take(5*4*32000), np.uint8).reshape(5, 4, 400, 80)
    assert position == len(raw)
    return data


def screen(state):
    return np.unpackbits(capture()["planes"][state, 0], axis=1) * 255


def expected_screen(state):
    expected = qstatus.production().copy()
    for text, y in (("CURRENT", 32), ("1/24", 48), ("SELECT", 80), (SELECT[state], 96)):
        for cell, character in enumerate(text):
            expected[y:y+16, 520+cell*8:528+cell*8] = (1-qstatus.glyph(character))*255
    return expected


class StatusUpdateTests(unittest.TestCase):
    def test_complete_fixture_independent_bytes(self):
        self.assertEqual(len(WIRE), 2953)
        self.assertEqual(capture()["wire"], WIRE)
        self.assertEqual(capture()["wire"][:9], HEADER)
        self.assertEqual(len(capture()["wire"][9:]), 2944)
        self.assertTrue({0, 0x1A, 0x80, 0xFF, 13, 10} <= set(WIRE[9:]))

    def test_fixture_sha256_and_artifacts(self):
        self.assertEqual(hashlib.sha256(WIRE).hexdigest(), WIRE_SHA256)
        self.assertEqual(hashlib.sha256(capture()["wire"]).hexdigest(), WIRE_SHA256)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "QUPD.BIN").write_bytes(WIRE)
        (ARTIFACTS / "QUPD.HEX").write_text(WIRE.hex().upper() + '\n')

    def test_qr_before_labels_equals_production_every_pixel(self):
        expected = qstatus.production()
        for p in range(4):
            with self.subTest(plane=p):
                pixels = np.unpackbits(capture()["qr"][p], axis=1)*255
                np.testing.assert_array_equal(pixels, expected)

    def test_matrix_all_bytes_equal_production(self):
        body = qstatus.production()[23:377:2, 143:497:2]
        packed = np.packbits((body == 0).astype(np.uint8).reshape(-1)).tobytes()
        self.assertEqual(len(packed), 3917)
        self.assertEqual(capture()["matrix"], packed)
        self.assertEqual(packed[-1] & 0x7F, 0)

    def test_initial_equals_unchanged_qstatus_every_pixel(self):
        np.testing.assert_array_equal(screen(0), qstatus.image("probe"))

    def check_screen(self, state):
        expected = expected_screen(state)
        np.testing.assert_array_equal(screen(state), expected)
        np.testing.assert_array_equal(screen(state)[96:112, 520:560], expected[96:112, 520:560])

    def test_initial_pixels(self):
        self.check_screen(0)

    def test_update1_pixels(self):
        self.check_screen(1)

    def test_update2_pixels(self):
        self.check_screen(2)

    def test_update3_pixels(self):
        self.check_screen(3)

    def test_update4_pixels(self):
        self.check_screen(4)

    def test_23_to_2_erases_old_3(self):
        self.assertTrue(np.any(screen(1)[96:112, 528:536] == 0))
        self.assertTrue(np.all(screen(2)[96:112, 528:560] == 255))
        np.testing.assert_array_equal(screen(2)[96:112, 520:528], (1-qstatus.glyph('2'))*255)

    def test_00023_to_dash_erases_all_old_strokes(self):
        for cell in range(5):
            self.assertTrue(np.any(screen(3)[96:112, 520+cell*8:528+cell*8] == 0))
        self.assertTrue(np.all(screen(4)[96:112, 528:560] == 255))
        np.testing.assert_array_equal(screen(4)[96:112, 520:528], (1-qstatus.glyph('-'))*255)
        np.testing.assert_array_equal(screen(4), screen(0))

    def test_matrix_preserved_at_all_five_waits(self):
        original = np.frombuffer(capture()["matrix"], np.uint8)
        for state, actual in enumerate(capture()["matrices"]):
            with self.subTest(state=state):
                np.testing.assert_array_equal(actual, original)

    def test_qr_body_pixels_preserved(self):
        for state in range(1, 5):
            with self.subTest(state=state):
                np.testing.assert_array_equal(screen(state)[23:377, 143:497], screen(0)[23:377, 143:497])

    def test_quiet_zone_extent_and_white_pixels_preserved(self):
        for state in range(5):
            with self.subTest(state=state):
                actual = screen(state)
                np.testing.assert_array_equal(actual[15:385, 135:505], qstatus.production()[15:385, 135:505])
                for area in (actual[15:23, 135:505], actual[377:385, 135:505],
                             actual[23:377, 135:143], actual[23:377, 497:505]):
                    self.assertTrue(np.all(area == 255))

    def test_all_x_less_than_520_preserved(self):
        for state in range(5):
            with self.subTest(state=state):
                np.testing.assert_array_equal(screen(state)[:, :520], qstatus.production()[:, :520])
                self.assertTrue(np.all(screen(state)[:, 512:520] == 255))

    def test_current_number_and_select_heading_preserved(self):
        for state in range(1, 5):
            for y, width in ((32, 56), (48, 32), (80, 48)):
                with self.subTest(state=state, y=y):
                    np.testing.assert_array_equal(screen(state)[y:y+16, 520:520+width],
                                                  screen(0)[y:y+16, 520:520+width])

    def test_every_byte_outside_update_rectangle_preserved(self):
        outside = np.ones((400, 80), bool)
        outside[96:112, 65:70] = False
        planes = capture()["planes"]
        for state in range(1, 5):
            for p in range(4):
                with self.subTest(state=state, plane=p):
                    np.testing.assert_array_equal(planes[state, p][outside], planes[state-1, p][outside])
                    np.testing.assert_array_equal(planes[state, p][outside], planes[0, p][outside])

    def test_all_four_planes_match_independent_expected_bytes(self):
        for state in range(5):
            expected = np.packbits(expected_screen(state) == 255, axis=1)
            for p in range(4):
                with self.subTest(state=state, plane=p):
                    np.testing.assert_array_equal(capture()["planes"][state, p], expected)

    def test_store_offsets_counts_and_unique_writes_per_stage(self):
        expected = np.zeros((5, 400, 80), np.uint8)
        for y, cells in ((32, 7), (48, 4), (80, 6), (96, 1)):
            expected[0, y:y+16, 65:65+cells] = 1
        expected[1:, 96:112, 65:70] = 1
        for state in range(5):
            for p in range(4):
                with self.subTest(state=state, plane=p):
                    np.testing.assert_array_equal(capture()["stores"][state, p], expected[state])
        self.assertEqual(capture()["info"]["writes"], [[288]*4] + [[80]*4]*4)
        for state in range(1, 5):
            self.assertEqual(int(capture()["stores"][state].sum()), 320)

    def test_lifecycle_and_exact_event_order(self):
        info = capture()["info"]
        self.assertEqual(info["trace"].split(','), EVENTS)
        counts = {"result": 0, "headers": 1, "encodes": 1, "inits": 1, "clears": 1,
                  "qr_passes": 1, "initials": 1, "shows": 1, "updates": 4,
                  "completed": 4, "waits": 5, "shutdowns": 1}
        for key, value in counts.items():
            self.assertEqual(info[key], value, key)
        self.assertEqual(info["boxes"], int(np.count_nonzero(qstatus.production()[23:377:2, 143:497:2] == 0)))

    def check_decode(self, state):
        frame = screen(state)
        result = zxingcpp.read_barcode(frame, formats=zxingcpp.BarcodeFormat.QRCode,
                                      try_downscale=False)
        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        self.assertIsInstance(result.bytes, bytes)
        self.assertEqual(len(result.bytes), 2953)
        self.assertEqual(result.bytes, WIRE)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.assertTrue(cv2.imwrite(str(ARTIFACTS / f"qupd-host-{state}.png"), frame))

    def test_initial_raw_binary_decode(self):
        self.check_decode(0)

    def test_update1_raw_binary_decode(self):
        self.check_decode(1)

    def test_update2_raw_binary_decode(self):
        self.check_decode(2)

    def test_update3_raw_binary_decode(self):
        self.check_decode(3)

    def test_update4_raw_binary_decode(self):
        self.check_decode(4)

    def check_failure(self, fault, code, update=0):
        result = subprocess.run([str(driver()), "capture", fault], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stderr, b"")
        line, rest = result.stdout.split(b'\n', 1)
        self.assertEqual(rest, b"")
        info = json.loads(line)
        self.assertEqual(info["result"], code)
        active = code == 4
        events = [] if fault == "usage" else EVENTS[:EVENTS.index(fault if update else {
            "header": "header", "encoder": "encode", "initial": "initial"}.get(fault, fault))+1]
        if active:
            events.append("shutdown")
        self.assertEqual(info["trace"], ','.join(events))
        expected = {"headers": int(fault != "usage"), "encodes": int(code >= 3),
                    "inits": int(active), "clears": int(active), "qr_passes": int(active),
                    "initials": int(active), "shows": int(update > 0), "updates": update,
                    "completed": max(update-1, 0), "waits": update, "shutdowns": int(active)}
        for key, value in expected.items():
            self.assertEqual(info[key], value, key)
        self.assertEqual(info["boxes"], capture()["info"]["boxes"] if active else 0)
        writes = [[0]*4 for _ in range(5)]
        if update:
            writes[0] = [288]*4
            for state in range(1, update):
                writes[state] = [80]*4
        self.assertEqual(info["writes"], writes)

    def test_usage_failure(self):
        self.check_failure("usage", 1)

    def test_header_failure(self):
        self.check_failure("header", 2)

    def test_encoder_failure(self):
        self.check_failure("encoder", 3)

    def test_initial_status_failure(self):
        self.check_failure("initial", 4)

    def test_update1_failure(self):
        self.check_failure("update1", 4, 1)

    def test_update2_failure(self):
        self.check_failure("update2", 4, 2)

    def test_update3_failure(self):
        self.check_failure("update3", 4, 3)

    def test_update4_failure(self):
        self.check_failure("update4", 4, 4)


if __name__ == '__main__':
    unittest.main()
