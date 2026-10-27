# 標準ライブラリだけで、無視対象のbuild内にPhase 3C-2の実機入力データを再生成する。
"""Recreate Phase 3C-2 hardware inputs under ignored build/, using only stdlib."""

import hashlib
import json
from pathlib import Path


def payload(length):
    # Phase 3C-1のqr_v25_oracleと同じ決定的なベクトルを使う。
    # Same deterministic vector as tests/qr_v25_oracle.py (Phase 3C-1).
    special = bytes.fromhex("001a80ff0d0a0041c3287f")
    return (special + bytes((73 * i + 19) % 256 for i in range(length)))[:length]


def main():
    target = Path(__file__).resolve().parents[1] / "build/phase3c2-fixtures"
    target.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "BYTE1000.BIN": payload(1000),
        "BYTE1273.BIN": payload(1273),
        "BYTE1274.BIN": payload(1273) + b"\x00",
        # 元ファイルを変更せず、過去のベクトルを再現する。
        # Reproduce the historical vector without modifying its original file.
        "BYTE26.BIN": bytes.fromhex("00011A7F80FFC3280041421020304050607080FF001A80FF0D0A"),
    }
    manifest = {}
    for name, raw in fixtures.items():
        (target / name).write_bytes(raw)
        manifest[name] = {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "first16": raw[:16].hex().upper(),
            "last16": raw[-16:].hex().upper(),
        }
    report = json.dumps(manifest, indent=2) + "\n"
    (target / "manifest.json").write_text(report, encoding="ascii")
    print(report, end="")


if __name__ == "__main__":
    main()
