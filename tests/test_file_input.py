# 本番mainからファイル全体を入力し、ホスト専用のI/O故障を注入する試験。
"""Whole-file input through the production main, with host-only I/O faults."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import cv2
import numpy as np
from qrcode.constants import ERROR_CORRECT_M, ERROR_CORRECT_Q
import zxingcpp

from qr_byte_oracle import selected_reference
import qr_v25_oracle as v25
import qr_v40_oracle as v40

ROOT = Path(__file__).resolve().parents[1]
BYTE5 = bytes.fromhex("00017F80FF")
BYTE11 = bytes.fromhex("00011A7F80FFC328004142")
BYTE20 = bytes.fromhex("00011A7F80FFC3280041421020304050607080FF")
BYTE26 = bytes.fromhex("00011A7F80FFC3280041421020304050607080FF001A80FF0D0A")
FAULT_VARIABLES = ("PC98QR_TEST_READ_PLAN", "PC98QR_TEST_CLOSE_ERROR",
                   "PC98QR_TEST_ENCODER_ERROR")


class FileInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        override = os.environ.get("PC98QR_MAIN_DRIVER")
        cls.executable = Path(override) if override else ROOT / "build/test_main_render"
        if not override:
            subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                           stdout=subprocess.PIPE)

    def invoke(self, *arguments, cwd=None, read_plan=None, close_error=False,
               encoder_error=False):
        environment = os.environ.copy()
        for name in FAULT_VARIABLES:
            environment.pop(name, None)
        if read_plan is not None:
            environment[FAULT_VARIABLES[0]] = read_plan
        if close_error:
            environment[FAULT_VARIABLES[1]] = "1"
        if encoder_error:
            environment[FAULT_VARIABLES[2]] = "1"
        return subprocess.run([str(self.executable), *arguments], cwd=cwd,
                              env=environment, capture_output=True)

    def read_file(self, payload, *, filename="INPUT.BIN", **faults):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            result = self.invoke("/F", filename, cwd=directory, **faults)
            self.assertEqual(path.read_bytes(), payload)  # Input stays unchanged. / 日本語: 入力は変更されない。
            return result

    def assert_success(self, result, payload):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        frame = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (400, 640))
        decoded = zxingcpp.read_barcode(frame, formats=zxingcpp.BarcodeFormat.QRCode,
                                       try_downscale=False)
        self.assertIsNotNone(decoded)
        self.assertTrue(decoded.valid)
        self.assertEqual(decoded.bytes, payload)
        return frame

    def assert_file_error(self, result, message):
        # C側でも符号化呼出し0回、画面段階0を要求する。
        # 模擬故障を含め、open成功ごとにcloseを正確に一度行う。
        # C assertions also require zero encoder calls, graphics stage 0, and
        # exactly one close per successful open (including simulated failures).
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, ("Error: " + message + "\r\n").encode("ascii"))

    def test_all_lengths_use_actual_file_size(self):
        for length in range(1, 27):
            payload = bytes(range(length))
            with self.subTest(length=length):
                self.assert_success(self.read_file(payload), payload)

    def test_single_special_bytes(self):
        for value in (0x00, 0x1A, 0x80, 0xFF, 0x0D, 0x0A):
            payload = bytes([value])
            with self.subTest(value=value):
                self.assert_success(self.read_file(payload), payload)

    def test_embedded_nul(self):
        payload = b"A\x00B"
        self.assert_success(self.read_file(payload), payload)

    def test_trailing_1a_is_not_removed(self):
        payload = b"A\x00\x1a"
        self.assert_success(self.read_file(payload), payload)

    def test_cr_lf_crlf_are_not_translated(self):
        payload = b"\r\nA\rB\n\x1a\x80\xff"
        self.assert_success(self.read_file(payload), payload)

    def test_phase2c_vectors_match_hex_frame_matrix_and_mask(self):
        for payload in (BYTE5, BYTE11):
            with self.subTest(hex=payload.hex()):
                file_result = self.read_file(payload)
                hex_result = self.invoke("/B", payload.hex().upper())
                frame = self.assert_success(file_result, payload)
                self.assert_success(hex_result, payload)
                self.assertEqual(file_result.stdout, hex_result.stdout)
                qr = np.asarray(selected_reference(payload).get_matrix(), dtype=np.uint8)
                expected = np.full((400, 640), 255, dtype=np.uint8)
                expected[74:326, 194:446] = (1 - qr).repeat(12, 0).repeat(12, 1) * 255
                # 形式情報とマスク領域の両方を含む441モジュールすべてを照合する。
                # All 441 modules, including both format/mask fields, match.
                np.testing.assert_array_equal(frame, expected)

    def test_version_boundaries_match_hex_and_every_frame_pixel(self):
        self.assertEqual(len(BYTE20), 20)
        for length in (1, 11, 12, 20, 21, 25, 26):
            payload = BYTE26[:length]
            with self.subTest(length=length):
                file_result = self.read_file(payload)
                frame = self.assert_success(file_result, payload)
                self.assertEqual(file_result.stdout, self.invoke("/B", payload.hex()).stdout)
                version = 1 if length <= 11 else 2
                ecc = ERROR_CORRECT_Q if length <= 20 else ERROR_CORRECT_M
                qr = np.asarray(selected_reference(payload, version=version, ecc=ecc).get_matrix(), np.uint8)
                self.assertEqual(qr.shape, (21, 21) if version == 1 else (25, 25))
                x, y = (194, 74) if version == 1 else (170, 50)
                expected = np.full((400, 640), 255, np.uint8)
                expected[y:y + len(qr) * 12, x:x + len(qr) * 12] = (
                    (1 - qr).repeat(12, 0).repeat(12, 1) * 255)
                np.testing.assert_array_equal(frame, expected)

    def test_twelfth_special_byte_now_succeeds_in_v2(self):
        for value in (0x00, 0x1A, 0x80, 0xFF):
            payload = BYTE11 + bytes([value])
            self.assert_success(self.read_file(payload), payload)

    def test_positive_short_reads_5_6_1_0_now_succeed(self):
        payload = BYTE20[:12]
        self.assert_success(self.read_file(payload, read_plan="5,6,1,0"), payload)

    def test_twenty_bytes_short_reads_require_eof(self):
        self.assert_success(self.read_file(BYTE20, read_plan="5,6,9,0"), BYTE20)
        self.assert_success(self.read_file(BYTE20, read_plan=",".join(["1"] * 20 + ["0"])), BYTE20)

    def test_read_error_after_twenty_bytes_is_not_success(self):
        self.assert_file_error(self.read_file(BYTE20, read_plan="20,-1"), "file I/O failed.")

    def test_twenty_byte_close_error_prevents_encoding(self):
        self.assert_file_error(self.read_file(BYTE20, close_error=True), "file I/O failed.")

    def test_v2_encoder_error_prevents_graphics(self):
        self.assert_file_error(self.read_file(BYTE20, encoder_error=True), "binary encoding failed.")

    def test_v2_cr_lf_nul_and_trailing_1a_are_not_translated(self):
        payload = b"\r\nA\rB\n\x00\x80\xff\x1a" * 2
        self.assertEqual(len(payload), 20)
        self.assert_success(self.read_file(payload), payload)

    def test_empty_file(self):
        self.assert_file_error(self.read_file(b""), "file is empty.")

    def test_twenty_seven_byte_file_now_succeeds_in_v25(self):
        # Phase 3C-2で意図的に拡張した入力。
        # Phase 3B-2ではこの/F入力を拒否していた。
        # Intentional Phase 3C-2 change; Phase 3B-2 rejected this /F input.
        raw = BYTE26 + b"\x00"
        self.assert_success(self.read_file(raw), raw)

    def test_larger_file_stops_at_2954(self):
        # 読み取り計画の枯渇とC側の要求量検査により、2955バイト目を読ませない。
        # Plan exhaustion and C request assertions forbid a 2955th byte.
        self.assert_file_error(self.read_file(bytes(range(256)) * 32, read_plan="2954"),
                               "file must contain 1-2953 bytes.")

    def test_twenty_seventh_special_byte_now_succeeds_in_v25(self):
        for value in (0x00, 0x1A, 0x80, 0xFF):
            with self.subTest(value=value):
                raw = BYTE26 + bytes([value])
                self.assert_success(self.read_file(raw), raw)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assert_file_error(self.invoke("/F", "MISSING.BIN", cwd=directory),
                                   "could not open file.")

    def test_f_alone_remains_alphanumeric(self):
        self.assert_success(self.invoke("/F"), b"/F")

    def test_lowercase_f_is_usage_error(self):
        result = self.invoke("/f", "INPUT.BIN")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.startswith(b"Usage:"))

    def test_extra_arguments_are_usage_error(self):
        result = self.invoke("/F", "INPUT.BIN", "0", "11")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.startswith(b"Usage:"))

    def test_empty_path_is_rejected_before_open(self):
        self.assert_file_error(self.invoke("/F", ""), "file path must not be empty.")

    def test_relative_path_is_passed_unchanged(self):
        # ここではPOSIXの区切り文字を使う。
        # 実DOSのドライブ・バックスラッシュ処理は実機確認が必要。
        # POSIX separator here; real DOS backslash/drive handling needs hardware.
        self.assert_success(self.read_file(BYTE5, filename="Data/Mixed.BIN"), BYTE5)

    def test_filename_is_not_part_of_payload(self):
        first = self.read_file(BYTE5, filename="FIRST.BIN")
        second = self.read_file(BYTE5, filename="SECOND.BIN")
        self.assert_success(first, BYTE5)
        self.assert_success(second, BYTE5)
        self.assertEqual(first.stdout, second.stdout)

    def test_positive_short_reads_5_6_0_succeed(self):
        self.assert_success(self.read_file(BYTE11, read_plan="5,6,0"), BYTE11)

    def test_positive_short_reads_5_6_9_1_0_now_succeed_in_m(self):
        payload = BYTE20 + b"\x00"
        self.assert_success(self.read_file(payload, read_plan="5,6,9,1,0"), payload)

    def test_twenty_six_bytes_require_eof(self):
        for plan in ("26,0", "5,6,15,0", ",".join(["1"] * 26 + ["0"])):
            with self.subTest(plan=plan):
                self.assert_success(self.read_file(BYTE26, read_plan=plan), BYTE26)

    def test_twenty_seventh_byte_short_reads_now_require_eof_and_succeed(self):
        for plan in ("26,1,0", "5,6,15,1,0", "27,0"):
            with self.subTest(plan=plan):
                raw = BYTE26 + b"\x00"
                self.assert_success(self.read_file(raw, read_plan=plan), raw)

    def test_read_error_after_twenty_six_bytes_is_not_success(self):
        self.assert_file_error(self.read_file(BYTE26, read_plan="26,-1"), "file I/O failed.")

    def test_twenty_six_byte_close_error_prevents_encoding(self):
        self.assert_file_error(self.read_file(BYTE26, close_error=True), "file I/O failed.")

    def test_v2m_encoder_error_prevents_graphics(self):
        self.assert_file_error(self.read_file(BYTE26, encoder_error=True), "binary encoding failed.")

    def test_read_error_before_any_bytes(self):
        self.assert_file_error(self.read_file(BYTE5, read_plan="-1"), "file I/O failed.")

    def test_read_error_after_five_bytes(self):
        self.assert_file_error(self.read_file(BYTE11, read_plan="5,-1"), "file I/O failed.")

    def test_read_error_after_eleven_bytes_is_not_success(self):
        self.assert_file_error(self.read_file(BYTE11, read_plan="11,-1"), "file I/O failed.")

    def test_close_error_prevents_encoding(self):
        self.assert_file_error(self.read_file(BYTE5, close_error=True), "file I/O failed.")

    def test_empty_classification_survives_close_error(self):
        self.assert_file_error(self.read_file(b"", close_error=True), "file is empty.")

    def test_too_large_classification_survives_close_error(self):
        self.assert_file_error(self.read_file(v40.payload(2953) + b"\xff", close_error=True),
                               "file must contain 1-2953 bytes.")

    def test_read_error_also_attempts_failing_close(self):
        self.assert_file_error(self.read_file(BYTE11, read_plan="5,-1", close_error=True),
                               "file I/O failed.")

    def test_encoder_error_after_close_never_starts_graphics(self):
        # ファイルエラーと異なり、close後に符号化器へ一度到達する必要がある。
        # Unlike file errors, this must reach the encoder once, after close.
        result = self.read_file(BYTE5, encoder_error=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"Error: binary encoding failed.\r\n")

    def assert_profile(self, frame, length):
        size, x, y, scale, ecc = ((21, 194, 74, 12, 3) if length <= 11 else
                                 (25, 170, 50, 12, 3) if length <= 20 else
                                 (25, 170, 50, 12, 0) if length <= 26 else
                                 (117, 144, 24, 3, 1) if length <= 1273 else
                                 (177, 143, 23, 2, 1))
        matrix = (frame[y:y + size * scale:scale, x:x + size * scale:scale] == 0).astype(np.uint8)
        vertical = (0, 1, 2, 3, 4, 5, 7, 8, *range(size - 7, size))
        fmt = sum(int(matrix[row, 8]) << bit for bit, row in enumerate(vertical))
        self.assertEqual((fmt ^ 0x5412) >> 13, ecc)
        # C側の検査とは独立に、サイズ・原点・モジュール倍率を確認する。
        # Exact size, origin and module scale, independently of C's checks.
        expected = np.full((400, 640), 255, np.uint8)
        expected[y:y + size * scale, x:x + size * scale] = (
            (1 - matrix).repeat(scale, 0).repeat(scale, 1) * 255)
        np.testing.assert_array_equal(frame, expected)
        return matrix, ((fmt ^ 0x5412) >> 10) & 7

    def test_file_length_boundaries_and_each_profile(self):
        for length in (0, 1, 11, 12, 20, 21, 26, 27, 28, 255, 256, 1000,
                       1272, 1273, 1274, 1275, 2000, 2952, 2953, 2954):
            with self.subTest(length=length):
                raw = v25.payload(length)
                result = self.read_file(raw)
                if length == 0:
                    self.assert_file_error(result, "file is empty.")
                elif length == 2954:
                    self.assert_file_error(result, "file must contain 1-2953 bytes.")
                else:
                    self.assert_profile(self.assert_success(result, raw), length)

    def test_v25_main_raw_decode_and_all_frame_pixels(self):
        for length, mask in ((1000, 0), (1273, 7)):
            with self.subTest(length=length):
                raw = v25.payload(length)
                self.assertTrue({0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A} <= set(raw))
                result = self.read_file(raw, filename=f"BYTE{length}.BIN")
                frame = self.assert_success(result, raw)  # result.bytes only. / 日本語: result.bytesだけを使用する。
                actual, actual_mask = self.assert_profile(frame, length)
                self.assertEqual(actual_mask, mask)
                # Phase 3C-1と同様、外部符号化器は固定マスクの比較基準にだけ使用する。
                # External encoder only as fixed-mask oracle, as in Phase 3C-1.
                qr = np.asarray(v25.reference(raw, mask).get_matrix(), np.uint8)
                np.testing.assert_array_equal(actual, qr)
                expected = np.full((400, 640), 255, np.uint8)
                expected[24:375, 144:495] = (1 - qr).repeat(3, 0).repeat(3, 1) * 255
                np.testing.assert_array_equal(frame, expected)
                outer = np.pad(1 - qr, 4, constant_values=1).repeat(3, 0).repeat(3, 1) * 255
                self.assertEqual(outer.shape, (375, 375))
                np.testing.assert_array_equal(frame[12:387, 132:507], outer)
                self.assertEqual((132, 640 - 507, 12, 400 - 387), (132, 133, 12, 13))
                for white in (outer[:12], outer[-12:], outer[:, :12], outer[:, -12:],
                              frame[:12], frame[387:], frame[:, :132], frame[:, 507:]):
                    self.assertTrue(np.all(white == 255))
                np.testing.assert_array_equal(frame[372:375, 492:495], np.full((3, 3), (1 - qr[116, 116]) * 255))
                target = ROOT / "build/phase3c2-fixtures"
                target.mkdir(exist_ok=True)
                self.assertTrue(cv2.imwrite(str(target / f"v25-{length}-main-screen.png"), frame))

    def test_small_main_outputs_match_both_prechange_heads(self):
        baseline = json.loads((ROOT / "tests/phase3c2_small_baseline.json").read_text())
        for case in baseline["cases"]:
            raw = bytes.fromhex(case["hex"])
            for mode in ("/B", "/F"):
                with self.subTest(length=len(raw), mode=mode):
                    result = self.invoke(mode, raw.hex()) if mode == "/B" else self.read_file(raw)
                    frame = self.assert_success(result, raw)
                    matrix, mask = self.assert_profile(frame, len(raw))
                    self.assertEqual(matrix.shape, (case["size"], case["size"]))
                    self.assertEqual(mask, case["mask"])
                    self.assertEqual(hashlib.sha256(matrix.tobytes()).hexdigest(), case["matrix_sha256"])
                    self.assertEqual(hashlib.sha256(result.stdout).hexdigest(), case["framebuffer_sha256"])

    def test_b_boundaries_keep_26_byte_limit(self):
        for length in (1, 11, 12, 20, 21, 26, 27):
            with self.subTest(length=length):
                raw = v25.payload(length)
                result = self.invoke("/B", raw.hex())
                if length == 27:
                    self.assert_file_error(result, "binary hex must contain 2-52 characters with even length.")
                else:
                    self.assert_profile(self.assert_success(result, raw), length)

    def test_invalid_b_never_starts_graphics(self):
        for value, message in (("", "binary hex must contain 2-52 characters with even length."),
                               ("0", "binary hex must contain 2-52 characters with even length."),
                               ("GG", "binary hex must use 0-9, A-F, or a-f only.")):
            with self.subTest(value=value):
                self.assert_file_error(self.invoke("/B", value), message)

    def test_1000_bytes_multiple_short_reads(self):
        raw = v25.payload(1000)
        for plan in ("255,1,500,244,0", ",".join(["1"] * 1000 + ["0"])):
            with self.subTest(plan=plan[:40]):
                self.assert_success(self.read_file(raw, read_plan=plan), raw)

    def test_1273_bytes_require_eof(self):
        raw = v25.payload(1273)
        for plan in ("1273,0", "1000,200,73,0"):
            with self.subTest(plan=plan):
                self.assert_success(self.read_file(raw, read_plan=plan), raw)

    def test_1274_bytes_in_one_or_multiple_reads_now_succeed_in_v40(self):
        # 1274バイトはPhase 3C-2では拒否したが、Phase 3D-2では意図的に受け付ける。
        # Phase 3C-2 rejected 1274; Phase 3D-2 intentionally accepts it.
        raw = v25.payload(1273) + b"\x00"
        for plan in ("1273,1,0", "1000,200,73,1,0", "1274,0"):
            with self.subTest(plan=plan):
                self.assert_profile(self.assert_success(self.read_file(raw, read_plan=plan), raw), 1274)

    def test_1274th_special_byte_is_never_an_eof(self):
        for value in (0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A):
            with self.subTest(value=value):
                raw = v25.payload(1273) + bytes([value])
                self.assert_success(self.read_file(raw, read_plan="1273,1,0"), raw)

    def test_1273_read_error_is_not_eof(self):
        self.assert_file_error(self.read_file(v25.payload(1273), read_plan="1273,-1"), "file I/O failed.")

    def test_v25_partial_read_error(self):
        self.assert_file_error(self.read_file(v25.payload(1273), read_plan="1000,200,-1"), "file I/O failed.")

    def test_v25_close_error_prevents_encoding(self):
        self.assert_file_error(self.read_file(v25.payload(1273), close_error=True), "file I/O failed.")

    def test_v25_encoder_error_prevents_graphics(self):
        self.assert_file_error(self.read_file(v25.payload(1273), encoder_error=True), "binary encoding failed.")

    def test_v25_trailing_special_bytes_are_data(self):
        raw = v25.payload(1267) + bytes.fromhex("001a80ff0d0a")
        self.assertEqual(len(raw), 1273)
        self.assert_success(self.read_file(raw), raw)
        raw = raw[:-1] + b"\x1a"
        self.assert_success(self.read_file(raw), raw)

    def test_prechange_production_profiles_matrices_and_framebuffers(self):
        baseline = json.loads((ROOT / "tests/phase3d2_production_baseline.json").read_text())
        self.assertEqual(baseline["head"], "3eaecec1031d976d4403bd94c11105925c703b3f")
        for case in baseline["cases"]:
            with self.subTest(length=case["length"]):
                raw = v25.payload(case["length"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), case["payload_sha256"])
                result = self.read_file(raw)
                matrix, mask = self.assert_profile(self.assert_success(result, raw), len(raw))
                self.assertEqual(len(matrix), case["size"])
                self.assertEqual(mask, case["mask"])
                self.assertEqual(hashlib.sha256(matrix.tobytes()).hexdigest(), case["matrix_sha256"])
                self.assertEqual(hashlib.sha256(result.stdout).hexdigest(), case["framebuffer_sha256"])

    def test_v40_main_raw_decode_and_all_frame_pixels(self):
        for length in (1274, 2000, 2953):
            with self.subTest(length=length):
                raw = v40.payload(length)
                self.assertTrue({0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A} <= set(raw))
                frame = self.assert_success(self.read_file(raw, filename=f"BYTE{length}.BIN"), raw)
                actual, mask = self.assert_profile(frame, length)
                # 比較基準は固定マスクだけ。
                # 変更しない本番符号化器が自分でマスクを選ぶ。
                # Forced-mask oracle only; the unchanged encoder chooses its mask.
                qr = np.asarray(v40.reference(raw, mask).get_matrix(), np.uint8)
                np.testing.assert_array_equal(actual, qr)
                expected = np.full((400, 640), 255, np.uint8)
                expected[23:377, 143:497] = (1 - qr).repeat(2, 0).repeat(2, 1) * 255
                np.testing.assert_array_equal(frame, expected)  # All 256000 pixels. / 日本語: 256000画素すべてを確認する。
                outer = np.pad(1 - qr, 4, constant_values=1).repeat(2, 0).repeat(2, 1) * 255
                self.assertEqual(outer.shape, (370, 370))
                np.testing.assert_array_equal(frame[15:385, 135:505], outer)
                self.assertEqual((135, 640 - 505, 15, 400 - 385), (135, 135, 15, 15))
                for white in (outer[:8], outer[-8:], outer[:, :8], outer[:, -8:],
                              frame[:15], frame[385:], frame[:, :135], frame[:, 505:]):
                    self.assertTrue(np.all(white == 255))
                np.testing.assert_array_equal(frame[375:377, 495:497],
                                              np.full((2, 2), (1 - qr[176, 176]) * 255))
                target = ROOT / "build/phase3d2-fixtures"
                target.mkdir(exist_ok=True)
                self.assertTrue(cv2.imwrite(str(target / f"v40-{length}-main-screen.png"), frame))

    def test_2000_bytes_multiple_short_reads(self):
        raw = v40.payload(2000)
        self.assert_success(self.read_file(raw, read_plan="255,1,500,1000,244,0"), raw)

    def test_2953_bytes_require_eof(self):
        raw = v40.payload(2953)
        for plan in ("2953,0", "2000,900,53,0", ",".join(["1"] * 2953 + ["0"])):
            with self.subTest(plan=plan[:40]):
                self.assert_success(self.read_file(raw, read_plan=plan), raw)

    def test_2954_bytes_in_one_or_multiple_reads_reject(self):
        raw = v40.payload(2953) + b"\x00"
        for plan in ("2953,1", "2000,900,53,1", "2954"):
            with self.subTest(plan=plan):
                self.assert_file_error(self.read_file(raw, read_plan=plan), "file must contain 1-2953 bytes.")

    def test_2954th_special_byte_is_never_an_eof(self):
        for value in (0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A):
            with self.subTest(value=value):
                self.assert_file_error(self.read_file(v40.payload(2953) + bytes([value]), read_plan="2953,1"),
                                       "file must contain 1-2953 bytes.")

    def test_2953_read_error_is_not_eof(self):
        self.assert_file_error(self.read_file(v40.payload(2953), read_plan="2953,-1"), "file I/O failed.")

    def test_v40_partial_read_error(self):
        self.assert_file_error(self.read_file(v40.payload(2953), read_plan="2000,900,-1"), "file I/O failed.")

    def test_v40_close_error_prevents_encoding(self):
        self.assert_file_error(self.read_file(v40.payload(2953), close_error=True), "file I/O failed.")

    def test_v40_encoder_error_prevents_graphics(self):
        self.assert_file_error(self.read_file(v40.payload(2953), encoder_error=True), "binary encoding failed.")

    def test_v40_read_error_also_attempts_failing_close(self):
        self.assert_file_error(self.read_file(v40.payload(2953), read_plan="2000,900,-1", close_error=True),
                               "file I/O failed.")

    def test_v40_trailing_special_bytes_are_data(self):
        raw = v40.payload(2947) + bytes.fromhex("001a80ff0d0a")
        self.assertEqual(len(raw), 2953)
        self.assert_success(self.read_file(raw), raw)
        raw = raw[:-1] + b"\x1a"
        self.assert_success(self.read_file(raw), raw)

    def test_usage_reports_current_file_limit_and_unchanged_b_limit(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr,
                         b'Usage: QRTEST "TEXT" (1-16 QR Alphanumeric characters)\r\n'
                         b'       QRTEST /B HEX (1-26 bytes, 2-52 hex digits)\r\n'
                         b'       QRTEST /F PATH (whole binary file, 1-2953 bytes)\r\n'
                         b'       QRTEST /T PATH (transport payload, 1-2944 bytes)\r\n'
                         b'       QRTEST /T INDEX PATH (selected transport frame, index 0-65534)\r\n')


if __name__ == "__main__":
    unittest.main()
