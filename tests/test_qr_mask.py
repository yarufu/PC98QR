# 外部qrcodeの自動選択ではなく、PC98QRの明示したN3契約でマスクを検証する。
"""Mask selection tests for PC98QR's explicit N3 contract, not auto qrcode."""

from pathlib import Path
import random
import subprocess
import unittest

import cv2
import numpy as np

from qr_mask_oracle import balance, best_mask, candidate_scores, penalties, reference
from test_fixed_qr import generator
from test_qr_encode import ALPHABET, samples

ROOT = Path(__file__).resolve().parents[1]
FORMATS = (0x355F, 0x3068, 0x3F31, 0x3A06, 0x24B4, 0x2183, 0x2EDA, 0x2BED)
VERTICAL = (0, 1, 2, 3, 4, 5, 7, 8, 14, 15, 16, 17, 18, 19, 20)
HORIZONTAL = (20, 19, 18, 17, 16, 15, 14, 13, 7, 5, 4, 3, 2, 1, 0)
REPRESENTATIVES = (
    "PC98-QR-TEST-001", "0123456789", "HELLO", "3", "A",
    "85B$BXCI$V-1.EW", "$%*+-./:", "2",
)
TIE_TEXT = "/OY.AC72OIRRQZ"
N3_VECTORS = (
    ("000000000000000000000", 0),
    ("110001011101000111111", 0),
    ("110000101110101111111", 40),
    ("110000010111010111111", 40),
    ("100001011101000011111", 80),
    ("101110101010101010101", 40),
    ("101010101010101011101", 40),
    ("101110100001011101000", 160),
    ("000010111010111010000", 80),
    ("100001011101100001111", 0),
    ("110011111100110010101", 40),
    ("110011111100110101010", 0),
    ("111000111111111000111", 80),
)


def parse_matrix(rows):
    assert len(rows) == 21
    assert all(len(row) == 21 and set(row) <= {"0", "1"} for row in rows)
    return np.array([[int(cell) for cell in row] for row in rows], dtype=np.uint8)


def read_format(matrix):
    return (sum(int(matrix[row, 8]) << bit for bit, row in enumerate(VERTICAL)),
            sum(int(matrix[8, col]) << bit for bit, col in enumerate(HORIZONTAL)))


def serial_bch(mask):
    # Cの次数走査とは異なり、多項式のビットを一つずつ入力する。
    """Feed polynomial bits one at a time, distinct from C's degree scan."""
    remainder = 0
    for bit in f"{24 | mask:05b}" + "0" * 10:
        remainder = (remainder << 1) | int(bit)
        if remainder & 0x400:
            remainder ^= 0x537
    return (((24 | mask) << 10) | remainder) ^ 0x5412


class MaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = ROOT / "build/test_qr_encode"
        subprocess.run(["make", "build/test_qr_encode"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)

    def driver(self, mode, *arguments):
        return subprocess.run([str(self.executable), mode, *arguments],
                              check=True, capture_output=True, text=True).stdout.splitlines()

    def score(self, matrix):
        flat = "".join(str(int(cell)) for cell in matrix.flat)
        return tuple(map(int, self.driver("penalty", flat)[0].split()))

    def candidates(self, text):
        lines = self.driver("candidates", text)
        self.assertEqual(len(lines), 8 * 22)
        scores, matrices = [], []
        for mask in range(8):
            fields = lines[mask * 22].split()
            self.assertEqual(int(fields[0]), mask)
            self.assertEqual(int(fields[1], 16), FORMATS[mask])
            scores.append(tuple(map(int, fields[2:])))
            matrices.append(parse_matrix(lines[mask * 22 + 1:(mask + 1) * 22]))
        return tuple(scores), matrices

    def test_all_mask_coordinates(self):
        rows = self.driver("maskmaps")
        self.assertEqual(len(rows), 8 * 21)
        expressions = (
            lambda r, c: (r + c) % 2 == 0,
            lambda r, c: r % 2 == 0,
            lambda r, c: c % 3 == 0,
            lambda r, c: (r + c) % 3 == 0,
            lambda r, c: (r // 2 + c // 3) % 2 == 0,
            lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
            lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
            lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
        )
        for mask, expression in enumerate(expressions):
            actual = parse_matrix(rows[mask * 21:(mask + 1) * 21])
            expected = [[int(expression(r, c)) for c in range(21)] for r in range(21)]
            np.testing.assert_array_equal(actual, expected)
            if mask in (1, 2, 4):
                self.assertFalse(np.array_equal(actual, actual.T))

    def test_dynamic_format_bch_all_masks(self):
        words = tuple(int(word, 16) for word in self.driver("formats"))
        self.assertEqual(words, FORMATS)
        for mask, word in enumerate(words):
            self.assertEqual(word, serial_bch(mask))
            data = (word ^ 0x5412) >> 10
            self.assertEqual(data >> 3, 3)
            self.assertEqual(data & 7, mask)

    def test_both_format_copies_and_function_cells(self):
        _, matrices = self.candidates("HELLO")
        layout = self.driver("layout")
        reserved = parse_matrix(layout[:21])
        self.assertEqual(int(reserved.sum()), 233)
        for row in VERTICAL:
            reserved[row, 8] = 0
        for col in HORIZONTAL:
            reserved[8, col] = 0
        for mask, matrix in enumerate(matrices):
            self.assertEqual(read_format(matrix), (FORMATS[mask], FORMATS[mask]))
            self.assertEqual(matrix[13, 8], 1)
            np.testing.assert_array_equal(matrix[reserved == 1], matrices[0][reserved == 1])
            np.testing.assert_array_equal(matrix, reference("HELLO", mask).get_matrix())

    def test_n1_lengths_and_final_runs(self):
        self.assertEqual(list(map(int, self.driver("runs"))),
                         [0 if n < 5 else n - 2 for n in range(1, 22)])
        for length in range(1, 22):
            matrix = (np.indices((21, 21)).sum(axis=0) % 2).astype(np.uint8)
            start = 21 - length
            matrix[10, start:] = 1 - matrix[10, start - 1] if start else 1
            for candidate in (matrix, matrix.T):
                with self.subTest(length=length, vertical=candidate is not matrix):
                    actual = self.score(candidate)
                    self.assertEqual(actual[0], 0 if length < 5 else length - 2)
                    self.assertEqual(actual, penalties(candidate))

    def test_n2_overlapping_squares_and_edges(self):
        for size, expected in ((2, 3), (3, 12)):
            for start in (0, 10, 21 - size):
                for color in (0, 1):
                    matrix = (np.indices((21, 21)).sum(axis=0) % 2).astype(np.uint8)
                    matrix[start:start + size, start:start + size] = color
                    with self.subTest(size=size, start=start, color=color):
                        self.assertEqual(self.score(matrix)[1], expected)
                        self.assertEqual(self.score(matrix), penalties(matrix))

    def test_n3_contract_vectors_rows_and_columns(self):
        for line, expected in N3_VECTORS:
            for reverse in (False, True):
                matrix = np.zeros((21, 21), dtype=np.uint8)
                matrix[10] = list(map(int, line[::-1] if reverse else line))
                for candidate in (matrix, matrix.T):
                    with self.subTest(line=line, reverse=reverse, vertical=candidate is not matrix):
                        self.assertEqual(self.score(candidate)[2], expected)
                        self.assertEqual(penalties(candidate)[2], expected)
                        self.assertEqual(self.score(candidate), penalties(candidate))

    def test_n3_one_side_not_counted_again_for_longer_light(self):
        for length in (4, 5, 6):
            line = ("1" + "0" * length + "1011101" + "0").ljust(21, "1")
            matrix = np.zeros((21, 21), dtype=np.uint8)
            matrix[10] = list(map(int, line))
            for candidate in (matrix, matrix.T):
                self.assertEqual(self.score(candidate)[2], 40)
                self.assertEqual(self.score(candidate), penalties(candidate))

    def test_n4_all_dark_counts(self):
        actual = list(map(int, self.driver("balance")))
        self.assertEqual(actual, [balance(dark) for dark in range(442)])
        for dark, expected in ((0, 90), (198, 10), (199, 0), (242, 0), (243, 10), (441, 90)):
            self.assertEqual(actual[dark], expected)
        for dark in range(442):
            matrix = np.zeros((21, 21), dtype=np.uint8)
            matrix.flat[:dark] = 1
            self.assertEqual(self.score(matrix)[3], actual[dark])

    def test_whole_symbol_extremes_no_quiet_zone(self):
        for color in (0, 1):
            matrix = np.full((21, 21), color, dtype=np.uint8)
            self.assertEqual(self.score(matrix), (798, 1200, 0, 90, 2088))
            self.assertEqual(self.score(matrix), penalties(matrix))

    def test_random_artificial_symbols_against_oracle(self):
        rng = random.Random(0x9821B3)
        for density in (0.1, 0.3, 0.5, 0.7, 0.9):
            for _ in range(32):
                matrix = np.array([[int(rng.random() < density) for _ in range(21)]
                                   for _ in range(21)], dtype=np.uint8)
                actual = self.score(matrix)
                self.assertEqual(actual, penalties(matrix))
                self.assertEqual(sum(actual[:4]), actual[4])
                self.assertLessEqual(actual[4], 8818)

    def test_all_candidate_penalties_and_selected_mask(self):
        # 既存561ケースの行列検証を維持し、全候補へ広げる。
        # 自動選択も検証し、空白等を含む明示的な意味別入力群を使う。
        # Preserve the 561-case matrix coverage and extend it to every candidate
        # and automatic choice; explicit semantic categories include spaces etc.
        texts = samples() + list(ALPHABET) + ["HELLO WORLD", "0123456789", "$%*+-./:"]
        for text in texts:
            with self.subTest(text=text):
                actual, matrices = self.candidates(text)
                expected = candidate_scores(text)
                self.assertEqual(actual, expected)
                selected = parse_matrix(self.driver("matrix", text))
                mask = best_mask(text)
                self.assertEqual(read_format(selected), (FORMATS[mask], FORMATS[mask]))
                np.testing.assert_array_equal(selected, matrices[mask])
                np.testing.assert_array_equal(selected, reference(text, mask).get_matrix())

    def test_all_eight_selected_mask_fixtures(self):
        for mask, text in enumerate(REPRESENTATIVES):
            with self.subTest(mask=mask, text=text):
                actual, matrices = self.candidates(text)
                self.assertEqual(actual, candidate_scores(text))
                self.assertEqual(best_mask(text), mask)
                selected = parse_matrix(self.driver("matrix", text))
                np.testing.assert_array_equal(selected, matrices[mask])
                self.assertEqual(read_format(selected), (FORMATS[mask], FORMATS[mask]))

    def test_tie_uses_lowest_mask(self):
        actual, matrices = self.candidates(TIE_TEXT)
        self.assertEqual(actual, candidate_scores(TIE_TEXT))
        totals = [score[-1] for score in actual]
        self.assertEqual(totals, [1045, 1295, 1072, 1062, 1154, 1287, 1045, 1151])
        self.assertEqual([mask for mask, total in enumerate(totals) if total == min(totals)], [0, 6])
        self.assertEqual(best_mask(TIE_TEXT), 0)
        np.testing.assert_array_equal(parse_matrix(self.driver("matrix", TIE_TEXT)), matrices[0])
        self.assertFalse(np.array_equal(matrices[0], matrices[6]))

    def test_repeated_selection_in_one_process(self):
        texts = list(REPRESENTATIVES) + [TIE_TEXT] + list(reversed(REPRESENTATIVES))
        rows = self.driver("matrix", *texts)
        self.assertEqual(len(rows), 21 * len(texts))
        for index, text in enumerate(texts):
            matrix = parse_matrix(rows[index * 21:(index + 1) * 21])
            np.testing.assert_array_equal(matrix, reference(text, best_mask(text)).get_matrix())

    def test_opencv_all_selected_masks_and_tie(self):
        for text in (*REPRESENTATIVES, TIE_TEXT):
            with self.subTest(text=text):
                matrix = parse_matrix(self.driver("matrix", text))
                image = generator.render_image(matrix)
                decoded, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
                self.assertIsNotNone(points)
                self.assertEqual(decoded, text)


if __name__ == "__main__":
    unittest.main()
