# Phase 3B-1のV2追加とV1/V2のCLI選択を、生バイトの独立基準で検証する。
"""Phase 3B-1: V2-only additions and V1/V2 CLI selection, with raw-byte oracles."""

from functools import lru_cache
import hashlib
from pathlib import Path
import random
import subprocess
import sys
import unittest

import cv2
import numpy as np
from qrcode import base
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.util import MODE_8BIT_BYTE
import zxingcpp

from qr_byte_oracle import (data_codewords, ecc_codewords, reference,
                            selected_reference, samples as v1_samples)
from qr_mask_oracle import balance, penalties
from test_qr_encode import polynomial_multiply, samples as alnum_samples
from test_qr_mask import FORMATS

ROOT = Path(__file__).resolve().parents[1]
BYTE20 = bytes.fromhex("00011A7F80FFC3280041421020304050607080FF")


def samples():
    rng = random.Random(0x983B1)
    return (BYTE20, BYTE20[::-1], bytes(20), bytes([255]) * 20,
            bytes.fromhex("57a4c3ac933f1d612168d96d9f8814ac"), *(
        rng.randbytes(length) for length in range(1, 21) for _ in range(3)
    ))


@lru_cache(maxsize=512)
def inspect(mode, *arguments):
    return subprocess.run([str(ROOT / "build/test_qr_v2"), mode, *arguments],
                          check=True, capture_output=True, text=True).stdout.splitlines()


def parse_matrix(rows):
    assert len(rows) == 25 and all(len(row) == 25 and set(row) <= {"0", "1"} for row in rows)
    return np.array([[int(cell) for cell in row] for row in rows], dtype=np.uint8)


def matrix(payload):
    return parse_matrix(inspect("matrix", payload.hex()))


def candidates(payload):
    rows = inspect("candidates", payload.hex())
    assert len(rows) == 8 * 26
    scores = [tuple(map(int, rows[mask * 26].split()[2:])) for mask in range(8)]
    matrices = [parse_matrix(rows[mask * 26 + 1:(mask + 1) * 26]) for mask in range(8)]
    return scores, matrices


def read_format(qr):
    vertical = (0, 1, 2, 3, 4, 5, 7, 8, 18, 19, 20, 21, 22, 23, 24)
    horizontal = (24, 23, 22, 21, 20, 19, 18, 17, 7, 5, 4, 3, 2, 1, 0)
    return (sum(int(qr[row, 8]) << bit for bit, row in enumerate(vertical)),
            sum(int(qr[8, col]) << bit for bit, col in enumerate(horizontal)))


def mask_at(mask, y, x):
    return ((y + x) % 2 == 0, y % 2 == 0, x % 3 == 0, (y + x) % 3 == 0,
            (y // 2 + x // 3) % 2 == 0, (y * x) % 2 + (y * x) % 3 == 0,
            ((y * x) % 2 + (y * x) % 3) % 2 == 0,
            ((y + x) % 2 + (y * x) % 3) % 2 == 0)[mask]


def layout():
    rows = inspect("layout")
    assert len(rows) == 75
    return tuple(parse_matrix(rows[start:start + 25]) for start in (0, 25, 50))


def positions():
    return [tuple(map(int, line.split())) for line in inspect("placement")]


def expected_frame(payload):
    version = 1 if len(payload) <= 11 else 2
    qr = np.asarray(selected_reference(payload, version=version).get_matrix(), dtype=np.uint8)
    total = (len(qr) + 8) * 12
    x, y = (640 - total) // 2 + 48, (400 - total) // 2 + 48
    frame = np.full((400, 640), 255, dtype=np.uint8)
    frame[y:y + len(qr) * 12, x:x + len(qr) * 12] = (1 - qr).repeat(12, 0).repeat(12, 1) * 255
    return frame


class V2EncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "build/test_qr_v2", "build/test_qr_encode"], cwd=ROOT,
                       check=True, stdout=subprocess.PIPE)

    def test_api_lengths_and_untouched_errors(self):
        for length in (0, 1, 11, 12, 19, 20, 21):
            with self.subTest(length=length):
                result = subprocess.run([str(ROOT / "build/test_qr_v2"), "matrix",
                                         bytes(range(length)).hex()], capture_output=True)
                self.assertEqual(result.returncode, 0 if 1 <= length <= 20 else 1)
                if result.returncode == 0:
                    self.assertEqual(len(result.stdout.splitlines()), 25)
                else:
                    self.assertEqual(result.stdout, b"")  # C checks all sentinels/canaries. / 日本語: C側ですべての番兵値と境界監視値を確認する。

    def test_invalid_length_precedes_pointer_access(self):
        self.assertEqual(inspect("invalid-pointers"), [])

    def test_oracle_forces_v2_q_byte_one_block(self):
        qr = reference(b"HELLO", version=2)
        self.assertEqual((qr.version, qr.error_correction, qr.border), (2, ERROR_CORRECT_Q, 0))
        self.assertEqual(len(qr.data_list), 1)
        self.assertEqual(qr.data_list[0].mode, MODE_8BIT_BYTE)
        self.assertEqual(qr.data_list[0].data, b"HELLO")
        blocks = base.rs_blocks(2, ERROR_CORRECT_Q)
        self.assertEqual([(block.total_count, block.data_count) for block in blocks], [(44, 22)])

    def test_data_fields_terminator_and_padding_all_twenty_lengths(self):
        for length in range(1, 21):
            payload = BYTE20[:length]
            actual = bytes.fromhex(inspect("data", payload.hex())[0])
            self.assertEqual(actual, data_codewords(payload, version=2))
            self.assertEqual(list(actual), reference(payload, version=2).data_cache[:22])
            bits = "".join(f"{value:08b}" for value in actual)
            self.assertEqual(len(bits), 176)
            self.assertEqual(bits[:12], "0100" + f"{length:08b}")
            self.assertEqual(bits[12:12 + length * 8], "".join(f"{value:08b}" for value in payload))
            self.assertEqual(bits[12 + length * 8:16 + length * 8], "0000")
            self.assertEqual(actual[length + 2:], bytes((0xEC, 0x11)[i % 2]
                                                        for i in range(20 - length)))

    def test_twenty_byte_binary_vector_fills_176_bits(self):
        self.assertEqual(len(BYTE20), 20)
        self.assertTrue({0, 0x1A, 0x80, 0xFF} <= set(BYTE20))
        self.assertEqual(bytes.fromhex(inspect("data", BYTE20.hex())[0]),
                         bytes.fromhex("41400011a7f80ffc3280041421020304050607080ff0"))

    def test_generator_product_and_twenty_two_roots(self):
        coefficients, root = [1], 1  # Ascending powers, independent carryless multiplication. / 日本語: 昇べき順と独立した桁上がりなし乗算を使う。
        for _ in range(22):
            expanded = [0] * (len(coefficients) + 1)
            for power, value in enumerate(coefficients):
                expanded[power] ^= polynomial_multiply(value, root)
                expanded[power + 1] ^= value
            coefficients = expanded
            root = polynomial_multiply(root, 2)
        descending = list(reversed(coefficients))
        actual = [1, *bytes.fromhex(inspect("generator")[0])]
        self.assertEqual(actual, descending)
        for power in range(22):
            value = 0
            for coefficient in actual:
                value = polynomial_multiply(value, base.gexp(power)) ^ coefficient
            self.assertEqual(value, 0)

    def test_ecc_arbitrary_data_and_syndromes(self):
        rng = random.Random(0x22ECC)
        cases = [bytes(22), bytes([255]) * 22, bytes(range(22))]
        cases += [bytes([0] * i + [1] + [0] * (21 - i)) for i in range(22)]
        cases += [rng.randbytes(22) for _ in range(64)]
        results = inspect("ecc", *(data.hex() for data in cases))
        self.assertEqual(len(results), len(cases))
        for data, result in zip(cases, results, strict=True):
            words = bytes.fromhex(result)
            self.assertEqual(words, data + ecc_codewords(data, length=22))
            for power in range(22):
                value = 0
                for coefficient in words:
                    value = polynomial_multiply(value, base.gexp(power)) ^ coefficient
                self.assertEqual(value, 0)

    def test_data_and_ecc_samples_match_reference(self):
        for payload, result in zip(samples(), inspect("codewords", *(p.hex() for p in samples())), strict=True):
            words = bytes.fromhex(result)
            self.assertEqual(len(words), 44)
            self.assertEqual(list(words), reference(payload, version=2).data_cache)

    def test_function_reservations_and_patterns(self):
        reserved, functions, zeros = layout()
        expected = np.zeros((25, 25), np.uint8)
        expected[:9, :9] = expected[:9, 17:] = expected[17:, :9] = 1
        expected[6, :] = expected[:, 6] = 1
        expected[16:21, 16:21] = 1
        np.testing.assert_array_equal(reserved, expected)
        self.assertEqual(int(reserved.sum()), 266)
        finder = np.ones((7, 7), np.uint8)
        finder[1:6, 1:6] = 0
        finder[2:5, 2:5] = 1
        for y, x in ((0, 0), (0, 18), (18, 0)):
            np.testing.assert_array_equal(functions[y:y + 7, x:x + 7], finder)
        for edge in (functions[7, :8], functions[:8, 7], functions[7, 17:],
                     functions[:8, 17], functions[17, :8], functions[17:, 7]):
            self.assertFalse(edge.any())
        np.testing.assert_array_equal(functions[6, 8:17], [1, 0, 1, 0, 1, 0, 1, 0, 1])
        np.testing.assert_array_equal(functions[8:17, 6], functions[6, 8:17])
        self.assertEqual(functions[17, 8], 1)
        ref = np.asarray(reference(BYTE20, version=2).get_matrix(), np.uint8)
        np.testing.assert_array_equal(functions[reserved == 1], ref[reserved == 1])
        np.testing.assert_array_equal(zeros[reserved == 1], functions[reserved == 1])

    def test_single_alignment_pattern_center_18_18(self):
        reserved, functions, _ = layout()
        expected = np.ones((5, 5), np.uint8)
        expected[1:4, 1:4] = 0
        expected[2, 2] = 1
        np.testing.assert_array_equal(functions[16:21, 16:21], expected)
        self.assertTrue(reserved[16:21, 16:21].all())

    def test_352_source_bits_unique_ordered_and_complete(self):
        cells = positions()  # C toggles each bit separately and checks function cells stay intact. / 日本語: C側で各ビットを個別に反転し、機能セルが保持されることを検査する。
        self.assertEqual(len(cells), 352)
        self.assertEqual(len(set(cells)), 352)
        self.assertEqual(cells[:4], [(24, 24), (24, 23), (23, 24), (23, 23)])
        reserved, _, _ = layout()
        self.assertTrue(all(reserved[y, x] == 0 for y, x in cells))
        for payload in (BYTE20, bytes(1), bytes(range(12)), bytes([255]) * 19):
            _, matrices = candidates(payload)
            bits = [byte >> bit & 1 for byte in reference(payload, version=2).data_cache
                    for bit in range(7, -1, -1)]
            for mask, qr in enumerate(matrices):
                self.assertEqual([int(qr[y, x]) ^ mask_at(mask, y, x) for y, x in cells], bits)

    def test_seven_remainder_bits_are_zero_then_masked(self):
        reserved, _, _ = layout()
        remaining = set(zip(*np.where(reserved == 0))) - set(positions())
        self.assertEqual(remaining, {(13, 0), (14, 1), (14, 0), (15, 1), (15, 0), (16, 1), (16, 0)})
        for payload in (BYTE20, b"\xff"):
            _, matrices = candidates(payload)
            for mask, qr in enumerate(matrices):
                self.assertEqual([int(qr[y, x]) ^ mask_at(mask, y, x) for y, x in remaining], [0] * 7)

    def test_all_masks_at_all_625_coordinates(self):
        rows = inspect("maskmaps")
        self.assertEqual(len(rows), 200)
        for mask in range(8):
            expected = [[mask_at(mask, y, x) for x in range(25)] for y in range(25)]
            np.testing.assert_array_equal(parse_matrix(rows[mask * 25:(mask + 1) * 25]), expected)

    def test_all_candidates_format_and_625_cells_match_oracle(self):
        for payload in samples():
            scores, matrices = candidates(payload)
            for mask, qr in enumerate(matrices):
                with self.subTest(hex=payload.hex(), mask=mask):
                    np.testing.assert_array_equal(qr, reference(payload, mask, version=2).get_matrix())
                    self.assertEqual(read_format(qr), (FORMATS[mask], FORMATS[mask]))
                    self.assertEqual(scores[mask], penalties(qr))
            chosen = min(range(8), key=lambda mask: (scores[mask][-1], mask))
            np.testing.assert_array_equal(matrix(payload), matrices[chosen])

    def test_representatives_select_each_of_eight_masks(self):
        values = (BYTE20.hex(), "17f553", "58bf2a4c89", "94", "5b565456b167c4",
                  "57a4c3ac933f1d612168d96d9f8814ac", "62", "247b")
        for mask, value in enumerate(values):
            payload = bytes.fromhex(value)
            self.assertEqual(read_format(matrix(payload)), (FORMATS[mask], FORMATS[mask]))
            np.testing.assert_array_equal(matrix(payload), reference(payload, mask, version=2).get_matrix())

    def test_v2_tie_selects_lowest_mask(self):
        payload = bytes.fromhex("4b3ceb70")
        scores, matrices = candidates(payload)
        self.assertEqual([score[-1] for score in scores],
                         [1198, 1207, 1198, 1339, 1513, 1297, 1269, 1328])
        np.testing.assert_array_equal(matrix(payload), matrices[0])
        self.assertFalse(np.array_equal(matrices[0], matrices[2]))

    def test_penalty_artificial_v2_symbols(self):
        rng = np.random.default_rng(0x3B1)
        cases = [np.zeros((25, 25), np.uint8), np.ones((25, 25), np.uint8)]
        cases += [(np.indices((25, 25)).sum(axis=0) % 2).astype(np.uint8)]
        cases += [(rng.random((25, 25)) < density).astype(np.uint8)
                  for density in (0.1, 0.3, 0.5, 0.7, 0.9) for _ in range(12)]
        # 追加された端の行・列で長いラン、角の2×2、端の拡大N3を試す。
        # New last rows/columns: long runs, 2x2 at the corner, scaled N3 at the edge.
        for width in range(1, 26):
            qr = cases[2].copy()
            qr[24, 25 - width:] = 1
            cases.extend((qr, qr.T))
        for line in ("1011101" + "0" * 18, "0" * 4 + "111000111111111000111"):
            qr = np.zeros((25, 25), np.uint8)
            qr[24] = list(map(int, line.ljust(25, "0")))
            cases.extend((qr, qr.T))
        arguments = ["".join(map(str, qr.flat)) for qr in cases]
        scores = inspect("penalty", *arguments)
        for qr, score in zip(cases, scores, strict=True):
            self.assertEqual(tuple(map(int, score.split())), penalties(qr))
        self.assertEqual(tuple(map(int, scores[0].split())), (1150, 1728, 0, 90, 2968))

    def test_n4_all_626_dark_counts(self):
        self.assertEqual(list(map(int, inspect("balance"))), [balance(dark, 25) for dark in range(626)])

    def test_repeated_v1_v2_alnum_calls_reset_shared_workspace(self):
        payloads = (BYTE20, b"\x00", BYTE20[:11], BYTE20[:12], BYTE20[::-1], b"\xff") * 2
        rows = inspect("mixed", *(payload.hex() for payload in payloads))
        self.assertEqual(len(rows), 25 * len(payloads))
        for index, payload in enumerate(payloads):
            np.testing.assert_array_equal(parse_matrix(rows[index * 25:(index + 1) * 25]),
                                          selected_reference(payload, version=2).get_matrix())

    def test_zxing_raw_bytes_all_lengths_and_special_vectors(self):
        for payload in (BYTE20, BYTE20[::-1], *(BYTE20[:length] for length in range(1, 21))):
            qr = matrix(payload)
            image = np.pad(1 - qr, 4, constant_values=1).repeat(12, 0).repeat(12, 1) * 255
            self.assertEqual(image.shape, (396, 396))
            decoded = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.QRCode,
                                           try_downscale=False)
            self.assertIsNotNone(decoded)
            self.assertTrue(decoded.valid)
            self.assertEqual(decoded.bytes, payload)

    def test_v1_matrices_match_phase3a_snapshot(self):
        # 変更のない開始HEADのホストドライバーから取得した標準出力のSHA-256。
        # Byte入力342件と英数字入力516件を順序付きで含む。
        # SHA-256 of stdout captured from the clean starting HEAD's host driver.
        # Includes all 342 Byte fixtures and 516 Alphanumeric fixtures in order.
        for mode, arguments, expected in (
            ("bytes-matrix", [p.hex() for p in v1_samples()], "b58e5db51f61be0f853b50af595ac56f4237fda0957d4a7d947f49203cdb3fc7"),
            ("matrix", alnum_samples(), "9ea6fa6681ec0c77f6baf558c08cbe5e939f24a887dc29f9928dc42a4baaa5f2"),
        ):
            result = subprocess.run([str(ROOT / "build/test_qr_encode"), mode, *arguments],
                                    check=True, capture_output=True)
            self.assertEqual(hashlib.sha256(result.stdout).hexdigest(), expected)


class V2MainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True, stdout=subprocess.PIPE)

    def render(self, payload):
        result = subprocess.run([str(ROOT / "build/test_main_render"), "/B", payload.hex()],
                                check=True, capture_output=True)
        self.assertEqual(result.stderr, b"")
        return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)

    def test_cli_outputs_match_phase3b1_production_snapshot(self):
        # ソース変更前のクリーンなHEADから取得した記録。
        # 記載コミットから採取した基準であり、外部の基準実装ではない。
        # 長さ1～20と12～20の3変種を含む、順序固定の47ケース。
        # 生符号語、LF付きASCII行列、ヘッダー込みPGM標準出力をそれぞれハッシュする。
        # ケースごとの選択マスク1バイトも別にハッシュする。
        # Captured BEFORE source changes from clean HEAD
        # 9c758ec9ea361d2e42193ed9b45d2e5a3330cae8, not an external oracle.
        # 47 ordered cases: all 1..20 lengths, plus three variants for 12..20.
        # Hash raw codewords, ASCII matrix rows with LF, PGM stdout including
        # its header, and one selected-mask byte per case, respectively.
        cases = [BYTE20[:length] for length in range(1, 21)]
        cases += [payload for length in range(12, 21) for payload in
                  (bytes(range(length)), bytes([255]) * length,
                   bytes((37 * index + length) % 256 for index in range(length)))]
        hashes = {key: hashlib.sha256() for key in ("codewords", "matrix", "framebuffer", "masks")}
        for payload in cases:
            v1 = len(payload) <= 11
            driver = ROOT / ("build/test_qr_encode" if v1 else "build/test_qr_v2")
            prefix = "bytes-" if v1 else ""
            words = subprocess.run([str(driver), prefix + "codewords", payload.hex()],
                                   check=True, capture_output=True).stdout
            rows = subprocess.run([str(driver), prefix + "matrix", payload.hex()],
                                  check=True, capture_output=True).stdout
            frame = subprocess.run([str(ROOT / "build/test_main_render"), "/B", payload.hex()],
                                   check=True, capture_output=True).stdout
            matrix_rows = rows.splitlines()
            vertical = (0, 1, 2, 3, 4, 5, 7, 8, *range(len(matrix_rows) - 7, len(matrix_rows)))
            fmt = sum((matrix_rows[y][8] - 48) << bit for bit, y in enumerate(vertical))
            self.assertEqual((fmt ^ 0x5412) >> 13, 3)  # Q, including V2. / 日本語: V2を含めECCはQ。
            hashes["masks"].update(bytes([((fmt ^ 0x5412) >> 10) & 7]))
            hashes["codewords"].update(bytes.fromhex(words.decode("ascii")))
            hashes["matrix"].update(rows)
            hashes["framebuffer"].update(frame)
        self.assertEqual({key: digest.hexdigest() for key, digest in hashes.items()}, {
            "codewords": "439be8d250c7e8787463a92d59289719c28575bacd2d6f518345f63a96c6db2b",
            "matrix": "b2e8364f4e7a3f0f7ac3fcd3b72d908516258e211f59ce108ce054165782eac9",
            "framebuffer": "9475410faccda5fa461370a478a42405765db1a54812bd8917f958ca092dbf70",
            "masks": "e2bb965543e1b6b8424a9cefcb5884b0c1f4a0966d944b53f46f11830e078f57",
        })

    def test_b_all_lengths_select_v1_or_v2_and_match_every_pixel(self):
        for length in range(1, 21):
            payload = BYTE20[:length]
            frame = self.render(payload)
            np.testing.assert_array_equal(frame, expected_frame(payload))
            result = zxingcpp.read_barcode(frame, formats=zxingcpp.BarcodeFormat.QRCode,
                                          try_downscale=False)
            self.assertIsNotNone(result)
            self.assertTrue(result.valid)
            self.assertEqual(result.bytes, payload)

    def test_v2_quiet_zone_and_two_pixel_vertical_margin(self):
        frame = self.render(BYTE20)
        qr = np.pad(1 - matrix(BYTE20), 4, constant_values=1).repeat(12, 0).repeat(12, 1) * 255
        np.testing.assert_array_equal(frame[2:398, 122:518], qr)
        for margin in (frame[:2], frame[398:], qr[:48], qr[348:], qr[:, :48], qr[:, 348:]):
            self.assertTrue(np.all(margin == 255))
        self.assertTrue(cv2.imwrite(str(ROOT / "build/screen_v2_bytes.png"), frame))

    def test_twenty_seven_byte_hex_rejected_before_encoding(self):
        result = subprocess.run([str(ROOT / "build/test_main_render"), "/B", (BYTE20 + bytes(7)).hex()],
                                capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"Error: binary hex must contain 2-52 characters with even length.\r\n")

    def test_new_length_nonhex_is_still_rejected(self):
        for text in ("0" * 39 + "G", "G" + "0" * 39):
            result = subprocess.run([str(ROOT / "build/test_main_render"), "/B", text], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"binary hex must use", result.stderr)

    def test_production_photo_decoder_on_host_v2_image(self):
        path = ROOT / "build/v2_host_decode.png"
        self.assertTrue(cv2.imwrite(str(path), self.render(BYTE20)))
        result = subprocess.run([sys.executable, str(ROOT / "windows/decode_binary_photo.py"),
                                 str(path), "--expect-hex", BYTE20.hex()], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"Length: 20", result.stdout)
        self.assertIn(BYTE20.hex().upper().encode("ascii"), result.stdout)


if __name__ == "__main__":
    unittest.main()
