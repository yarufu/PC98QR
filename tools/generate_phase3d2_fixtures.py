# 標準ライブラリだけで、無視対象のbuild内にPhase 3D-2の実機入力データを再生成する。
"""Recreate Phase 3D-2 hardware inputs under ignored build/, using only stdlib."""

import hashlib
import json
from pathlib import Path


def payload(length):
    # Phase 3D-1のV40基準実装と同じ、特殊バイナリを含む決定的ベクトル。
    # Same deterministic special-binary vector as Phase 3D-1's V40 oracle.
    special = bytes.fromhex("001a80ff0d0a0041c3287f")
    return (special + bytes((73 * i + 19) % 256 for i in range(length)))[:length]


def main():
    target = Path(__file__).resolve().parents[1] / "build/phase3d2-fixtures"
    target.mkdir(parents=True, exist_ok=True)
    fixtures = {f"BYTE{n}.BIN": payload(n) for n in (1273, 1274, 2000, 2953)}
    # 最大の有効入力から最後の1バイトだけを変えたもの。
    # 切り捨ててはならない。
    # Only the final byte differs from the maximum valid input: never truncate.
    fixtures["BYTE2954.BIN"] = fixtures["BYTE2953.BIN"] + b"\x00"
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
