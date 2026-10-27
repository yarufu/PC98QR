# 受信側やCを参照せず仕様から転送バイト列を作る試験専用基準実装。
# 先頭容量と後続容量で消費して分割し、chunk数から総数を得る。
# frame関数は異常系試験用に不正な内容も許す。
"""Test-only wire construction from the specification; no receiver/C imports.

Split by consuming the first capacity and then ordinary capacities. Count is
the resulting number of chunks, not the receiver's or C helper's calculation.
The frame() primitive intentionally permits malformed bodies for fault tests.
"""
import struct

NAMES = (b"A", b"AB", b"ABC", b"ABCD", b"ABCDE", b"ABCDEF", b"ABCDEFG", b"ABCDEFGH",
         b"ABCDEFG.X", b"ABCDEFGH.X", b"ABCDEFGH.XY", b"ABCDEFGH.XYZ")
PREFIX_6000 = bytes.fromhex("50393851020000000308544553542e4d4147")
SPECIAL = bytes.fromhex("00011a7f80ffc328000d0a414200ff1a")


def fixture(size):
    return bytes((i * 37 + (i // 256) * 11 + (i // 65536) * 19 + 3) % 256
                 for i in range(size))


def frame(index, count, body, version=2):
    return struct.pack(">4sBHH", b"P98Q", version, index, count) + body


def split(source, name=b"TEST.MAG"):
    if not source or not 1 <= len(name) <= 12:
        raise ValueError("oracle requires a nonempty source and a 1..12-byte name")
    first = 2944 - 1 - len(name)
    chunks = [source[:first]]
    offset = first
    while offset < len(source):
        chunks.append(source[offset:offset + 2944])
        offset += 2944
    return [frame(i, len(chunks), (bytes([len(name)]) + name if i == 0 else b"") + chunk)
            for i, chunk in enumerate(chunks)]
