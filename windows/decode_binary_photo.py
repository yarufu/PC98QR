# QR写真1枚から生バイトを復号する。
# 再試行は画像の縮小だけで行う。
# OpenCVは画像読み込みと縮小、zxing-cppはQR復号を担当し、result.bytesをそのまま返す。
# 期待HEXとの比較は取得後に行い、不一致でも別倍率を探索し直さない。
"""Decode raw bytes from one QR photograph, with a resize-only fallback."""

import argparse
from pathlib import Path
import re
import sys

import cv2
import numpy as np
import zxingcpp

DECODE_SCALES = (1.0, 0.75, 0.5, 0.375, 0.25)


class QRDecodeError(ValueError):
    # QRは検出したが、どの再試行でも正しく復号できなかった状態。
    """A QR was detected but no fallback produced a valid decode."""


def parse_expect_hex(value):
    # 区切りなし、非空、偶数桁のASCII十六進値だけを受け付ける。
    """Accept a nonempty, even-length ASCII hex value, without separators."""
    if len(value) % 2 or re.fullmatch(r"[0-9A-Fa-f]+", value) is None:
        raise argparse.ArgumentTypeError(
            "expected hex must contain an even, nonzero number of 0-9, A-F, or a-f digits"
        )
    return bytes.fromhex(value)


def decode_image(image, *, report_errors=False):
    # 最初の有効なQRの生バイトと倍率を返し、未取得なら(None, None)。
    # 任意の診断指定で不正QRと未検出を区別する。
    # 単一写真CLIの従来の呼出し・エラー処理は維持する。
    """Return first valid QR's raw bytes and scale, or (None, None).

    Opt-in diagnostics distinguish invalid QR results from no detection.
    The single-photo CLI retains its original calls and error behavior.
    """
    height, width = image.shape[:2]
    invalid = None
    options = {"return_errors": True} if report_errors else {}
    for scale in DECODE_SCALES:
        candidate = image
        if scale != 1.0:
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            candidate = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        # 外側の各縮小候補に対して、ZXing内部の縮小探索も許可する。
        # Allow ZXing's internal downscaling for each external scale candidate.
        result = zxingcpp.read_barcode(candidate, formats=zxingcpp.BarcodeFormat.QRCode,
                                       try_downscale=True, **options)
        if result is not None and result.valid:
            return result.bytes, scale
        if result is not None and report_errors:
            invalid = str(result.error)
    if invalid is not None:
        raise QRDecodeError(f"Detected QR could not be decoded: {invalid}")
    return None, None


def decode_photo(path, *, report_errors=False):
    # 文字列復号器と同じく、pathlibでWindowsの日本語ファイル名を扱う。
    # pathlib supports Japanese Windows filenames, as in the text decoder.
    data = Path(path).read_bytes()
    if not data:
        raise ValueError("Empty image file")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not read the image as PNG/JPEG")
    decoded, _ = decode_image(image, report_errors=report_errors)
    return decoded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path, help="One PNG/JPEG photograph")
    parser.add_argument("--expect-hex", type=parse_expect_hex,
                        help="Exact expected bytes as contiguous hex digits")
    args = parser.parse_args(argv)
    try:
        decoded = decode_photo(args.photo)
    except (OSError, ValueError, cv2.error) as error:
        print(f"Image error: {error}", file=sys.stderr)
        return 2
    if decoded is None:
        print("No decodable QR code found.", file=sys.stderr)
        return 1
    print(f"Length: {len(decoded)}")
    print(f"Hex: {decoded.hex().upper()}")
    if args.expect_hex is not None and decoded != args.expect_hex:
        print(f"Expected exactly {args.expect_hex.hex().upper()}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
