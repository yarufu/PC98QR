# Phase 3D-1のV40-L、生バイト、ビット詰め出力、複数RSブロックをホストで検証する。
"""Phase 3D-1 V40-L: raw bytes, packed output, multiple RS blocks, host only."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import unittest

import cv2
import numpy as np
import zxingcpp

import qr_v40_oracle as oracle

ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = (1, 255, 256, 1000, 1273, 1274, 2048, 2952, 2953)
VECTORS = tuple(oracle.payload(n) for n in BOUNDARIES) + (oracle.SPECIAL * 70,)


@lru_cache(maxsize=1)
def driver():
    # ホスト用ビルドはunittestの検出処理が担当し、Makefileは変更しない。
    # unittest discovery owns this host-only build; Makefile remains unchanged.
    override = os.environ.get('PC98QR_V40_DRIVER')
    if override:
        return Path(override)
    target = ROOT / 'build/test_qr_v40'
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get('HOST_CC', 'cc')), '-std=c99', '-O2',
                    '-Wall', '-Wextra', '-Werror', '-o', str(target),
                    str(ROOT / 'tests/encode_v40_main.c')], check=True, cwd=ROOT)
    return target


def call(mode, argument=None):
    args = [str(driver()), mode]
    if argument is not None:
        args.append(argument.hex() if isinstance(argument, bytes) else argument)
    result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if result.returncode:
        raise AssertionError(f'V40 driver {mode}: exit {result.returncode}\n{result.stderr}')
    return json.loads(result.stdout)


@lru_cache(maxsize=16)
def inspect(raw):
    return call('inspect', raw)


class V40BoundaryTests(unittest.TestCase):
    def test_invalid_null_pointers_and_unchanged_output(self):
        self.assertTrue(call('invalid'))  # 0, 2954, 65535, UINT_MAX; NULL/NULL too. / 日本語: 長さ0・2954・65535・UINT_MAXと、両ポインタNULLも試す。

    def test_valid_boundaries_input_and_output_canaries(self):
        for length in BOUNDARIES:
            with self.subTest(length=length):
                result = inspect(oracle.payload(length))
                self.assertEqual(len(bytes.fromhex(result['packed'])), 3917)

    def test_independent_bitstream_boundaries(self):
        for raw in VECTORS:
            with self.subTest(length=len(raw)):
                data = bytes.fromhex(inspect(raw)['data'])
                self.assertEqual(data, oracle.data_codewords(raw))
                self.assertEqual(len(data), 2956)
                bits = ''.join(f'{b:08b}' for b in data)
                self.assertEqual(bits[:4], '0100')
                self.assertEqual(int(bits[4:20], 2), len(raw))
                self.assertEqual(bytes(int(bits[i:i+8], 2) for i in range(20, 20+len(raw)*8, 8)), raw)
                self.assertEqual(bits[20+len(raw)*8:24+len(raw)*8], '0000')

    def test_count_255_256_is_16_bit_msb_first(self):
        for n in (255, 256, 1273, 1274, 2048, 2953):
            bits = ''.join(f'{b:08b}' for b in bytes.fromhex(inspect(oracle.payload(n))['data']))
            self.assertEqual(bits[4:20], f'{n:016b}')
        self.assertEqual(f'{255:016b}', '0000000011111111')
        self.assertEqual(f'{256:016b}', '0000000100000000')

    def test_maximum_exact_capacity_no_alignment_or_pad(self):
        raw = oracle.payload(2953)
        bits = '0100' + f'{2953:016b}' + ''.join(f'{b:08b}' for b in raw) + '0000'
        self.assertEqual(len(bits), 23648)
        data = bytes.fromhex(inspect(raw)['data'])
        self.assertEqual(''.join(f'{b:08b}' for b in data), bits)
        self.assertEqual(4 + 16 + 2953 * 8 + 4, 2956 * 8)
        self.assertGreater(4 + 16 + 2954 * 8, 2956 * 8)

    def test_short_payload_ec_11_padding(self):
        for n in BOUNDARIES[:-1]:
            data = bytes.fromhex(inspect(oracle.payload(n))['data'])
            self.assertEqual(data[n + 3:], bytes((0xEC, 0x11)[i % 2] for i in range(2953 - n)))

    def test_all_special_raw_bytes_survive(self):
        raw = oracle.SPECIAL * 70
        self.assertTrue(set((0, 0x1A, 0x80, 0xFF, 13, 10)).issubset(raw))
        self.assertEqual(bytes.fromhex(inspect(raw)['data']), oracle.data_codewords(raw))


class V40ReedSolomonTests(unittest.TestCase):
    def test_generator_independent_convolution(self):
        self.assertEqual(bytes.fromhex(call('constants')['generator']), bytes(oracle.generator()[1:]))
        self.assertEqual(len(oracle.generator()), 31)
        self.assertEqual(oracle.generator()[0], 1)

    def test_generator_all_30_roots(self):
        self.assertEqual(oracle.syndromes(oracle.generator()), [0] * 30)

    def test_arbitrary_short_and_long_blocks(self):
        rng = random.Random(0x253C1)
        for n in (118, 119):
            for raw in (bytes(n), bytes([255]) * n, bytes(range(n)), rng.randbytes(n), rng.randbytes(n)):
                with self.subTest(length=n, prefix=raw[:4]):
                    actual = bytes.fromhex(call('ecc', raw))
                    self.assertEqual(actual, oracle.ecc(raw))
                    self.assertEqual(oracle.syndromes(raw + actual), [0] * 30)

    def test_split_lengths_offsets_and_group_boundary(self):
        result = inspect(oracle.payload(2953))
        data = bytes.fromhex(result['data'])
        blocks = result['blocks']
        self.assertEqual([len(bytes.fromhex(b['data'])) for b in blocks], list(oracle.LENGTHS))
        self.assertEqual([b['offset'] for b in blocks], [sum(oracle.LENGTHS[:b]) for b in range(25)])
        self.assertEqual(b''.join(bytes.fromhex(b['data']) for b in blocks), data)
        self.assertEqual(bytes.fromhex(blocks[18]['data'])[-1], data[2241])
        self.assertEqual(bytes.fromhex(blocks[19]['data'])[0], data[2242])
        indices = [i for b, n in zip(blocks, oracle.LENGTHS) for i in range(b['offset'], b['offset'] + n)]
        self.assertEqual(indices, list(range(2956)))

    def test_all_blocks_ecc_and_syndromes(self):
        for raw in VECTORS:
            for b in inspect(raw)['blocks']:
                data, ecc = bytes.fromhex(b['data']), bytes.fromhex(b['ecc'])
                self.assertEqual(ecc, oracle.ecc(data))
                self.assertEqual(oracle.syndromes(data + ecc), [0] * 30)

    def test_independent_final_interleaved_sequence(self):
        for raw in VECTORS:
            result = inspect(raw)
            words = bytes.fromhex(result['interleaved'])
            self.assertEqual(len(words), 3706)
            self.assertEqual(words, oracle.interleave(bytes.fromhex(result['data'])))
            extras = bytes(bytes.fromhex(b['data'])[-1] for b in result['blocks'][19:])
            self.assertEqual(words[2950:2956], extras)
            self.assertEqual(words[2956:], bytes(bytes.fromhex(b['ecc'])[j]
                                               for j in range(30) for b in result['blocks']))

    def test_interleave_unique_source_labels(self):
        # 2枚のバイト面で0～2955の識別値を作り、重複や欠落を検出する。
        # Two byte planes form distinct labels 0..2955, detecting duplicates/loss.
        low = bytes.fromhex(call('interleave', bytes(i & 255 for i in range(2956))))
        high = bytes.fromhex(call('interleave', bytes(i >> 8 for i in range(2956))))
        actual = [lo | hi << 8 for lo, hi in zip(low[:2956], high[:2956])]
        offsets = [sum(oracle.LENGTHS[:b]) for b in range(25)]
        expected = [offsets[b] + j for j in range(119) for b in range(25) if j < oracle.LENGTHS[b]]
        self.assertEqual(actual, expected)
        self.assertEqual(sorted(actual), list(range(2956)))
        self.assertEqual(actual[2950:], [2360, 2479, 2598, 2717, 2836, 2955])

    def test_external_reference_is_final_interleaved_not_data_prefix(self):
        for raw in VECTORS:
            result = inspect(raw)
            expected = bytes(oracle.reference(raw, 0).data_cache)
            self.assertEqual(bytes.fromhex(result['interleaved']), expected)
            self.assertNotEqual(bytes.fromhex(result['data']), expected[:2956])


class V40GeometryTests(unittest.TestCase):
    def test_reserved_geometry_and_counts(self):
        result = inspect(VECTORS[-1])
        cells = oracle.functions()
        reserved = oracle.matrix(result['reserved'])
        actual = oracle.matrix(result['functions'])
        # ISO表1に基づき、機能1614と形式/Version/固定黒67で予約セルは1681。
        # ISO table 1: 1614 function + 67 format/version/dark = 1681 reserved.
        self.assertEqual(len(cells), 1614 + 67)
        self.assertEqual(3*64 + 2*161 + 46*25 - 10*5 + 1 + 30 + 36, 1681)
        for y in range(177):
            for x in range(177):
                self.assertEqual(reserved[y][x], int((x, y) in cells))
                self.assertEqual(actual[y][x], cells.get((x, y), 0))
        self.assertEqual(sum(map(sum, reserved)), 1681)
        self.assertEqual(177*177 - 1681, 3706*8)

    def test_46_alignment_patterns_and_three_exclusions(self):
        rows = oracle.matrix(inspect(VECTORS[0])['functions'])
        template = [[int(c) for c in s] for s in ('11111','10001','10101','10001','11111')]
        self.assertEqual(len(oracle.ALIGNMENTS), 46)
        for y in oracle.CENTERS:
            for x in oracle.CENTERS:
                actual = [r[x-2:x+3] for r in rows[y-2:y+3]]
                with self.subTest(center=(x,y)):
                    if (x,y) in ((6,6),(6,170),(170,6)):
                        self.assertNotEqual(actual, template)
                    else:
                        self.assertEqual(actual, template)
        self.assertEqual(sum(x == 6 or y == 6 for x,y in oracle.ALIGNMENTS), 10)

    def test_dark_module_and_finder_separators(self):
        rows = oracle.matrix(inspect(VECTORS[0])['functions'])
        self.assertEqual(rows[169][8], 1)
        for x0,y0 in ((0,0),(170,0),(0,170)):
            self.assertEqual([row[x0:x0+7] for row in rows[y0:y0+7]],
                             [[int(c) for c in s] for s in
                              ('1111111','1000001','1011101','1011101','1011101','1000001','1111111')])
        for x in range(8):
            self.assertEqual(rows[7][x], 0)
            self.assertEqual(rows[169][x], 0)
            self.assertEqual(rows[x][7], 0)
            self.assertEqual(rows[x][169], 0)

    def test_version_polynomial_and_both_copies_all_masks(self):
        self.assertEqual(oracle.bch(40,12,0x1F25), 0x28C69)
        self.assertEqual(call('constants')['version'], 0x28C69)
        self.assertEqual(f'{0x28C69:018b}', '101000110001101001')
        self.assertEqual(set(oracle.version_positions()[0]),
                         {(x,y) for x in range(166,169) for y in range(6)})
        self.assertEqual(set(oracle.version_positions()[1]),
                         {(x,y) for x in range(6) for y in range(166,169)})
        for candidate in inspect(VECTORS[-1])['candidates']:
            rows = oracle.matrix(candidate['matrix'])
            for copy in oracle.version_positions():
                self.assertEqual(len(copy), 18)
                self.assertEqual(sum(rows[y][x] << i for i,(x,y) in enumerate(copy)), 0x28C69)

    def test_format_l_bch_xor_two_copies_all_masks(self):
        expected = [oracle.format_word(mask) for mask in range(8)]
        self.assertEqual(call('constants')['formats'], expected)
        for mask,candidate in enumerate(inspect(VECTORS[-1])['candidates']):
            rows = oracle.matrix(candidate['matrix'])
            for copy in oracle.format_positions():
                word = sum(rows[y][x] << i for i,(x,y) in enumerate(copy))
                self.assertEqual(word, expected[mask])
                self.assertEqual(((word ^ 0x5412) >> 10) >> 3, 1)

    def test_placement_label_bijection_no_remainder_all_masks(self):
        result = call('placement')
        positions = oracle.placement()
        self.assertEqual(len(positions), 29648)
        self.assertEqual(len(set(positions)), 29648)
        planes = [oracle.matrix(p) for p in result['planes']]
        labels = [sum((plane[y][x] ^ oracle.mask_at(0,x,y)) << i for i,plane in enumerate(planes))
                  for x,y in positions]
        self.assertEqual(labels, list(range(1,29649)))
        for mask,logical in enumerate(result['zero_masks']):
            rows = oracle.matrix(logical)
            self.assertTrue(all(rows[y][x] == oracle.mask_at(mask,x,y) for x,y in positions))
            for (x,y),value in oracle.functions(mask).items():
                self.assertEqual(rows[y][x], value)

    def test_all_codeword_bits_no_remainder_unmask(self):
        result = inspect(oracle.payload(2953))
        bits = ''.join(f'{b:08b}' for b in bytes.fromhex(result['interleaved']))
        self.assertEqual(len(bits), 29648)
        self.assertEqual(len(oracle.placement()) - len(bits), 0)
        for mask,candidate in enumerate(result['candidates']):
            rows = oracle.matrix(candidate['matrix'])
            extracted = ''.join(str(rows[y][x] ^ int(oracle.mask_at(mask,x,y))) for x,y in oracle.placement())
            self.assertEqual(extracted, bits)


class V40MaskPackedTests(unittest.TestCase):
    def test_external_fixed_masks_every_cell(self):
        for raw in VECTORS:
            for mask,candidate in enumerate(inspect(raw)['candidates']):
                with self.subTest(length=len(raw),mask=mask):
                    ref = oracle.reference(raw, mask)
                    self.assertEqual(ref.version,40)
                    self.assertEqual(len(ref.data_list),1)
                    self.assertEqual(ref.data_list[0].mode,4)
                    self.assertEqual(oracle.matrix(candidate['matrix']), ref.get_matrix())

    def test_all_penalty_components_and_best_mask(self):
        for raw in VECTORS:
            result = inspect(raw)
            expected = [oracle.penalties(oracle.matrix(c['matrix'])) for c in result['candidates']]
            self.assertEqual([c['score'] for c in result['candidates']], expected)
            best = min(range(8), key=lambda m:(expected[m][-1],m))
            self.assertEqual(result['selected'],best)
            self.assertEqual(result['logical'],result['candidates'][best]['matrix'])

    def test_artificial_penalty_over_65535(self):
        for c in '01':
            logical = c * 31329
            expected = oracle.penalties(oracle.matrix(logical))
            self.assertEqual(expected, [61950,92928,0,90,154968])
            self.assertEqual(call('penalty',logical), expected)
            self.assertGreater(expected[-1],65535)

    def test_artificial_n3_and_scaled_edge_patterns(self):
        for line in ('10111010000'*16+'0', '1100111111001100000000'*8+'0',
                     '1011101'+'0'*170, '01'*88+'0'):
            self.assertEqual(len(line),177)
            logical = line * 177
            self.assertEqual(call('penalty',logical),oracle.penalties(oracle.matrix(logical)))

    def test_real_minimum_tie_selects_lowest_mask(self):
        # 決定的なLCGの203番目の試行について、Pythonが全マスクを独立に採点する。
        # Deterministic LCG trial 203; Python independently scores all masks.
        state = 0x403D1
        raw = bytearray(2953)
        for _ in range(204):
            for i in range(2953):
                state = (1664525 * state + 1013904223) & 0xFFFFFFFF
                raw[i] = state >> 24
        result = inspect(bytes(raw))
        scores = [oracle.penalties(oracle.matrix(c['matrix'])) for c in result['candidates']]
        self.assertEqual([c['score'] for c in result['candidates']], scores)
        self.assertEqual([p[-1] for p in scores],
                         [21186,21685,21186,22250,21881,21316,21838,22296])
        self.assertEqual([m for m in range(8) if scores[m][-1] == min(p[-1] for p in scores)], [0,2])
        self.assertEqual(result['selected'],0)
        self.assertEqual(result['logical'],result['candidates'][0]['matrix'])

    def test_n4_all_dark_counts_and_large_intermediate(self):
        scores = call('balance')
        self.assertEqual(scores,[oracle.balance(dark) for dark in range(31330)])
        self.assertEqual(scores[0],90)
        self.assertEqual(scores[-1],90)
        self.assertGreater(10*31329,65535)

    def test_packed_public_and_private_all_modules_unused_bits(self):
        for raw in VECTORS:
            result = inspect(raw)
            packed = bytes.fromhex(result['packed'])
            self.assertEqual(oracle.unpack(packed),oracle.matrix(result['logical']))
            self.assertEqual(oracle.unpack(packed),oracle.reference(raw,result['selected']).get_matrix())
            self.assertEqual(packed[-1] & 0x7F,0)
            self.assertEqual(oracle.unpack(bytes.fromhex(result['reserved_packed'])),oracle.matrix(result['reserved']))
            self.assertEqual(bytes.fromhex(result['reserved_packed'])[-1] & 0x7F,0)
            for index in (0,7,8,176,177,31328):
                self.assertEqual((packed[index>>3] >> (7-(index&7))) & 1,int(result['logical'][index]))

    def test_mixed_repeated_all_existing_apis(self):
        # ソース編集前のクリーンなeda30a7のドライバーから記録したSHA-256。
        # SHA-256 recorded from the clean eda30a7 driver BEFORE source edits.
        baseline = {1000: 'ac646877eb10ae6b970655a9130f274e62da02fb802bc357c479823e3671740a',
                    1273: 'ae29504a26fc0e8908dac2425e03f65a8fa639c2312c654e06194de0235d2cbe'}
        for length in (1, 256, 1273, 1274, 2953):
            raw = oracle.payload(length)
            result = call('mixed',raw)
            self.assertEqual(result['v40'],inspect(raw)['packed'])
            for n, digest in baseline.items():
                self.assertEqual(hashlib.sha256(bytes.fromhex(result[f'v25_{n}'])).hexdigest(),digest)


@lru_cache(maxsize=2)
def synthetic(length):
    rows = np.array(oracle.unpack(bytes.fromhex(inspect(oracle.payload(length))['packed'])),dtype=np.uint8)
    qr = np.repeat(np.repeat((1-np.pad(rows,4))*255,2,axis=0),2,axis=1)
    screen = np.full((400,640),255,dtype=np.uint8)
    screen[15:385,135:505] = qr
    return rows, qr, screen


class V40SyntheticTests(unittest.TestCase):
    def test_host_only_2px_geometry_every_pixel(self):
        for length in (2000,2953):
            rows, qr, screen = synthetic(length)
            self.assertEqual(qr.shape,(370,370))
            self.assertEqual((640-370)//2,135)
            self.assertEqual((400-370)//2,15)
            self.assertEqual((135+8,15+8),(143,23))
            self.assertEqual((177*2,177*2),(354,354))
            # QR本体の座標から画面の各画素を独立に参照する。
            # Quiet Zone・余白・各2×2モジュールの全画素を含む。
            # Independently address each screen pixel from core coordinates.
            # Includes all quiet-zone, margins and every 2x2 module pixel.
            ys,xs = np.indices((400,640))
            inside = (xs >= 143) & (xs < 497) & (ys >= 23) & (ys < 377)
            expected = np.full((400,640),255,dtype=np.uint8)
            expected[inside] = (1-rows[(ys[inside]-23)//2,(xs[inside]-143)//2])*255
            np.testing.assert_array_equal(screen,expected)
            self.assertTrue(np.all(qr[:8,:] == 255))
            self.assertTrue(np.all(qr[-8:,:] == 255))
            self.assertTrue(np.all(qr[:,:8] == 255))
            self.assertTrue(np.all(qr[:,-8:] == 255))
            self.assertEqual((135,640-505,15,400-385),(135,135,15,15))

    def test_host_synthetic_raw_decode_2000_and_2953(self):
        directory = ROOT/'build/phase3d-fixtures'
        directory.mkdir(exist_ok=True)
        for length in (2000,2953):
            raw = oracle.payload(length)
            self.assertTrue(set((0,0x1A,0x80,0xFF,13,10)).issubset(raw))
            _, _, screen = synthetic(length)
            self.assertTrue(cv2.imwrite(str(directory/f'v40-{length}-screen.png'),screen))
            decoded = zxingcpp.read_barcode(screen)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.bytes,raw)  # Never result.text. / 日本語: result.textは使わない。
            (directory/f'v40-{length}.bin').write_bytes(raw)


if __name__ == '__main__':
    unittest.main()
