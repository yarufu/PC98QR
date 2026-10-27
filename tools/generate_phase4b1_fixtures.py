# 本番Cとは独立に、実機確認用の単一フレームデータを生成する。
"""Generate single-frame hardware fixtures independently of production C."""

import hashlib
import json
from pathlib import Path


# v1転送形式はP98Q、version 1、index BE16=0、count BE16=1。
# Version 1 wire specification: P98Q, version 1, index BE16=0, count BE16=1.
HEADER = b"P98Q\x01\x00\x00\x00\x01"


def payload(length):
    special = bytes.fromhex("001a80ff0d0a") + b"P98Q"
    raw = (special + bytes((73 * i + 19) % 256 for i in range(length)))[:length]
    if length >= len(special) + 3:
        raw = raw[:-3] + bytes.fromhex("001aff")
    return raw


def make_fixtures():
    minimum = payload(1)
    maximum = payload(2944)
    return {
        "T1.BIN": minimum,
        "T2944.BIN": maximum,
        "T2945.BIN": maximum + b"\x00",
        "TF1.BIN": HEADER + minimum,
        "TF2944.BIN": HEADER + maximum,
    }


def main():
    target = Path(__file__).resolve().parents[1] / "build/phase4b1-fixtures"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, raw in make_fixtures().items():
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
