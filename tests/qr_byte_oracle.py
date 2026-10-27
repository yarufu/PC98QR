# Byteビット列をCの書き込み処理とは独立したフィールド列から構成するホスト基準実装。
# ECCはqrcodeの多項式演算、評価点はPhase 2Bの座標方式を使う。
# 外部の自動マスク選択は使用しない。
"""Host-only Byte bitstream oracle and explicitly forced qrcode references.

The bitstream is composed as a declarative string of fields, independently of
C's bit writer. ECC uses qrcode's polynomial arithmetic; penalties retain the
Phase 2B coordinate oracle. No external auto-mask choice is used.
"""

from functools import lru_cache
import random

import qrcode
from qrcode import base
from qrcode.constants import ERROR_CORRECT_M, ERROR_CORRECT_Q
from qrcode.util import MODE_8BIT_BYTE, QRData

from qr_mask_oracle import penalties

VECTORS = tuple(bytes.fromhex(value) for value in (
    "00", "FF", "00FF", "00017F80FF", "004180FF1A", "410042", "C328",
    "00011A7F80FFC328004142",
))
SINGLE_BYTES = tuple(bytes([value]) for value in range(256))


def samples():
    rng = random.Random(0x98212C)
    return tuple(dict.fromkeys((*SINGLE_BYTES, *VECTORS, *(
        rng.randbytes(length) for length in range(2, 12) for _ in range(8)
    ))))


def data_codewords(payload, version=1, ecc=ERROR_CORRECT_Q):
    capacity = {(1, ERROR_CORRECT_Q): 13, (2, ERROR_CORRECT_Q): 22,
                (2, ERROR_CORRECT_M): 28}[version, ecc]
    assert 1 <= len(payload) <= capacity - 2
    fields = ["0100", f"{len(payload):08b}", "".join(f"{value:08b}" for value in payload)]
    bits = "".join(fields)
    bits += "0" * min(4, capacity * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    words = bytes(int(bits[index:index + 8], 2) for index in range(0, len(bits), 8))
    return words + bytes((0xEC, 0x11)[index % 2] for index in range(capacity - len(words)))


def ecc_codewords(data, length=13):
    generator = base.Polynomial([1], 0)
    for power in range(length):
        generator *= base.Polynomial([1, base.gexp(power)], 0)
    remainder = (base.Polynomial(list(data) + [0] * length, 0) % generator).num
    return bytes(length - len(remainder)) + bytes(remainder)


@lru_cache(maxsize=4096)
def reference(payload, mask=0, version=1, ecc=ERROR_CORRECT_Q):
    assert (version, ecc) in ((1, ERROR_CORRECT_Q), (2, ERROR_CORRECT_Q), (2, ERROR_CORRECT_M))
    qr = qrcode.QRCode(version=version, error_correction=ecc,
                       mask_pattern=mask, border=0)
    qr.add_data(QRData(payload, mode=MODE_8BIT_BYTE), optimize=0)
    qr.make(fit=False)
    return qr


@lru_cache(maxsize=512)
def candidate_scores(payload, version=1, ecc=ERROR_CORRECT_Q):
    return tuple(penalties(reference(payload, mask, version, ecc).get_matrix()) for mask in range(8))


def best_mask(payload, version=1, ecc=ERROR_CORRECT_Q):
    scores = candidate_scores(payload, version, ecc)
    return min(range(8), key=lambda mask: (scores[mask][-1], mask))


def selected_reference(payload, version=1, ecc=ERROR_CORRECT_Q):
    return reference(payload, best_mask(payload, version, ecc), version, ecc)
