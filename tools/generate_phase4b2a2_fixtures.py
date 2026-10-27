# 単独DOS診断FSIZE用に、サイズだけを調べる試験データを生成する。
"""Generate size-only fixtures for the standalone FSIZE DOS probe."""

import json
from pathlib import Path


SIZES = (0, 1, 65535, 65536, 65537, 1474560)


def main():
    target = Path(__file__).resolve().parents[1] / "build/phase4b2a2-fixtures"
    target.mkdir(parents=True, exist_ok=True)
    manifest = []
    for size in SIZES:
        name = f"S{size}.BIN"
        with (target / name).open("wb") as stream:
            stream.truncate(size)
        manifest.append({"filename": name, "size": size})
    report = json.dumps(manifest, indent=2) + "\n"
    (target / "manifest.json").write_text(report, encoding="ascii")
    print(report, end="")


if __name__ == "__main__":
    main()
