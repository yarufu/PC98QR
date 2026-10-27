# 本番Cの生バイト処理段階、マスク、ZXing認識を検証する。
"""Raw-byte stage, mask and ZXing recognition tests for the production C."""

from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.util import MODE_8BIT_BYTE
import zxingcpp

from qr_byte_oracle import (SINGLE_BYTES, VECTORS, best_mask, candidate_scores,
                            data_codewords, ecc_codewords, reference, samples,
                            selected_reference)
from qr_mask_oracle import selected_reference as alnum_reference
from test_fixed_qr import generator
from test_qr_mask import FORMATS, parse_matrix, read_format

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=2048)
def inspect(mode, *payloads):
    return subprocess.run([str(ROOT / "build/test_qr_encode"), "bytes-" + mode,
                           *(payload.hex() for payload in payloads)],
                          check=True, capture_output=True, text=True).stdout.splitlines()


def matrix(payload):
    return parse_matrix(inspect("matrix", payload))


class ByteEncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "build/test_qr_encode"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)

    def assert_decodes(self, payload):
        image = generator.render_image(matrix(payload))
        result = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.QRCode,
                                       try_downscale=False)
        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        self.assertEqual(result.bytes, payload)

    def test_reference_versions_and_byte_conditions(self):
        self.assertEqual(version("qrcode"), "8.2")
        self.assertEqual(version("zxing-cpp"), "2.3.0")
        qr = reference(b"HELLO")  # Must force Byte even for Alphanumeric input. / 日本語: 英数字だけの入力でもByteモードを強制する。
        self.assertEqual((qr.version, qr.error_correction, qr.mask_pattern, qr.border),
                         (1, ERROR_CORRECT_Q, 0, 0))
        self.assertEqual(len(qr.data_list), 1)
        self.assertEqual(qr.data_list[0].mode, MODE_8BIT_BYTE)
        self.assertEqual(qr.data_list[0].data, b"HELLO")

    def test_data_fields_all_lengths(self):
        for length in range(1, 12):
            payload = bytes(range(length))
            with self.subTest(length=length):
                actual = bytes.fromhex(inspect("data", payload)[0])
                self.assertEqual(actual, data_codewords(payload))
                self.assertEqual(list(actual), reference(payload).data_cache[:13])
                bits = "".join(f"{value:08b}" for value in actual)
                self.assertEqual(len(bits), 104)
                self.assertEqual(bits[:4], "0100")
                self.assertEqual(int(bits[4:12], 2), length)
                self.assertEqual(bits[12:12 + 8 * length],
                                 "".join(f"{value:08b}" for value in payload))
                self.assertEqual(bits[12 + 8 * length:16 + 8 * length], "0000")
                # どの長さでも4つの終端ビットで最後のバイトが完成する。
                # Four terminator bits complete the last byte at every length.
                self.assertEqual(actual[length + 2:],
                                 bytes((0xEC, 0x11)[i % 2] for i in range(11 - length)))

    def test_one_and_two_byte_known_codewords(self):
        self.assertEqual(inspect("data", b"\x00")[0], "401000ec11ec11ec11ec11ec11")
        self.assertEqual(inspect("data", b"\x00\xff")[0], "40200ff0ec11ec11ec11ec11ec")

    def test_ten_byte_terminator_and_one_ec_pad(self):
        payload = bytes(range(10))
        actual = bytes.fromhex(inspect("data", payload)[0])
        bits = "".join(f"{value:08b}" for value in actual)
        self.assertEqual(4 + 8 + 8 * len(payload), 92)
        self.assertEqual(bits[92:96], "0000")
        self.assertEqual(actual[12:], b"\xec")

    def test_eleven_bytes_fill_104_bits_without_pad(self):
        payload = VECTORS[-1]
        actual = bytes.fromhex(inspect("data", payload)[0])
        self.assertEqual(len(payload), 11)
        self.assertEqual(4 + 8 + 88 + 4, 104)
        expected_bits = "0100" + "00001011" + "".join(f"{b:08b}" for b in payload) + "0000"
        self.assertEqual("".join(f"{b:08b}" for b in actual), expected_bits)
        self.assertEqual(len(actual), 13)

    def test_zero_and_twelve_bytes_rejected_matrix_untouched(self):
        # ドライバーが境界監視値、入力保持、441セルすべての番兵値を検査する。
        # Driver asserts canaries, unchanged input, and all 441 sentinel cells.
        for payload in (b"", bytes(12)):
            with self.subTest(length=len(payload)):
                result = subprocess.run([str(ROOT / "build/test_qr_encode"),
                                         "bytes-matrix", payload.hex()], capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")

    def test_all_256_single_bytes_data_and_ecc(self):
        for payload, encoded in zip(SINGLE_BYTES, inspect("codewords", *SINGLE_BYTES), strict=True):
            with self.subTest(hex=payload.hex()):
                data = data_codewords(payload)
                words = bytes.fromhex(encoded)
                self.assertEqual(words[:13], data)
                self.assertEqual(words[13:], ecc_codewords(data))
                self.assertEqual(list(words), reference(payload).data_cache)

    def test_fixed_vectors_and_seeded_samples_data_and_ecc(self):
        cases = samples()
        for payload, encoded in zip(cases, inspect("codewords", *cases), strict=True):
            with self.subTest(hex=payload.hex()):
                data = data_codewords(payload)
                self.assertEqual(bytes.fromhex(encoded), data + ecc_codewords(data))
                self.assertEqual(list(bytes.fromhex(encoded)), reference(payload).data_cache)

    def test_special_byte_values_are_ordinary_data(self):
        for value in (0x00, 0x01, 0x1A, 0x7F, 0x80, 0xFF):
            with self.subTest(value=value):
                payload = bytes([value])
                words = bytes.fromhex(inspect("data", payload)[0])
                self.assertEqual(words[0], 0x40)
                self.assertEqual(words[1], 0x10 | (value >> 4))
                self.assertEqual(words[2], (value & 15) << 4)
                self.assert_decodes(payload)

    def test_embedded_nul_uses_explicit_length(self):
        payload = b"A\x00B"
        data = bytes.fromhex(inspect("data", payload)[0])
        self.assertEqual(data[:5], bytes.fromhex("4034100420"))
        self.assert_decodes(payload)

    def test_invalid_utf8_is_preserved(self):
        self.assert_decodes(b"\xc3\x28")

    def test_input_and_output_canaries_repeated_and_mixed_modes(self):
        cases = (*VECTORS, *reversed(VECTORS), b"HELLO", b"\xff")
        rows = inspect("mixed", *cases)
        self.assertEqual(len(rows), 42 * len(cases))
        for index, payload in enumerate(cases):
            np.testing.assert_array_equal(parse_matrix(rows[42 * index:42 * index + 21]),
                                          alnum_reference("HELLO").get_matrix())
            np.testing.assert_array_equal(parse_matrix(rows[42 * index + 21:42 * index + 42]),
                                          selected_reference(payload).get_matrix())

    def test_all_forced_masks_441_cells(self):
        for payload in samples():
            rows = inspect("candidates", payload)
            self.assertEqual(len(rows), 8 * 22)
            for mask in range(8):
                with self.subTest(hex=payload.hex(), mask=mask):
                    actual = parse_matrix(rows[mask * 22 + 1:(mask + 1) * 22])
                    np.testing.assert_array_equal(actual, reference(payload, mask).get_matrix())
                    self.assertEqual(read_format(actual), (FORMATS[mask], FORMATS[mask]))

    def test_all_candidates_penalties_best_and_ties(self):
        for payload in samples():
            rows = inspect("candidates", payload)
            scores = candidate_scores(payload)
            for mask in range(8):
                with self.subTest(hex=payload.hex(), mask=mask):
                    fields = rows[mask * 22].split()
                    self.assertEqual(int(fields[0]), mask)
                    self.assertEqual(int(fields[1], 16), FORMATS[mask])
                    self.assertEqual(tuple(map(int, fields[2:])), scores[mask])
            chosen = min(range(8), key=lambda mask: (scores[mask][-1], mask))
            self.assertEqual(best_mask(payload), chosen)
            np.testing.assert_array_equal(matrix(payload), reference(payload, chosen).get_matrix())

    def test_zxing_synthetic_fixed_vectors(self):
        for payload in VECTORS:
            with self.subTest(hex=payload.hex()):
                self.assert_decodes(payload)

    def test_representatives_for_all_eight_best_masks(self):
        representatives = ("01", "23", "02", "C0", "00", "44", "1A", "04")
        for mask, value in enumerate(representatives):
            payload = bytes.fromhex(value)
            self.assertEqual(best_mask(payload), mask)
            self.assertEqual(read_format(matrix(payload)), (FORMATS[mask], FORMATS[mask]))

    def test_byte_tie_selects_lowest_mask(self):
        payload = b"\x67"
        scores = candidate_scores(payload)
        self.assertEqual([score[-1] for score in scores],
                         [1050, 1155, 1169, 1306, 1050, 1097, 1113, 1217])
        self.assertEqual(best_mask(payload), 0)
        np.testing.assert_array_equal(matrix(payload), reference(payload, 0).get_matrix())

    def test_zxing_synthetic_all_256_single_bytes(self):
        for payload in SINGLE_BYTES:
            with self.subTest(hex=payload.hex()):
                self.assert_decodes(payload)


class ByteMainIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = ROOT / "build/test_main_render"
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)

    def render(self, *arguments):
        result = subprocess.run([str(self.executable), *arguments], capture_output=True, check=True)
        self.assertEqual(result.stderr, b"")
        return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)

    def test_binary_main_framebuffer_quiet_zone_and_decode(self):
        for payload in VECTORS:
            with self.subTest(hex=payload.hex()):
                frame = self.render("/B", payload.hex().upper())
                qr = np.asarray(selected_reference(payload).get_matrix(), dtype=np.uint8)
                expected = np.full((400, 640), 255, dtype=np.uint8)
                expected[74:326, 194:446] = (1 - qr).repeat(12, 0).repeat(12, 1) * 255
                np.testing.assert_array_equal(frame, expected)
                np.testing.assert_array_equal(frame[26:374, 146:494], generator.render_image(qr))
                result = zxingcpp.read_barcode(frame, formats=zxingcpp.BarcodeFormat.QRCode,
                                               try_downscale=False)
                self.assertIsNotNone(result)
                self.assertTrue(result.valid)
                self.assertEqual(result.bytes, payload)
                if payload == bytes.fromhex("00017F80FF"):
                    self.assertTrue(cv2.imwrite(str(ROOT / "build/screen_bytes.png"), frame))

    def test_hex_case_and_maximum_length(self):
        for payload in (bytes.fromhex("abcdef"), VECTORS[-1]):
            np.testing.assert_array_equal(self.render("/B", payload.hex()),
                                          self.render("/B", payload.hex().upper()))

    def test_invalid_binary_cli_never_calls_encoder_or_graphics(self):
        cases = [(text, b"binary hex must contain") for text in
                 ("", "0", "000", "0" * 23, "0" * 41, "0" * 53, "0" * 54, "0" * 1000)]
        cases += [(text, b"binary hex must use") for text in
                  ("GG", "0g", "G0", "00GG", "0x", " 0", "0\n", "\uff26\uff26")]
        for text, message in cases:
            with self.subTest(text=text):
                result = subprocess.run([str(self.executable), "/B", text], capture_output=True)
                # ハーネスが符号化器の呼出し回数と画面段階0を検査する。
                # Harness asserts both encoder call counts and graphics stage = 0.
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertIn(message, result.stderr)

    def test_usage_errors_distinct_from_hex_errors(self):
        for args in ([], ["/b", "00"], ["/B", "00", "FF"], ["HELLO", "WORLD"]):
            result = subprocess.run([str(self.executable), *args], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(result.stderr.startswith(b"Usage:"))

    def test_existing_alnum_cli_including_b_alone(self):
        for text in ("HELLO", "HELLO WORLD", "/B"):
            frame = self.render(text)
            qr = np.asarray(alnum_reference(text).get_matrix(), dtype=np.uint8)
            np.testing.assert_array_equal(frame[74:326, 194:446],
                                          (1 - qr).repeat(12, 0).repeat(12, 1) * 255)
            self.assertEqual(zxingcpp.read_barcode(frame).bytes, text.encode("ascii"))

    def test_binary_decoder_png_jpeg_unicode_path_cli(self):
        frame = self.render("/B", "00017F80FF")
        with tempfile.TemporaryDirectory() as directory:
            for extension in (".png", ".jpg"):
                path = Path(directory) / ("撮影 byte" + extension)
                ok, encoded = cv2.imencode(extension, frame)
                self.assertTrue(ok)
                path.write_bytes(encoded.tobytes())
                for options, code in (([], 0), (["--expect-hex", "00017f80ff"], 0),
                                      (["--expect-hex", "00"], 1)):
                    result = subprocess.run([sys.executable,
                                             str(ROOT / "windows/decode_binary_photo.py"),
                                             str(path), *options], capture_output=True, text=True)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(result.stdout, "Length: 5\nHex: 00017F80FF\n")


if __name__ == "__main__":
    unittest.main()
