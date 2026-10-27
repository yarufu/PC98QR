# Phase 3B-2の固定V2-M API、生バイト、CLIの21～26バイト拡張を検証する。
"""Phase 3B-2: fixed V2-M API, raw bytes, and CLI's additional 21..26 range."""

from functools import lru_cache
from pathlib import Path
import random
import subprocess
import sys
import unittest

import cv2
import numpy as np
from qrcode import base
from qrcode.constants import ERROR_CORRECT_M, ERROR_CORRECT_Q
from qrcode.util import MODE_8BIT_BYTE
import zxingcpp

from qr_byte_oracle import data_codewords, ecc_codewords, reference, selected_reference
from qr_mask_oracle import penalties
from test_qr_encode import polynomial_multiply
from test_qr_v2 import inspect as inspect_q, mask_at, parse_matrix, read_format

ROOT = Path(__file__).resolve().parents[1]
BYTE26 = bytes.fromhex("00011A7F80FFC3280041421020304050607080FF001A80FF0D0A")
FORMATS_M = (0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0)
REPRESENTATIVES = ("78", "709d263ae9", "4e5fb9", "a8dd62c3e715",
                   "3ea46ba6afc063a1a2ee86", "71aa81", "91cd", "3b0e73c6")
TIE = bytes.fromhex("22f88bbacd60c7b153b1190b1d")


def samples():
    rng = random.Random(0x983B2)
    return (BYTE26, BYTE26[::-1], bytes(26), bytes([255]) * 26, TIE,
            *(bytes.fromhex(value) for value in REPRESENTATIVES), *(
                rng.randbytes(length) for length in range(1, 27) for _ in range(3)))


@lru_cache(maxsize=512)
def inspect(mode, *arguments):
    return subprocess.run([str(ROOT / "build/test_qr_v2"), "m-" + mode, *arguments],
                          check=True, capture_output=True, text=True).stdout.splitlines()


def matrix(payload):
    return parse_matrix(inspect("matrix", payload.hex()))


def candidates(payload):
    rows = inspect("candidates", payload.hex())
    assert len(rows) == 8 * 26
    scores = [tuple(map(int, rows[mask * 26].split()[2:])) for mask in range(8)]
    matrices = [parse_matrix(rows[mask * 26 + 1:(mask + 1) * 26]) for mask in range(8)]
    return scores, matrices


def reference_m(payload, mask=0):
    return reference(payload, mask, version=2, ecc=ERROR_CORRECT_M)


def roots():
    value = 1
    result = []
    for _ in range(16):
        result.append(value)
        value = polynomial_multiply(value, 2)
    return result


def evaluate(coefficients, root):
    value = 0
    for coefficient in coefficients:
        value = polynomial_multiply(value, root) ^ coefficient
    return value


class V2MEncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "build/test_qr_v2"], cwd=ROOT, check=True, stdout=subprocess.PIPE)

    def test_api_all_lengths_and_untouched_errors(self):
        for length in range(28):
            with self.subTest(length=length):
                result = subprocess.run([str(ROOT / "build/test_qr_v2"), "m-matrix",
                                         bytes(range(length)).hex()], capture_output=True)
                self.assertEqual(result.returncode, 0 if 1 <= length <= 26 else 1)
                if result.returncode == 0:
                    parse_matrix(result.stdout.decode("ascii").splitlines())
                else:
                    self.assertEqual(result.stdout, b"")  # Driver checks input and 625 sentinels. / 日本語: ドライバーが入力と625セルの番兵値を確認する。

    def test_invalid_lengths_precede_pointer_access(self):
        self.assertEqual(inspect("invalid-pointers"), [])  # 0, 27 and UINT_MAX, both pointers NULL. / 日本語: 長さ0・27・UINT_MAXで両ポインタをNULLにする。

    def test_reference_forces_v2_m_byte_single_block(self):
        qr = reference_m(b"HELLO", mask=3)
        self.assertEqual((qr.version, qr.error_correction, qr.mask_pattern, qr.border),
                         (2, ERROR_CORRECT_M, 3, 0))
        self.assertEqual(len(qr.data_list), 1)
        self.assertEqual(qr.data_list[0].mode, MODE_8BIT_BYTE)
        self.assertEqual(qr.data_list[0].data, b"HELLO")
        blocks = base.rs_blocks(2, ERROR_CORRECT_M)
        self.assertEqual([(block.total_count, block.data_count) for block in blocks], [(44, 28)])

    def test_q_and_m_references_do_not_share_cache_entries(self):
        for ecc in (ERROR_CORRECT_Q, ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_M):
            qr = selected_reference(BYTE26[:20], version=2, ecc=ecc)
            self.assertEqual(qr.error_correction, ecc)
            self.assertEqual(len(data_codewords(BYTE26[:20], version=2, ecc=ecc)),
                             22 if ecc == ERROR_CORRECT_Q else 28)
            fmt = read_format(np.asarray(qr.get_matrix(), np.uint8))[0]
            self.assertEqual((fmt ^ 0x5412) >> 13, 3 if ecc == ERROR_CORRECT_Q else 0)

    def test_data_fields_and_padding_all_26_lengths(self):
        for length in range(1, 27):
            payload = BYTE26[:length]
            actual = bytes.fromhex(inspect("data", payload.hex())[0])
            self.assertEqual(len(actual), 28)
            self.assertEqual(actual, data_codewords(payload, version=2, ecc=ERROR_CORRECT_M))
            self.assertEqual(list(actual), reference_m(payload).data_cache[:28])
            bits = "".join(f"{value:08b}" for value in actual)
            self.assertEqual(bits[:12], "0100" + f"{length:08b}")
            self.assertEqual(bits[12:12 + 8 * length], "".join(f"{value:08b}" for value in payload))
            self.assertEqual(bits[12 + 8 * length:16 + 8 * length], "0000")
            self.assertEqual(actual[length + 2:], bytes((0xEC, 0x11)[i % 2]
                                                       for i in range(26 - length)))

    def test_24_25_26_byte_padding_boundaries(self):
        for length, padding in ((24, b"\xec\x11"), (25, b"\xec"), (26, b"")):
            actual = bytes.fromhex(inspect("data", BYTE26[:length].hex())[0])
            self.assertEqual(actual[length + 2:], padding)
        self.assertEqual(len(BYTE26), 26)
        self.assertEqual(len(BYTE26.hex()), 52)
        self.assertEqual(4 + 8 + len(BYTE26) * 8 + 4, 224)
        self.assertTrue({0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A} <= set(BYTE26))
        self.assertIn(b"\xc3\x28", BYTE26)

    def test_generator_independent_product_and_all_16_roots(self):
        # 昇べき順係数、桁上がりなし乗算、多項式剰余を使う。
        # 外部の生成多項式表や有限体演算は使わない。
        # Ascending coefficients, carryless products and polynomial reduction;
        # no external generator table or external field arithmetic is used.
        coefficients = [1]
        for root in roots():
            expanded = [0] * (len(coefficients) + 1)
            for power, value in enumerate(coefficients):
                expanded[power] ^= polynomial_multiply(value, root)
                expanded[power + 1] ^= value
            coefficients = expanded
        actual = [1, *bytes.fromhex(inspect("generator")[0])]
        self.assertEqual(len(actual), 17)
        self.assertEqual(actual, list(reversed(coefficients)))
        self.assertEqual([evaluate(actual, root) for root in roots()], [0] * 16)

    def test_arbitrary_data_ecc_and_all_16_syndromes(self):
        rng = random.Random(0x16ECC)
        cases = [bytes(28), bytes([255]) * 28, bytes(range(28))]
        cases += [bytes([0] * i + [1] + [0] * (27 - i)) for i in range(28)]
        cases += [rng.randbytes(28) for _ in range(64)]
        results = inspect("ecc", *(data.hex() for data in cases))
        for data, result in zip(cases, results, strict=True):
            words = bytes.fromhex(result)
            self.assertEqual(len(words), 44)
            self.assertEqual(words, data + ecc_codewords(data, length=16))
            self.assertEqual([evaluate(words, root) for root in roots()], [0] * 16)

    def test_payload_codewords_and_vector_ecc_match_reference(self):
        for payload, result in zip(samples(), inspect("codewords", *(p.hex() for p in samples())), strict=True):
            words = bytes.fromhex(result)
            self.assertEqual(len(words), 44)
            self.assertEqual(list(words), reference_m(payload).data_cache)
            self.assertEqual(words[28:], ecc_codewords(words[:28], length=16))
            self.assertEqual([evaluate(words, root) for root in roots()], [0] * 16)

    def test_all_eight_m_format_words_bch_and_recovered_fields(self):
        actual = tuple(int(word, 16) for word in inspect("formats"))
        self.assertEqual(actual, FORMATS_M)
        for mask, word in enumerate(actual):
            remainder = 0
            for bit in f"{mask:05b}" + "0" * 10:  # M = 00, unlike Q = 11. / 日本語: Mの形式値は00で、Qの11とは異なる。
                remainder = (remainder << 1) | int(bit)
                if remainder & 0x400:
                    remainder ^= 0x537
            self.assertEqual(word, ((mask << 10) | remainder) ^ 0x5412)
            self.assertEqual((word ^ 0x5412) >> 13, 0)
            self.assertEqual(((word ^ 0x5412) >> 10) & 7, mask)

    def test_geometry_matches_q_except_format_values(self):
        mrows, qrows = inspect("layout"), inspect_q("layout")
        reserved, functions, zeros = (parse_matrix(mrows[i:i + 25]) for i in (0, 25, 50))
        qreserved, qfunctions = (parse_matrix(qrows[i:i + 25]) for i in (0, 25))
        np.testing.assert_array_equal(reserved, qreserved)
        self.assertEqual(int(reserved.sum()), 266)
        self.assertEqual(int((reserved == 0).sum()), 359)
        nonformat = reserved.copy()
        for y in (0, 1, 2, 3, 4, 5, 7, 8, 18, 19, 20, 21, 22, 23, 24):
            nonformat[y, 8] = 0
        for x in (24, 23, 22, 21, 20, 19, 18, 17, 7, 5, 4, 3, 2, 1, 0):
            nonformat[8, x] = 0
        self.assertEqual(int(nonformat.sum()), 236)
        np.testing.assert_array_equal(functions[nonformat == 1], qfunctions[nonformat == 1])
        np.testing.assert_array_equal(zeros[reserved == 1], functions[reserved == 1])
        self.assertEqual(read_format(functions), (FORMATS_M[0], FORMATS_M[0]))
        self.assertEqual(functions[17, 8], 1)
        alignment = np.ones((5, 5), np.uint8)
        alignment[1:4, 1:4] = 0
        alignment[2, 2] = 1
        np.testing.assert_array_equal(functions[16:21, 16:21], alignment)

    def test_352_bit_placement_and_seven_masked_remainders(self):
        cells = [tuple(map(int, row.split())) for row in inspect("placement")]
        self.assertEqual(len(cells), 352)
        self.assertEqual(len(set(cells)), 352)
        self.assertEqual(cells, [tuple(map(int, row.split())) for row in inspect_q("placement")])
        reserved = parse_matrix(inspect("layout")[:25])
        self.assertTrue(all(reserved[y, x] == 0 for y, x in cells))
        remaining = set(zip(*np.where(reserved == 0))) - set(cells)
        self.assertEqual(remaining, {(13, 0), (14, 1), (14, 0), (15, 1), (15, 0), (16, 1), (16, 0)})
        for payload in (BYTE26, b"\x00", bytes([255]) * 26):
            _, matrices = candidates(payload)
            bits = [value >> bit & 1 for value in reference_m(payload).data_cache
                    for bit in range(7, -1, -1)]
            for mask, qr in enumerate(matrices):
                self.assertEqual([int(qr[y, x]) ^ mask_at(mask, y, x) for y, x in cells], bits)
                self.assertEqual([int(qr[y, x]) ^ mask_at(mask, y, x) for y, x in remaining], [0] * 7)

    def test_all_candidates_625_cells_penalties_and_best_mask(self):
        for payload in samples():
            scores, matrices = candidates(payload)
            for mask, qr in enumerate(matrices):
                with self.subTest(hex=payload.hex(), mask=mask):
                    np.testing.assert_array_equal(qr, reference_m(payload, mask).get_matrix())
                    self.assertEqual(read_format(qr), (FORMATS_M[mask], FORMATS_M[mask]))
                    # 完成したM形式の候補を採点し、Q形式の採点後にMへ書き換える方式にはしない。
                    # Completed M-format candidate, not a Q-format score later patched to M.
                    self.assertEqual(scores[mask], penalties(qr))
            chosen = min(range(8), key=lambda mask: (scores[mask][-1], mask))
            np.testing.assert_array_equal(matrix(payload), matrices[chosen])

    def test_representatives_select_every_mask(self):
        for mask, value in enumerate(REPRESENTATIVES):
            self.assertEqual(read_format(matrix(bytes.fromhex(value))), (FORMATS_M[mask], FORMATS_M[mask]))

    def test_tie_selects_lowest_mask(self):
        scores, matrices = candidates(TIE)
        self.assertEqual([score[-1] for score in scores],
                         [1276, 1195, 1210, 1185, 1185, 1194, 1273, 1282])
        np.testing.assert_array_equal(matrix(TIE), matrices[3])
        self.assertFalse(np.array_equal(matrices[3], matrices[4]))

    def test_mixed_v1_q_m_alnum_calls_reset_shared_workspace(self):
        payloads = (BYTE26, b"\x00", BYTE26[:11], BYTE26[:12], BYTE26[:20],
                    BYTE26[:21], BYTE26[:25], BYTE26[::-1], b"\xff") * 2
        rows = inspect("mixed", *(payload.hex() for payload in payloads))
        for index, payload in enumerate(payloads):
            np.testing.assert_array_equal(parse_matrix(rows[index * 25:(index + 1) * 25]),
                                          selected_reference(payload, version=2, ecc=ERROR_CORRECT_M).get_matrix())

    def test_raw_decode_all_lengths_and_special_vectors(self):
        payloads = (BYTE26, BYTE26[::-1], b"\xc3\x28", b"A\0B", b"\x1a", b"\r\n",
                    *(BYTE26[:length] for length in range(1, 27)))
        for payload in payloads:
            qr = matrix(payload)
            image = np.pad(1 - qr, 4, constant_values=1).repeat(12, 0).repeat(12, 1) * 255
            decoded = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.QRCode,
                                           try_downscale=False)
            self.assertIsNotNone(decoded)
            self.assertTrue(decoded.valid)
            self.assertEqual(decoded.bytes, payload)


class V2MMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True, stdout=subprocess.PIPE)

    def render(self, payload):
        result = subprocess.run([str(ROOT / "build/test_main_render"), "/B", payload.hex()],
                                check=True, capture_output=True)
        self.assertEqual(result.stderr, b"")
        return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)

    def test_all_cli_lengths_select_correct_profile_and_every_pixel(self):
        for length in range(1, 27):
            payload = BYTE26[:length]
            version = 1 if length <= 11 else 2
            ecc = ERROR_CORRECT_Q if length <= 20 else ERROR_CORRECT_M
            qr = np.asarray(selected_reference(payload, version=version, ecc=ecc).get_matrix(), np.uint8)
            expected = np.full((400, 640), 255, np.uint8)
            x, y = (194, 74) if version == 1 else (170, 50)
            expected[y:y + len(qr) * 12, x:x + len(qr) * 12] = (1 - qr).repeat(12, 0).repeat(12, 1) * 255
            frame = self.render(payload)
            np.testing.assert_array_equal(frame, expected)
            decoded = zxingcpp.read_barcode(frame, formats=zxingcpp.BarcodeFormat.QRCode,
                                           try_downscale=False)
            self.assertIsNotNone(decoded)
            self.assertTrue(decoded.valid)
            self.assertEqual(decoded.bytes, payload)

    def test_hex_boundaries_and_nonhex_never_start_graphics(self):
        for value in ("", "0", "0" * 51, "0" * 53, "0" * 54, "0" * 1000,
                      "G" + "0" * 51, "0" * 51 + "G"):
            result = subprocess.run([str(ROOT / "build/test_main_render"), "/B", value], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            expected = (b"Error: binary hex must use 0-9, A-F, or a-f only.\r\n" if "G" in value else
                        b"Error: binary hex must contain 2-52 characters with even length.\r\n")
            self.assertEqual(result.stderr, expected)

    def test_maximum_hex_case_and_usage(self):
        outputs = [subprocess.run([str(ROOT / "build/test_main_render"), "/B", value],
                                  capture_output=True, check=True).stdout
                   for value in (BYTE26.hex(), BYTE26.hex().upper())]
        self.assertEqual(outputs[0], outputs[1])
        result = subprocess.run([str(ROOT / "build/test_main_render")], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"/B HEX (1-26 bytes, 2-52 hex digits)", result.stderr)
        # Phase 3C-2の拡張は/Fだけで、上記/Bのusageは維持する。
        # Phase 3C-2 extends /F only; the /B usage above remains unchanged.
        self.assertIn(b"/F PATH (whole binary file, 1-2953 bytes)", result.stderr)

    def test_quiet_zone_margins_and_unchanged_production_decoder(self):
        frame = self.render(BYTE26)
        qr = np.pad(1 - matrix(BYTE26), 4, constant_values=1).repeat(12, 0).repeat(12, 1) * 255
        self.assertEqual(qr.shape, (396, 396))
        np.testing.assert_array_equal(frame[2:398, 122:518], qr)
        for margin in (frame[:2], frame[398:], qr[:48], qr[348:], qr[:, :48], qr[:, 348:]):
            self.assertTrue(np.all(margin == 255))
        path = ROOT / "build/screen_v2m_bytes.png"
        self.assertTrue(cv2.imwrite(str(path), frame))
        result = subprocess.run([sys.executable, str(ROOT / "windows/decode_binary_photo.py"),
                                 str(path), "--expect-hex", BYTE26.hex()], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"Length: 26\nHex: {BYTE26.hex().upper()}\n".encode("ascii"))


if __name__ == "__main__":
    unittest.main()
