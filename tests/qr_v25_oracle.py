# V25-Lの演算・配置・評価点を独立に定義するホスト基準実装。
# 本番コードを外部から導出せず、qrcodeは固定プロファイルと固定マスクの比較だけに使う。
# data_cacheは最終インターリーブ列であり、順次データ符号語ではない。
"""Host-only V25-L definitions and independent arithmetic/layout/penalty oracle.

No production code is derived from an external encoder. qrcode is called only
by reference() as a forced-profile, forced-mask test oracle; data_cache is the
FINAL INTERLEAVED sequence, never the sequential data codewords.
"""
from functools import lru_cache
from itertools import groupby

import qrcode
from qrcode.constants import ERROR_CORRECT_L
from qrcode.util import MODE_8BIT_BYTE, QRData

from qr_mask_oracle import n3_coordinates

SIZE = 117
CENTERS = (6, 32, 58, 84, 110)
ALIGNMENTS = tuple((x, y) for y in CENTERS for x in CENTERS
                   if (x, y) not in ((6, 6), (110, 6), (6, 110)))
LENGTHS = (106,) * 8 + (107,) * 4
SPECIAL = bytes.fromhex("001a80ff0d0a0041c3287f")


def payload(length):
    return (SPECIAL + bytes((i * 73 + 19) % 256 for i in range(length)))[:length]


def data_codewords(raw):
    assert 1 <= len(raw) <= 1273
    bits = '0100' + f'{len(raw):016b}' + ''.join(f'{x:08b}' for x in raw)
    bits += '0' * min(4, 10208 - len(bits))
    bits += '0' * (-len(bits) % 8)
    encoded = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    return encoded + bytes((0xEC, 0x11)[i % 2] for i in range(1276 - len(encoded)))


def multiply(a, b):
    # Cの逐次乗算方式ではなく、桁上がりなしの整数積から多項式剰余を取る。
    # Carryless integer product, then polynomial reduction (not C's peasant loop).
    product = 0
    for power in range(8):
        if b & (1 << power):
            product ^= a << power
    while product.bit_length() > 8:
        product ^= 0x11D << (product.bit_length() - 9)
    return product


@lru_cache(maxsize=1)
def generator():
    # 昇べき順で多項式を畳み込み、API境界でだけ逆順にする。
    # Ascending polynomial convolution, reversed only at the API boundary.
    coefficients, root = [1], 1
    for _ in range(26):
        next_coefficients = [0] * (len(coefficients) + 1)
        for power, c in enumerate(coefficients):
            next_coefficients[power] ^= multiply(c, root)
            next_coefficients[power + 1] ^= c
        coefficients = next_coefficients
        root = multiply(root, 2)
    return tuple(reversed(coefficients))


def evaluate(coefficients, x):
    result = 0
    for c in coefficients:
        result = multiply(result, x) ^ c
    return result


def syndromes(codeword):
    root, values = 1, []
    for _ in range(26):
        values.append(evaluate(codeword, root))
        root = multiply(root, 2)
    return values


@lru_cache(maxsize=256)
def ecc(data):
    # Cのブロック全体の被除数とは独立した、逐次剰余レジスターを使う。
    # Streaming remainder register, independent of C's whole-block dividend.
    register = [0] * 26
    for c in data:
        factor = c ^ register[0]
        register = register[1:] + [0]
        register = [r ^ multiply(factor, g) for r, g in zip(register, generator()[1:])]
    return bytes(register)


def split(data):
    assert len(data) == 1276
    blocks, start = [], 0
    for n in LENGTHS:
        blocks.append(data[start:start + n])
        start += n
    assert start == len(data)
    return blocks


def interleave(data):
    blocks = split(data)
    errors = [ecc(block) for block in blocks]
    return bytes(block[j] for j in range(107) for block in blocks if j < len(block)) + bytes(
        block[j] for j in range(26) for block in errors)


def bch(data, shift, polynomial):
    value = data << shift
    while value.bit_length() >= polynomial.bit_length():
        value ^= polynomial << (value.bit_length() - polynomial.bit_length())
    return (data << shift) | value


def format_word(mask):
    return bch(8 | mask, 10, 0x537) ^ 0x5412


def format_positions():
    vertical = [(8, y) for y in (*range(6), 7, 8, *range(110, 117))]
    horizontal = [(x, 8) for x in (*range(116, 108, -1), 7, 5, 4, 3, 2, 1, 0)]
    return vertical, horizontal


def version_positions():
    top = [(x, y) for y in range(6) for x in range(106, 109)]
    return top, [(y, x) for x, y in top]


def functions(mask=0):
    cells = {}
    # Cの輪判定とは独立に、直交座標の正方形パターンで構成する。
    # Cartesian square templates, deliberately independent from C's ring tests.
    finder = ('1111111', '1000001', '1011101', '1011101', '1011101', '1000001', '1111111')
    for x0, y0 in ((0, 0), (110, 0), (0, 110)):
        for y in range(max(y0 - 1, 0), min(y0 + 8, SIZE)):
            for x in range(max(x0 - 1, 0), min(x0 + 8, SIZE)):
                cells[x, y] = 0
        cells.update({(x0 + x, y0 + y): int(finder[y][x]) for x in range(7) for y in range(7)})
    for i in range(8, 109):
        cells[i, 6] = cells[6, i] = int(i % 2 == 0)
    align = ('11111', '10001', '10101', '10001', '11111')
    for x0, y0 in ALIGNMENTS:
        cells.update({(x0 + x - 2, y0 + y - 2): int(align[y][x])
                      for x in range(5) for y in range(5)})
    cells[8, 109] = 1
    for copy in format_positions():
        for bit, point in enumerate(copy):
            cells[point] = (format_word(mask) >> bit) & 1
    for copy in version_positions():
        for bit, point in enumerate(copy):
            cells[point] = (bch(25, 12, 0x1F25) >> bit) & 1
    return cells


def placement():
    reserved = functions()
    columns = list(range(116, 6, -2)) + [5, 3, 1]
    result = []
    for pair, right in enumerate(columns):
        ys = range(116, -1, -1) if pair % 2 == 0 else range(117)
        result.extend((x, y) for y in ys for x in (right, right - 1) if (x, y) not in reserved)
    return result


def mask_at(mask, x, y):
    # 外部マスクヘルパーに頼らず論理式で定義する。
    # Boolean definitions without relying on external mask helpers.
    return ((x + y) % 2 == 0, y % 2 == 0, x % 3 == 0, (x + y) % 3 == 0,
            (y // 2 + x // 3) % 2 == 0, (x * y) % 6 == 0,
            ((x * y) % 2 + (x * y) % 3) % 2 == 0,
            ((x + y) % 2 + (x * y) % 3) % 2 == 0)[mask]


def unpack(packed):
    return [[(packed[(y * SIZE + x) // 8] >> (7 - (y * SIZE + x) % 8)) & 1
             for x in range(SIZE)] for y in range(SIZE)]


def matrix(logical):
    assert len(logical) == SIZE * SIZE
    return [[int(c) for c in logical[y * SIZE:(y + 1) * SIZE]] for y in range(SIZE)]


@lru_cache(maxsize=16384)
def line_n3(line):
    # 既存の独立した座標・パターン方式を使い、Cのラン長ロジックは使わない。
    # Existing independent coordinate/template oracle, no run-length C logic.
    return n3_coordinates(line)


def balance(dark):
    return 10 * sum(10 * abs(2 * dark - SIZE * SIZE) > step * SIZE * SIZE for step in range(1, 11))


def penalties(rows):
    lines = [tuple(row) for row in rows] + list(zip(*rows))
    n1 = sum(n - 2 for line in lines for _, group in groupby(line)
             for n in [len(list(group))] if n >= 5)
    n2 = 3 * sum(rows[y][x] == rows[y][x + 1] == rows[y + 1][x] == rows[y + 1][x + 1]
                 for y in range(116) for x in range(116))
    n3 = sum(line_n3(line) for line in lines)
    n4 = balance(sum(map(sum, rows)))
    return [n1, n2, n3, n4, n1 + n2 + n3 + n4]


@lru_cache(maxsize=64)
def reference(raw, mask):
    qr = qrcode.QRCode(version=25, error_correction=ERROR_CORRECT_L,
                       mask_pattern=mask, border=0)
    qr.add_data(QRData(raw, mode=MODE_8BIT_BYTE), optimize=0)
    qr.make(fit=False)
    return qr
