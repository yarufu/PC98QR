# 独立C符号化器の各段階を比較する。
# qrcodeは出力の比較基準として使う。
"""Stage comparisons for the original C encoder; qrcode is an output oracle."""

from importlib.metadata import version
from pathlib import Path
import random
import subprocess
import unittest

import cv2
import numpy as np
from qrcode import base
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.util import MODE_ALPHA_NUM

from test_fixed_qr import embedded_matrix, generator
from qr_mask_oracle import reference, selected_reference

ROOT = Path(__file__).resolve().parents[1]
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
EXAMPLES = ("HELLO", "PC98-QR-TEST-001", "PC98-QR-TEST-002", "ABCDEFGHIJKLMNOP")


def samples():
    rng = random.Random(0x98212A)
    return list(EXAMPLES) + [
        "".join(rng.choice(ALPHABET) for _ in range(length))
        for length in range(1, 17) for _ in range(32)
    ]


def polynomial_multiply(a, b):
    # 桁上がりなしの整数積から長除算で剰余を取る、独立した有限体演算基準。
    """Independent GF oracle: carryless integer product, then long reduction."""
    product = 0
    for bit in range(8):
        if b & (1 << bit):
            product ^= a << bit
    for degree in range(14, 7, -1):
        if product & (1 << degree):
            product ^= 0x11D << (degree - 8)
    return product


def generated_coefficients():
    # 昇べき順で各(x+2のi乗)を掛け、Cの格納形式に合わせて最後に逆順にする。
    # Ascending powers; multiply by each (x + 2^i), then reverse for C storage.
    coefficients = [1]
    root = 1
    for _ in range(13):
        expanded = [0] * (len(coefficients) + 1)
        for power, value in enumerate(coefficients):
            expanded[power] ^= polynomial_multiply(value, root)
            expanded[power + 1] ^= value
        coefficients = expanded
        root = polynomial_multiply(root, 2)
    return list(reversed(coefficients))


def reference_generator():
    result = base.Polynomial([1], 0)
    for i in range(13):
        result = result * base.Polynomial([1, base.gexp(i)], 0)
    return result


class EncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.executable = ROOT / "build/test_qr_encode"
        subprocess.run(["make", "build/test_qr_encode"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)

    def run_encoder(self, mode, text=None):
        arguments = [str(self.executable), mode]
        if text is not None:
            arguments.append(text)
        return subprocess.run(arguments, capture_output=True)

    def output(self, mode, text=None):
        result = self.run_encoder(mode, text)
        self.assertEqual(result.returncode, 0, (mode, text, result.stderr))
        return result.stdout.decode("ascii")

    def test_reference_version_and_conditions(self):
        self.assertEqual(version("qrcode"), "8.2")
        qr = reference("12345")
        self.assertEqual((qr.version, qr.error_correction, qr.mask_pattern, qr.border),
                         (1, ERROR_CORRECT_Q, 0, 0))
        self.assertEqual(len(qr.data_list), 1)
        self.assertEqual(qr.data_list[0].mode, MODE_ALPHA_NUM)

    def test_length_contract(self):
        for length in (0, 1, 2, 15, 16, 17, 1000):
            with self.subTest(length=length):
                result = self.run_encoder("validate", "A" * length)
                self.assertEqual(result.returncode, 0 if 1 <= length <= 16 else 1)
                if result.returncode == 0:
                    self.assertEqual(int(result.stdout), length)

    def test_all_allowed_characters(self):
        for character in ALPHABET:
            with self.subTest(character=character):
                self.assertEqual(self.output("validate", character), "1\n")

    def test_invalid_characters(self):
        for text in ("hello", "a", "日本語", "\n", "\t", "_", "@", "!", "é", "A\rB"):
            with self.subTest(text=text):
                self.assertEqual(self.run_encoder("validate", text).returncode, 2)

    def test_data_fields_and_padding_all_lengths(self):
        for length in range(1, 17):
            text = "ABCDEFGHIJKLMNOP"[:length]
            with self.subTest(length=length):
                data = bytes.fromhex(self.output("data", text))
                bits = "".join(f"{byte:08b}" for byte in data)
                payload = "0010" + f"{length:09b}"
                for i in range(0, length - 1, 2):
                    payload += f"{ALPHABET.index(text[i]) * 45 + ALPHABET.index(text[i+1]):011b}"
                if length % 2:
                    payload += f"{ALPHABET.index(text[-1]):06b}"
                terminator = min(4, 104 - len(payload))
                padded = payload + "0" * terminator
                padded += "0" * (-len(padded) % 8)
                pads = (104 - len(padded)) // 8
                padded += "".join(f"{(0xEC, 0x11)[i % 2]:08b}" for i in range(pads))
                self.assertEqual(bits, padded)
                self.assertEqual(list(data), reference(text).data_cache[:13])
                if length == 16:
                    self.assertEqual(len(payload), 101)
                    self.assertEqual(terminator, 3)
                    self.assertEqual(bits[101:], "000")
                    self.assertEqual(pads, 0)

    def test_data_reference_examples_and_seeded_samples(self):
        for text in samples():
            with self.subTest(text=text):
                data = bytes.fromhex(self.output("data", text))
                self.assertEqual(len(data), 13)
                self.assertEqual(list(data), reference(text).data_cache[:13])

    def test_gf_all_65536_pairs(self):
        actual = bytes.fromhex(self.output("gf"))
        self.assertEqual(len(actual), 65536)
        expected = bytearray()
        for a in range(256):
            for b in range(256):
                product = polynomial_multiply(a, b)
                via_qrcode = 0 if a == 0 or b == 0 else base.gexp(base.glog(a) + base.glog(b))
                self.assertEqual(product, via_qrcode)
                expected.append(product)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0x80 * 256 + 2], 0x1D)  # Reduction at x^8. / 日本語: xの8次で剰余処理する。
        self.assertEqual(actual[256:512], bytes(range(256)))  # Multiply by 1. / 日本語: 1を掛ける。
        self.assertEqual(actual[:256], bytes(256))

    def test_generator_derived_and_reference(self):
        generated = generated_coefficients()
        self.assertEqual(generated, reference_generator().num)
        self.assertEqual(generated[0], 1)
        self.assertEqual(list(bytes.fromhex(self.output("generator"))), generated[1:])
        for i in range(13):
            root = base.gexp(i)
            value = 0
            for coefficient in generated:
                value = polynomial_multiply(value, root) ^ coefficient
            self.assertEqual(value, 0)

    def test_ecc_arbitrary_data(self):
        rng = random.Random(0x13ECC)
        cases = [bytes(13), bytes([255] * 13), bytes(range(13))]
        cases += [bytes([0] * i + [1] + [0] * (12 - i)) for i in range(13)]
        cases += [rng.randbytes(13) for _ in range(256)]
        generator = reference_generator()
        for data in cases:
            with self.subTest(data=data.hex()):
                actual = bytes.fromhex(self.output("ecc", data.hex()))
                # 0を明示し、qrcode側でも零多項式を正規化できるようにする。
                # Explicit zeros let qrcode normalize the zero polynomial too.
                remainder = (base.Polynomial(list(data) + [0] * 13, 0) % generator).num
                expected_ecc = bytes(13 - len(remainder)) + bytes(remainder)
                self.assertEqual(actual, data + expected_ecc)
                # 正しい組織符号の多項式は13個の根すべてで0になる。
                # A valid systematic codeword polynomial vanishes at all 13 roots.
                for i in range(13):
                    value = 0
                    for coefficient in actual:
                        value = polynomial_multiply(value, base.gexp(i)) ^ coefficient
                    self.assertEqual(value, 0)

    def test_complete_codeword_reference(self):
        for text in samples():
            with self.subTest(text=text):
                actual = bytes.fromhex(self.output("codewords", text))
                self.assertEqual(len(actual), 26)
                self.assertEqual(list(actual), reference(text).data_cache)

    def matrix(self, text, mask=None):
        if mask is None:
            rows = self.output("matrix", text).splitlines()
        else:
            rows = subprocess.run([str(self.executable), "forced", str(mask), text],
                                  capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(len(rows), 21)
        self.assertTrue(all(len(row) == 21 and set(row) <= {"0", "1"} for row in rows))
        return np.array([[int(cell) for cell in row] for row in rows], dtype=np.uint8)

    def test_fixed_matrix_regression(self):
        np.testing.assert_array_equal(self.matrix("PC98-QR-TEST-001", mask=0), embedded_matrix())

    def test_matrix_reference_examples_and_seeded_samples(self):
        for text in samples() + list(ALPHABET):
            with self.subTest(text=text):
                for mask in range(8):
                    np.testing.assert_array_equal(self.matrix(text, mask), reference(text, mask).get_matrix())

    def test_public_api_rejection_preserves_matrix(self):
        for text in ("", "ABCDEFGHIJKLMNOPQ", "A" * 1000, "hello", "日本語", "\n", "_", "@"):
            with self.subTest(text=text):
                expected = 1 if not text or len(text) > 16 else 2
                self.assertEqual(self.run_encoder("matrix", text).returncode, expected)

    def test_all_non_alphanumeric_bytes_rejected(self):
        for byte in range(1, 256):  # NUL terminates the C string, tested as empty. / 日本語: NULでC文字列が終わるため、空入力として検証する。
            if chr(byte) not in ALPHABET:
                with self.subTest(byte=byte):
                    self.assertEqual(self.run_encoder("matrix", bytes([byte])).returncode, 2)

    def test_repeated_calls_reset_static_workspace(self):
        texts = ["HELLO", "PC98-QR-TEST-002", "A", "HELLO"]
        result = subprocess.run([str(self.executable), "matrix", *texts],
                                capture_output=True, check=True, text=True)
        rows = result.stdout.splitlines()
        self.assertEqual(len(rows), 84)
        for index, text in enumerate(texts):
            matrix = [[int(cell) for cell in row] for row in rows[index * 21:(index + 1) * 21]]
            np.testing.assert_array_equal(matrix, selected_reference(text).get_matrix())

    def test_functions_reservations_format_and_mask(self):
        rows = self.output("layout").splitlines()
        self.assertEqual(len(rows), 63)
        reserved, functions, zero_data = np.array(
            [[int(cell) for cell in row] for row in rows], dtype=np.uint8
        ).reshape(3, 21, 21)
        expected_reserved = np.zeros((21, 21), dtype=np.uint8)
        expected_reserved[:9, :9] = 1
        expected_reserved[:9, 13:] = 1
        expected_reserved[13:, :9] = 1
        expected_reserved[6, :] = 1
        expected_reserved[:, 6] = 1
        np.testing.assert_array_equal(reserved, expected_reserved)
        self.assertEqual(int(reserved.sum()), 233)
        # 独立した幾何学的パターンと白いセパレーターの各辺を検査する。
        # Independent geometric finder template and each white separator edge.
        finder = np.ones((7, 7), dtype=np.uint8)
        finder[1:6, 1:6] = 0
        finder[2:5, 2:5] = 1
        for y, x in ((0, 0), (0, 14), (14, 0)):
            np.testing.assert_array_equal(functions[y:y+7, x:x+7], finder)
        for edge in (functions[7, :8], functions[:8, 7], functions[7, 13:],
                     functions[:8, 13], functions[13, :8], functions[13:, 7]):
            self.assertFalse(edge.any())
        np.testing.assert_array_equal(functions[6, 8:13], [1, 0, 1, 0, 1])
        np.testing.assert_array_equal(functions[8:13, 6], [1, 0, 1, 0, 1])
        self.assertEqual(functions[13, 8], 1)
        # Cの定数と独立にQ=11、mask=000からBCH形式情報を導く。
        # Derive BCH format from Q=11 and mask=000, independently of C constant.
        payload = 0b11000 << 10
        remainder = payload
        for degree in range(14, 9, -1):
            if remainder & (1 << degree):
                remainder ^= 0x537 << (degree - 10)
        format_word = (payload | remainder) ^ 0x5412
        self.assertEqual(format_word, 0x355F)
        vertical = [0, 1, 2, 3, 4, 5, 7, 8, 14, 15, 16, 17, 18, 19, 20]
        horizontal = [20, 19, 18, 17, 16, 15, 14, 13, 7, 5, 4, 3, 2, 1, 0]
        self.assertEqual(sum(int(functions[y, 8]) << i for i, y in enumerate(vertical)), format_word)
        self.assertEqual(sum(int(functions[8, x]) << i for i, x in enumerate(horizontal)), format_word)
        known = embedded_matrix()
        np.testing.assert_array_equal(functions[reserved == 1], known[reserved == 1])
        # 全0符号語では、非機能セルにmask 0そのものが現れる。
        # All-zero codewords expose exactly mask 0 in every non-function cell.
        mask0 = (np.indices((21, 21)).sum(axis=0) % 2 == 0).astype(np.uint8)
        np.testing.assert_array_equal(zero_data[reserved == 0], mask0[reserved == 0])
        np.testing.assert_array_equal(zero_data[reserved == 1], functions[reserved == 1])

    def test_208_bits_unique_complete_and_ordered(self):
        positions = [tuple(map(int, row.split())) for row in self.output("placement").splitlines()]
        self.assertEqual(len(positions), 208)
        self.assertEqual(len(set(positions)), 208)
        self.assertEqual(positions[:4], [(20, 20), (20, 19), (19, 20), (19, 19)])
        self.assertEqual(positions[-1], (12, 0))
        matrix = self.matrix("PC98-QR-TEST-001", mask=0)
        unmasked = [int(matrix[y, x]) ^ int((x + y) % 2 == 0) for y, x in positions]
        expected = reference("PC98-QR-TEST-001").data_cache
        self.assertEqual(unmasked, [byte >> bit & 1 for byte in expected for bit in range(7, -1, -1)])

    def test_opencv_decode(self):
        texts = list(EXAMPLES) + ["A", "AB", "A" * 15, "HELLO WORLD", "$%*+-./:", "0123456789"]
        for text in texts:
            with self.subTest(text=text):
                image = generator.render_image(self.matrix(text))
                decoded, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
                self.assertIsNotNone(points)
                self.assertEqual(decoded, text)


class MainIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = ROOT / "build/test_main_render"
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)

    def test_dynamic_main_framebuffer_and_decode(self):
        for text in (*EXAMPLES, "HELLO WORLD", "$%*+-./:"):
            with self.subTest(text=text):
                result = subprocess.run([str(self.executable), text], check=True, capture_output=True)
                frame = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)
                matrix = np.asarray(selected_reference(text).get_matrix(), dtype=np.uint8)
                expected = np.full((400, 640), 255, dtype=np.uint8)
                expected[74:326, 194:446] = (1 - matrix).repeat(12, 0).repeat(12, 1) * 255
                np.testing.assert_array_equal(frame, expected)
                np.testing.assert_array_equal(frame[26:374, 146:494], generator.render_image(matrix))
                self.assertEqual(cv2.QRCodeDetector().detectAndDecode(frame)[0], text)
                if text == "HELLO":
                    self.assertTrue(cv2.imwrite(str(ROOT / "build/screen_hello.png"), frame))

    def test_invalid_arguments_never_initialize_graphics(self):
        cases = [[], ["HELLO", "WORLD"], [""], ["ABCDEFGHIJKLMNOPQ"], ["a"],
                 ["日本語"], ["\n"], ["!"], ["HELLO", "", "WORLD"]]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(self.executable), *arguments], capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr)


if __name__ == "__main__":
    unittest.main()
