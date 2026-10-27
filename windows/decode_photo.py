# PNG/JPEG写真1枚を復号し、画像縮小だけで再試行する。
# OpenCVで文字列を取得し、最初の復号成功後に期待文字列と比較する。
# 生バイナリ用はdecode_binary_photo.pyに分離する。
"""Decode one PNG/JPEG photograph, with a resize-only fallback."""

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np

EXPECTED = "PC98-QR-TEST-001"
DECODE_SCALES = (1.0, 0.75, 0.5, 0.375, 0.25)


def decode_image(image):
    # 最初の空でない文字列と倍率を返し、未取得なら空文字列とNoneを返す。
    """Return the first nonempty payload and its scale, or ("", None)."""
    detector = cv2.QRCodeDetector()
    height, width = image.shape[:2]
    for scale in DECODE_SCALES:
        candidate = image
        if scale != 1.0:
            # 常に元画像から縮小し、小さい画像でも幅と高さを0にしない。
            # Always resize the original; keep even tiny images nonempty.
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            candidate = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        decoded, _, _ = detector.detectAndDecode(candidate)
        if decoded:
            return decoded, scale
    return "", None


def decode_photo(path):
    # pathlibでバイトを読むことで、Windowsの日本語ファイル名にも対応する。
    # Read bytes with pathlib so Japanese Windows filenames also work.
    data = Path(path).read_bytes()
    if not data:
        raise ValueError("Empty image file")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not read the image as PNG/JPEG")
    decoded, _ = decode_image(image)
    return decoded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path, help="One PNG/JPEG photograph")
    parser.add_argument("--expect", default=EXPECTED,
                        help="Exact expected payload (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        decoded = decode_photo(args.photo)
    except (OSError, ValueError, cv2.error) as error:
        print(f"Image error: {error}", file=sys.stderr)
        return 2
    if not decoded:
        print("No decodable QR code found.", file=sys.stderr)
        return 1
    print(decoded)
    if decoded != args.expect:
        print(f"Expected exactly {args.expect!r}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
