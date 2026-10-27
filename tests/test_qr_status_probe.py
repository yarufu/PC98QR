# 固定QSTATUSを独立画素設計・境界保護プレーン・本番QRで検証する。
"""Fixed QSTATUS: independent pixel designs, guarded planes and production QR."""
from functools import lru_cache
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

import cv2
import numpy as np
import zxingcpp

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/phase4e1c"
PGM_HEADER = b"P5\n640 400\n255\n"
HEADER = bytes.fromhex("503938510100000018")
WIRE = HEADER + bytes((i * 37 + (i >> 8) * 11 + 3) & 255 for i in range(2944))

# 人が読める5列の線描として、独立した字形基準を定義する。
# 本番テーブル・Cソース解析・フォント出力・Cで計算した字形は読み込まない。
# Human-readable design oracle, independently specified as five-column strokes.
# No production table, C source parsing, font export or computed C glyph is read.
DESIGNS = {
    '0': '.###./#...#/#..##/#.#.#/##..#/#...#/.###.',
    '1': '..#../.##../..#../..#../..#../..#../.###.',
    '2': '.###./#...#/....#/...#./..#../.#.../#####',
    '3': '####./....#/....#/.###./....#/....#/####.',
    '4': '...#./..##./.#.#./#..#./#####/...#./...#.',
    '5': '#####/#..../#..../####./....#/....#/####.',
    '6': '.###./#..../#..../####./#...#/#...#/.###.',
    '7': '#####/....#/...#./..#../.#.../.#.../.#...',
    '8': '.###./#...#/#...#/.###./#...#/#...#/.###.',
    '9': '.###./#...#/#...#/.####/....#/....#/.###.',
    'C': '.###./#...#/#..../#..../#..../#...#/.###.',
    'E': '#####/#..../#..../####./#..../#..../#####',
    'L': '#..../#..../#..../#..../#..../#..../#####',
    'N': '#...#/##..#/##..#/#.#.#/#..##/#..##/#...#',
    'R': '####./#...#/#...#/####./#.#../#..#./#...#',
    'S': '.####/#..../#..../.###./....#/....#/####.',
    'T': '#####/..#../..#../..#../..#../..#../..#..',
    'U': '#...#/#...#/#...#/#...#/#...#/#...#/.###.',
    '/': '....#/....#/...#./..#../.#.../#..../#....',
    '-': '...../...../...../#####/...../...../.....',
    ' ': '...../...../...../...../...../...../.....',
}


def glyph(character):
    design = np.array([[c == '#' for c in row] for row in DESIGNS[character].split('/')], np.uint8)
    assert design.shape == (7, 5)
    result = np.zeros((16, 8), np.uint8)
    result[1:15, 1:6] = np.repeat(design, 2, axis=0)
    return result


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_STATUS_DRIVER")
    if override:
        return Path(override)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "test_qr_status_probe"
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "tests/qr_status_probe_main.c", "src/qr_encode.c", "src/frame_header.c",
                    "-o", str(target)], cwd=ROOT, check=True)
    return target


@lru_cache(maxsize=None)
def probe(mode):
    result = subprocess.run([str(driver()), mode], capture_output=True, check=True, timeout=30)
    if result.stderr:
        raise AssertionError(result.stderr)
    return result.stdout


def image(mode):
    raw = probe(mode)
    assert raw.startswith(PGM_HEADER) and len(raw) == len(PGM_HEADER) + 640 * 400
    return np.frombuffer(raw[len(PGM_HEADER):], np.uint8).reshape(400, 640)


@lru_cache(maxsize=1)
def production():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    fixture = ARTIFACTS / "QSTATUS.BIN"
    fixture.write_bytes(WIRE)
    target = os.environ.get("PC98QR_MAIN_DRIVER")
    if not target:
        subprocess.run(["make", "build/test_main_render"], cwd=ROOT, check=True,
                       stdout=subprocess.PIPE)
        target = str(ROOT / "build/test_main_render")
    result = subprocess.run([target, "/F", str(fixture)], capture_output=True, check=True, timeout=30)
    assert result.stderr == b"" and result.stdout.startswith(PGM_HEADER)
    return np.frombuffer(result.stdout[len(PGM_HEADER):], np.uint8).reshape(400, 640)


class StatusFontTests(unittest.TestCase):
    def request(self, text, x=520, y=32, length=None, null=False):
        if isinstance(text, str):
            text = text.encode('ascii')
        args = [str(driver()), "font", str(x), str(y), text.hex(),
                str(len(text) if length is None else length)]
        if null:
            args.append("null")
        result = subprocess.run(args, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        line, raw = result.stdout.split(b'\n', 1)
        self.assertEqual(len(raw), 4 * 32000)
        return json.loads(line), np.frombuffer(raw, np.uint8).reshape(4, 400, 80)

    def expected(self, text='', x=520, y=32):
        planes = np.empty((4, 400, 80), np.uint8)
        for p in range(4):
            planes[p].fill(0x21 + 0x33 * p)
        for i, character in enumerate(text):
            white = 1 - glyph(character)
            packed = np.packbits(white, axis=1)[:, 0]
            planes[:, y:y+16, x//8+i] = packed
        return planes

    def check_text(self, text, x=520, y=32):
        info, planes = self.request(text, x, y)
        self.assertEqual(info, {"result": 0, "writes": [len(text)*16]*4})
        np.testing.assert_array_equal(planes, self.expected(text, x, y))
        return planes

    def reject(self, text, code=1, **kwargs):
        info, planes = self.request(text, **kwargs)
        self.assertEqual(info, {"result": code, "writes": [0]*4})
        np.testing.assert_array_equal(planes, self.expected())

    def test_all_21_glyphs_and_cell_edges(self):
        self.assertEqual(len(DESIGNS), 21)
        for character in DESIGNS:
            with self.subTest(character=character):
                actual = self.check_text(character)
                cell = np.unpackbits(actual[0, 32:48, 65:66], axis=1)
                self.assertEqual(cell.shape, (16, 8))
                self.assertTrue(np.all(cell[[0, 15], :] == 1))
                self.assertTrue(np.all(cell[:, [0, 6, 7]] == 1))
                self.assertEqual(bool(np.any(cell == 0)), character != ' ')

    def test_asymmetric_orientation(self):
        for character in ('R', '2', '/', 'L'):
            with self.subTest(character=character):
                actual = self.check_text(character)
                black = 1 - np.unpackbits(actual[0, 32:48, 65:66], axis=1)
                self.assertFalse(np.array_equal(black, np.fliplr(glyph(character))))
                self.assertFalse(np.array_equal(black, np.flipud(glyph(character))))

    def test_current_label(self):
        self.check_text('CURRENT', 520, 32)

    def test_current_number(self):
        self.check_text('1/24', 520, 48)

    def test_select_label(self):
        self.check_text('SELECT', 520, 80)

    def test_select_dash(self):
        self.check_text('-', 520, 96)

    def test_maximum_number_at_requested_position(self):
        self.check_text('65535/65535', 520, 48)
        self.assertEqual(520 + 11 * 8 - 1, 607)

    def test_maximum_number_touching_right_edge(self):
        self.check_text('65535/65535', 552, 384)

    def test_fifteen_cells(self):
        self.check_text('0123456789CELNR', 520, 0)

    def test_bottom_right_cell(self):
        self.check_text('R', 632, 384)

    def test_every_aligned_column(self):
        for x in range(520, 633, 8):
            with self.subTest(x=x):
                self.check_text('R', x, 1)

    def test_invalid_x(self):
        for x in (0, 504, 511, 512, 519, 521, 527, 631, 633, 639, 640, 65535, 4294967295):
            with self.subTest(x=x):
                self.reject('R', x=x)

    def test_invalid_y(self):
        for y in (385, 399, 400, 65535, 4294967295):
            with self.subTest(y=y):
                self.reject('R', y=y)

    def test_empty(self):
        self.reject('')

    def test_null(self):
        self.reject('R', null=True)

    def test_sixteen_cells_rejected_without_truncation(self):
        self.reject('0123456789CELNRS')

    def test_two_cells_at_last_column(self):
        self.reject('RR', x=632)

    def test_number_one_column_too_far_right(self):
        self.reject('65535/65535', x=560)

    def test_large_length_rejected_before_input_read(self):
        for length in (16, 65535, 4294967295):
            with self.subTest(length=length):
                self.reject('R', length=length)

    def test_all_unsupported_bytes(self):
        supported = {ord(c) for c in DESIGNS}
        for value in set(range(256)) - supported:
            with self.subTest(value=value):
                self.reject(bytes([value]), code=2)

    def test_bad_final_character_never_draws_valid_prefix(self):
        self.reject('CURRENT?', code=2)

    def test_embedded_nul_rejected(self):
        self.reject(b'C\0R', code=2)

    def test_no_nul_terminator_required(self):
        self.check_text('CURRENT')  # Harness allocates exactly seven bytes under ASan. / 日本語: ハーネスはASanの下で正確に7バイトを確保する。


class StatusProbeTests(unittest.TestCase):
    def test_complete_fixture_independent_bytes(self):
        self.assertEqual(len(WIRE), 2953)
        self.assertEqual(probe('wire'), WIRE)
        self.assertEqual(WIRE[:9], HEADER)
        self.assertTrue({0, 0x1A, 0x80, 0xFF, 13, 10} <= set(WIRE[9:]))

    def test_before_overlay_equals_production_every_pixel(self):
        np.testing.assert_array_equal(image('qr'), production())

    def test_matrix_all_3917_bytes_equal_production(self):
        body = production()[23:377:2, 143:497:2]
        packed = np.packbits((body == 0).astype(np.uint8).reshape(-1)).tobytes()
        self.assertEqual(len(packed), 3917)
        self.assertEqual(probe('matrix'), packed)
        self.assertEqual(packed[-1] & 0x7F, 0)

    def test_quiet_zone_and_all_left_pixels_unchanged(self):
        before, after = image('qr'), image('probe')
        np.testing.assert_array_equal(after[15:385, 135:505], before[15:385, 135:505])
        np.testing.assert_array_equal(after[:, :520], before[:, :520])
        for area in (after[15:23, 135:505], after[377:385, 135:505],
                     after[23:377, 135:143], after[23:377, 497:505], after[:, 512:520]):
            self.assertTrue(np.all(area == 255))

    def test_fixed_labels_and_every_other_pixel(self):
        expected = production().copy()
        for text, y in (('CURRENT', 32), ('1/24', 48), ('SELECT', 80), ('-', 96)):
            for i, character in enumerate(text):
                expected[y:y+16, 520+i*8:528+i*8] = (1-glyph(character))*255
        np.testing.assert_array_equal(image('probe'), expected)

    def test_full_screen_raw_binary_decode_and_artifacts(self):
        frame = image('probe')
        decoded = zxingcpp.read_barcode(frame, formats=zxingcpp.BarcodeFormat.QRCode,
                                       try_downscale=False)
        self.assertIsNotNone(decoded)
        self.assertTrue(decoded.valid)
        self.assertEqual(decoded.bytes, WIRE)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / 'QSTATUS.BIN').write_bytes(WIRE)
        (ARTIFACTS / 'QSTATUS.HEX').write_text(WIRE.hex().upper() + '\n')
        self.assertTrue(cv2.imwrite(str(ARTIFACTS / 'qstatus-host.png'), frame))

    def check_failure(self, fault, code):
        result = subprocess.run([str(driver()), 'probe', fault], capture_output=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (code, b'', b''))

    def test_usage_never_initializes_graphics(self):
        self.check_failure('usage', 1)

    def test_header_failure_never_encodes_or_initializes_graphics(self):
        self.check_failure('header', 2)

    def test_encoder_failure_never_initializes_graphics(self):
        self.check_failure('encoder', 3)

    def test_status_failure_shuts_down_without_show_or_wait(self):
        self.check_failure('status', 4)


if __name__ == '__main__':
    unittest.main()
