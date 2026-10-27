# Phase 2Bの明示的契約に対する独立基準実装。
# 外部の自動マスク選択やlost_pointを使わず、N3はCのラン配列ではなく座標と拡大セルパターンで検査する。
"""Independent host oracle for the explicit PC98QR Phase 2B contract.

No external encoder's automatic mask choice or lost_point() is used here.
N3 checks coordinates and scaled cell templates, not C's run-length array.
"""

from functools import lru_cache
from itertools import groupby

import qrcode
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.util import MODE_ALPHA_NUM, QRData


def reference(text, mask=0):
    qr = qrcode.QRCode(version=1, error_correction=ERROR_CORRECT_Q,
                       mask_pattern=mask, border=0)
    qr.add_data(QRData(text, mode=MODE_ALPHA_NUM), optimize=0)
    qr.make(fit=False)
    return qr


def n3_coordinates(line):
    # 全開始位置と倍率kを調べ、両端を含め5つの完全なランを要求する。
    """Inspect every start and k; require five complete runs, including ends."""
    line = tuple(line)
    score = 0
    for k in range(1, len(line) // 7 + 1):
        pattern = (1,) * k + (0,) * k + (1,) * (3 * k) + (0,) * k + (1,) * k
        for start in range(len(line) - len(pattern) + 1):
            end = start + len(pattern)
            if line[start:end] != pattern:
                continue
            if (start > 0 and line[start - 1]) or (end < len(line) and line[end]):
                continue  # A dark run continues outside the template. / 日本語: 黒ランがパターンの外まで続いている。
            left_edge = start - 1
            while left_edge >= 0 and line[left_edge] == 0:
                left_edge -= 1
            right_edge = end
            while right_edge < len(line) and line[right_edge] == 0:
                right_edge += 1
            # どちらかの端に達すれば、4kに十分な仮想白領域があると扱う。
            # Reaching either edge supplies sufficient virtual light for 4k.
            left = 4 * k if left_edge < 0 else start - 1 - left_edge
            right = 4 * k if right_edge == len(line) else right_edge - end
            score += 40 * (left >= 4 * k and right >= k)
            score += 40 * (right >= 4 * k and left >= k)
    return score


def balance(dark, size=21):
    # Cの除算式とは独立に、5%偏差の厳密な閾値を数える。
    # Count strict 5%-deviation thresholds; independent of C's division formula.
    total = size * size
    deviation = abs(2 * dark - total)
    return 10 * sum(10 * deviation > step * total for step in range(1, 11))


def penalties(matrix):
    rows = [tuple(map(int, row)) for row in matrix]
    size = len(rows)
    assert size in (21, 25) and all(len(row) == size for row in rows)
    assert all(cell in (0, 1) for row in rows for cell in row)
    lines = rows + list(zip(*rows))
    n1 = sum(max(0, length - 2) if length >= 5 else 0
             for line in lines for _, group in groupby(line)
             for length in [len(list(group))])
    n2 = 3 * sum(len({rows[y][x], rows[y + 1][x], rows[y][x + 1], rows[y + 1][x + 1]}) == 1
                 for y in range(size - 1) for x in range(size - 1))
    n3 = sum(n3_coordinates(line) for line in lines)
    n4 = balance(sum(map(sum, rows)), size)
    return n1, n2, n3, n4, n1 + n2 + n3 + n4


@lru_cache(maxsize=1024)
def candidate_scores(text):
    return tuple(penalties(reference(text, mask).get_matrix()) for mask in range(8))


def best_mask(text):
    scores = candidate_scores(text)
    return min(range(8), key=lambda mask: (scores[mask][-1], mask))


def selected_reference(text):
    return reference(text, best_mask(text))
