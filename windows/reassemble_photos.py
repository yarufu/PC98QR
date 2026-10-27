# 転送 QR の写真から元のバイナリファイルを復元する受信側の入口。
# 写真を順に読み取り、全フレームの形式・総数・重複・欠落を検証した後、
# index 順に並べ、ヘッダーとファイル名情報を除いた元データだけを保存する。
# version 01 は出力先の指定が必要。version 02 は検証済みのファイル名を利用できる。
# 既存ファイルは上書きせず、全フレームの検証が成功してから出力を新規作成する。
# セッション ID やファイル全体のチェックサムはないため、写真は同じ転送のものを
# 指定する。構造の検証だけでは、すべてのファイル混在や破損は検出できない。

# 1枚にQR一つの写真群から1ファイルを復元する。
# v1/v2にはID・元サイズ・チェックサムがないため同じ転送の写真だけを渡す。
# 構造検証で全混在は検出できない。
# v1は出力指定必須、v2は検証済みの名前を現在ディレクトリで使える。
"""Restore one binary file from transport QR photos (one QR per photo).

Versions 1 and 2 have no file/session ID, file size or checksum. Supply photos
from only one transfer: structural validation cannot detect all mixed-file input.
Version 1 requires --output; version 2 can use its validated filename in cwd.
"""

import argparse
import os
from pathlib import Path
import sys
from typing import NamedTuple

if __package__:
    from . import decode_binary_photo as decoder
else:
    import decode_binary_photo as decoder

HEADER_SIZE = 9
MAX_FRAME_BYTES = 2953
MAX_PAYLOAD_BYTES = 2944
FILENAME_CHARACTERS = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~")
RESERVED_BASENAMES = frozenset(
    [b"CON", b"CONIN$", b"CONOUT$", b"PRN", b"AUX", b"NUL", b"CLOCK$", b"COM", b"LPT", b"LST"]
    + [prefix + bytes([digit]) for prefix in (b"COM", b"LPT") for digit in range(48, 58)]
)


class ReceiveError(ValueError):
    # 安定した診断分類を持つ、想定内の入出力エラー。
    """An expected input/output error, with a stable diagnostic category."""

    def __init__(self, category, detail, exit_code=1):
        super().__init__(f"{category}: {detail}")
        self.category = category
        self.exit_code = exit_code


class Summary(NamedTuple):
    frame_count: int
    duplicate_count: int
    byte_count: int


def _validate_filename_v2(name):
    # 文字列への変換やパス操作の前に、正規化済みメタデータの生バイトを検証する。
    """Validate canonical metadata bytes before any decoding or path handling."""
    parts = name.split(b".")
    if (not 1 <= len(name) <= 12 or len(parts) > 2 or not 1 <= len(parts[0]) <= 8
            or (len(parts) == 2 and not 1 <= len(parts[1]) <= 3)
            or any(byte not in FILENAME_CHARACTERS for part in parts for byte in part)):
        raise ReceiveError("filename_invalid", "expected canonical uppercase ASCII 8.3 filename")
    if parts[0] in RESERVED_BASENAMES:
        raise ReceiveError("filename_reserved", "reserved filename basename")


def _frame0_filename_v2(raw):
    # 共通ヘッダーと全長は検証済みとし、v2の先頭メタデータを検証する。
    # 名前の後に元データが最低1バイト必要。
    """Validate v2 frame 0 metadata and require at least one original file byte.

    The caller has already checked the common header and complete length.
    """
    length = raw[9]
    if not 1 <= length <= 12:
        raise ReceiveError("filename_length_invalid", f"filename length {length} must be 1..12")
    end = 10 + length
    if len(raw) < end:
        raise ReceiveError("filename_truncated", "frame 0 ends inside filename metadata")
    name = raw[10:end]
    _validate_filename_v2(name)
    if len(raw) == end:
        raise ReceiveError("frame_length_invalid", "frame 0 must contain raw file bytes after filename")
    return name


def _payload_start(raw):
    # 検証済み完全フレーム内の、元ファイルのバイト開始位置。
    """Original file bytes in an already validated complete frame."""
    return 10 + raw[9] if raw[4] == 2 and raw[5:7] == b"\x00\x00" else HEADER_SIZE


def parse_frame(raw):
    # 生の完全フレームを検証し、BE16のindexと総数を返す。
    """Validate a complete raw frame; return its BE16 index and count."""
    if not isinstance(raw, bytes):
        raise ReceiveError("qr_decode_invalid", "decoder must return raw bytes")
    if not HEADER_SIZE < len(raw) <= MAX_FRAME_BYTES:
        raise ReceiveError("frame_length_invalid",
                           f"decoded length {len(raw)} must be 10..2953 bytes")
    if raw[:4] != b"P98Q":
        raise ReceiveError("transport_magic_invalid", "expected P98Q at offset 0")
    if raw[4] not in (1, 2):
        raise ReceiveError("transport_version_unsupported", f"version {raw[4]}")
    index = int.from_bytes(raw[5:7], "big")
    count = int.from_bytes(raw[7:9], "big")
    if count == 0:
        raise ReceiveError("frame_count_invalid", "frame_count must be 1..65535")
    if index >= count:
        raise ReceiveError("frame_index_invalid", f"index {index} >= count {count}")
    if raw[4] == 2 and index == 0:
        _frame0_filename_v2(raw)
    if index < count - 1 and len(raw) - HEADER_SIZE != MAX_PAYLOAD_BYTES:
        raise ReceiveError("nonfinal_payload_length_invalid",
                           f"index {index}: payload must be 2944 bytes" if raw[4] == 1 else
                           f"index {index}: complete frame must be 2953 bytes")
    return index, count


def collect_frames(frames):
    # 入力全体を消費してから完全な集合として受理する。
    # 保持するのは重複を除いた完全フレームだけで、画像や文字列ではない。
    # 診断の入力番号は1始まり、フレームindexは0始まり。
    """Consume the entire iterable before accepting one complete collection.

    Keep only unique complete frame bytes, never images or text payloads.
    Input positions in diagnostics are one-based; frame indices are zero-based.
    """
    received = {}
    expected_count = None
    expected_version = None
    duplicates = 0
    for position, raw in enumerate(frames, start=1):
        try:
            index, count = parse_frame(raw)
        except ReceiveError as error:
            raise ReceiveError(error.category, f"input {position}: {error}") from error
        if expected_version is None:
            expected_version = raw[4]
        elif raw[4] != expected_version:
            raise ReceiveError("transport_version_mismatch",
                               f"input {position}: version {raw[4]}, expected {expected_version}")
        if expected_count is None:
            expected_count = count
        elif count != expected_count:
            raise ReceiveError("frame_count_mismatch",
                               f"input {position}: count {count}, expected {expected_count}")
        previous = received.get(index)
        if previous is not None:
            if previous != raw:
                raise ReceiveError("conflicting_duplicate",
                                   f"input {position}: different complete bytes for index {index}")
            duplicates += 1
        else:
            received[index] = raw
    if expected_count is None:
        raise ReceiveError("missing_frame", "no frames supplied")
    if len(received) != expected_count:
        missing = [index for index in range(expected_count) if index not in received]
        preview = ", ".join(map(str, missing[:20]))
        if len(missing) > 20:
            preview += ", ..."
        raise ReceiveError("missing_frame", f"{len(missing)} missing indices: {preview}")
    return [received[index] for index in range(expected_count)], duplicates


def photo_frames(photos):
    # 画像は1枚ずつ復号する。
    # decode_photoはyieldへ進む前に画素を解放する。
    """Decode one image at a time; decode_photo releases pixels before yielding."""
    for photo in photos:
        try:
            raw = decoder.decode_photo(photo, report_errors=True)
        except decoder.QRDecodeError as error:
            raise ReceiveError("qr_decode_invalid", f"{photo}: {error}") from error
        except (OSError, ValueError, decoder.cv2.error) as error:
            raise ReceiveError("input_photo_unreadable", f"{photo}: {error}", 2) from error
        except RuntimeError as error:
            raise ReceiveError("qr_decode_invalid", f"{photo}: {error}") from error
        if raw is None:
            raise ReceiveError("qr_not_found", f"{photo}: no decodable QR detected")
        yield raw


def _remove_partial(path, identity):
    # close後の最善努力の後始末。
    # 同じパスに置き換わったと分かる別ファイルは削除しない。
    """Best effort, after close; never delete a known replacement at this path."""
    try:
        current = path.lstat()
        if identity is None or not identity.st_ino or not os.path.samestat(identity, current):
            return "partial output cleanup refused: file identity changed or unavailable"
        path.unlink()
    except FileNotFoundError:
        return "partial output is no longer present"
    except OSError as error:
        return f"partial output cleanup failed: {error}"
    return "partial output removed"


def _write_output(path, frames):
    # 集合検証成功後だけ出力を排他的に新規作成する。
    # close/flush失敗もエラー。
    # rename・置換・親作成は行わず、不可分な公開や停電時の永続性は保証しない。
    """Exclusively create the output only after collection validation succeeds.

    Close/flush failures are errors too. No rename/replace or parent creation.
    This is not an atomic publication or a power-loss durability guarantee.
    """
    try:
        stream = path.open("xb")
    except FileExistsError as error:
        raise ReceiveError("output_already_exists", f"{path}: choose a new output name", 3) from error
    except (OSError, ValueError) as error:
        raise ReceiveError("output_write_error", f"{path}: {error}", 3) from error
    identity = None
    try:
        with stream:
            identity = os.fstat(stream.fileno())
            for raw in frames:
                payload = memoryview(raw)[_payload_start(raw):]
                if stream.write(payload) != len(payload):
                    raise OSError("short output write")
    except OSError as error:
        cleanup = _remove_partial(path, identity)
        raise ReceiveError("output_write_error", f"{path}: {error}; {cleanup}", 3) from error


def _reassemble_with_output(frames, output):
    # 従来の公開Summaryに加え、CLI用に実際の保存先を返す。
    """Return the unchanged public Summary plus the actual destination for CLI."""
    ordered, duplicates = collect_frames(frames)
    if output is None:
        if ordered[0][4] == 1:
            raise ReceiveError("output_required", "--output is required for protocol version 01", 2)
        name = _frame0_filename_v2(ordered[0]).decode("ascii")
        # 検証済みの名前は現在ディレクトリを基準とする。
        # 保存先を事前に解決しない。
        # 既存のシンボリックリンクも排他的openで衝突させる必要がある。
        # A validated basename is relative to this process's cwd. Do not resolve
        # the destination: an existing symlink must collide with exclusive open.
        output = Path(name)
    else:
        output = Path(output)
    _write_output(output, ordered)
    summary = Summary(len(ordered), duplicates, sum(len(raw) - _payload_start(raw) for raw in ordered))
    return summary, output


def reassemble_frames(frames, output=None):
    # 全フレームを検証後、元のファイルバイトだけをindex順に保存する。
    # 出力省略はv2だけで許し、戻り値は従来どおりSummary。
    """Validate every frame, then save only original file bytes in index order.

    Omitting output is allowed only for v2. The return value remains Summary.
    """
    summary, _ = _reassemble_with_output(frames, output)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="New binary file (required for v1; v2 defaults to filename in cwd); "
                             "existing files are never overwritten")
    parser.add_argument("photos", nargs="+", type=Path,
                        help="PNG/JPEG photos from one transfer, in any order")
    args = parser.parse_args(argv)
    try:
        summary, output = _reassemble_with_output(photo_frames(args.photos), args.output)
    except ReceiveError as error:
        print(f"Error: {error}", file=sys.stderr)
        return error.exit_code
    print(f"Restored: {summary.byte_count} bytes from {summary.frame_count} frames "
          f"({summary.duplicate_count} identical duplicates) -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
