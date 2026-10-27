# Phase 4A-2のホスト専用v1転送モデル。
# 本番には含めない。
# 1組のフレームは1ファイルを表すが、IDやCRCがないため異なるファイルの混在を判別できない場合がある。
# ここで全データを保持することはPC-98のメモリ設計を意味しない。
"""Phase 4A-2 host-only Version 1 transport model, never production code.

One supplied frame collection represents one file. Without an ID or CRC,
structurally valid frames from different files can be indistinguishable.
Whole-file bytes and frame storage here imply no PC-98 memory design.
"""
from typing import Iterable, NamedTuple

HEADER_SIZE = 9
MAX_FRAME_BYTES = 2953
MAX_PAYLOAD_BYTES = 2944
MAX_FRAME_COUNT = 65535


class ParsedFrame(NamedTuple):
    frame_index: int
    frame_count: int
    payload: bytes


def frame_count_for_size(size: int) -> int:
    # 整数演算と転送形式の上限だけで、非空chunkの個数を求める。
    """Count nonempty chunks using only integer arithmetic and the wire limit."""
    if not isinstance(size, int):
        raise TypeError("file size must be an integer")
    if size <= 0:
        raise ValueError("file size must be positive")
    count = (size + MAX_PAYLOAD_BYTES - 1) // MAX_PAYLOAD_BYTES
    if count > MAX_FRAME_COUNT:
        raise ValueError("frame_count exceeds 65535")
    return count


def split_payload(data: bytes) -> list[bytes]:
    # ヘッダーと生データを含むフレームを返し、末尾に埋め草を加えない。
    """Return complete header + raw-payload frames, without terminal padding."""
    if not isinstance(data, bytes):
        raise TypeError("payload must be bytes")
    count = frame_count_for_size(len(data))
    frames = []
    for index in range(count):
        header = (b"P98Q" + bytes([1]) + index.to_bytes(2, "big")
                  + count.to_bytes(2, "big"))
        offset = index * MAX_PAYLOAD_BYTES
        frames.append(header + data[offset:offset + MAX_PAYLOAD_BYTES])
    return frames


def parse_full_frame(frame: bytes) -> ParsedFrame:
    # 明示的に選んだ転送フレームを長さ込みで検証する。
    # ホスト側の完全フレーム規則であり、Cのヘッダー専用パーサーは変更しない。
    # データ長は復号後の全長から求める。
    """Validate an explicitly selected transport frame, including its length.

    This host-only full-frame rule does not change the C header-only parser.
    Payload length is derived from the decoded frame length, not a wire field.
    """
    if not isinstance(frame, bytes):
        raise TypeError("frame must be bytes")
    if not HEADER_SIZE + 1 <= len(frame) <= MAX_FRAME_BYTES:
        raise ValueError("frame length must be 10..2953")
    if frame[:4] != b"P98Q":
        raise ValueError("invalid magic")
    if frame[4] != 1:
        raise ValueError("invalid version")
    index = int.from_bytes(frame[5:7], "big")
    count = int.from_bytes(frame[7:9], "big")
    if count == 0:
        raise ValueError("frame_count must be nonzero")
    if index >= count:
        raise ValueError("frame_index must be less than frame_count")
    payload = frame[HEADER_SIZE:]
    # 全長の検証により、ペイロードは既に1～2944バイトと分かっている。
    # Overall length already guarantees 1..2944 payload bytes.
    if index < count - 1 and len(payload) != MAX_PAYLOAD_BYTES:
        raise ValueError("non-final payload length must be 2944")
    return ParsedFrame(index, count, payload)


def reassemble_frames(frames: Iterable[bytes]) -> bytes:
    # 全フレームを検証してからindex順のデータを返す。
    # 同一フレームは一度だけ数え、不正形式・総数不一致・矛盾する重複・欠落は全体を無効にする。
    # 構造上の合格はファイルの同一性や完全性の証明ではない。
    """Validate the entire collection before returning payloads in index order.

    Identical complete frames count once. Any malformed frame, count mismatch,
    conflicting duplicate or missing index invalidates this collection.
    Structural acceptance cannot establish the identity/integrity of a file.
    """
    expected_count = None
    received = {}
    for raw in frames:
        frame = parse_full_frame(raw)
        if expected_count is None:
            expected_count = frame.frame_count
        elif frame.frame_count != expected_count:
            raise ValueError("different frame_count in one collection")
        previous = received.get(frame.frame_index)
        if previous is not None and previous != raw:
            raise ValueError("conflicting duplicate")
        received[frame.frame_index] = raw
    if expected_count is None or len(received) != expected_count:
        raise ValueError("missing frames: incomplete collection")
    # 受理済みindexは0～count−1で、重複しないindexがcount個そろっている。
    # Each accepted index is in 0..count-1 and there are count unique indices.
    return b"".join(received[index][HEADER_SIZE:]
                    for index in range(expected_count))
