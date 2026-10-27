# 既存のsmall model DOS配置を保ち、コードの埋め草をV30で安全な値にするビルド補助。
# 外部パッケージは不要。
# 不明なマーカーはエラーとし、未修正スクリプトへ代替しない。
# コード末尾の整列を0x90のNOPで埋め、導入済み原本は上書きしない。
# 既存出力と同一なら更新を省く。
"""Preserve the installed small-model DOS layout; use V30-safe text padding.

Run at build time (no third-party packages). An unfamiliar marker is an error,
never permission to fall back to the unpatched linker script.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import shlex
import subprocess
import tempfile


# 導入済みdos-mssl.ldと旧照合対象を突き合わせた長さ・SHA-256。
# 外部原文は保持しない。既知のバイト列だけを対象とし、構造検証も独立に行う。
# SHA-256には理論上の衝突可能性があり、原文比較と数学的に同等ではない。
TEXT_START_FINGERPRINT = (29, "a4fe379ba40c04123fffdc95f0bd741af3fce75b574dabe184e0e2ab05b9a941")
MARKER_FINGERPRINT = (256, "6ae4390c3d5a9ce75f4378090dc7cf1f4e125ec676edd3282ebd1eca6a42b10c")
INSERTION_OFFSET = 37  # マーカー先頭から__etextの代入行直後までのバイト数。
INSERTION = b"\t\tFILL(0x9090);\n"


def _find_unique(source, fingerprint):
    """Locate one known byte span without storing the external text."""
    length, digest = fingerprint
    found = None
    position = 0
    while position <= len(source) - length:
        if sha256(source[position:position + length]).hexdigest() != digest:
            position += 1
            continue
        if found is not None:
            raise ValueError("unexpected dos-mssl.ld text padding marker")
        found = position
        position += length  # bytes.countと同じく、重ならない出現を数える。
    if found is None:
        raise ValueError("unexpected dos-mssl.ld text padding marker")
    return found


def _generate(source, text_start, marker, insertion_offset):
    """Shared validation; synthetic tests supply their own fingerprints here."""
    if source.count(b"__etext =") != 1:
        raise ValueError("unexpected dos-mssl.ld text padding marker")
    start = _find_unique(source, text_start)
    end = _find_unique(source, marker)
    body = source[start + text_start[0]:end]
    if start >= end or any(token in body for token in (b"FILL", b"{", b"}")):
        raise ValueError("unexpected dos-mssl.ld text section structure")
    position = end + insertion_offset
    etext = b"__etext = .;\n"
    if (not len(etext) <= insertion_offset <= marker[0] or
            source[position - len(etext):position] != etext):
        raise ValueError("unexpected dos-mssl.ld text padding position")
    return source[:position] + INSERTION + source[position:]


def generate(source):
    # CLIの照合条件は固定。合成データの注入はテスト内だけで行う。
    return _generate(source, TEXT_START_FINGERPRINT, MARKER_FINGERPRINT, INSERTION_OFFSET)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="ia16-elf-gcc")
    parser.add_argument("--source", type=Path, help="explicit installed script (also for validation tests)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        source = args.source
        if source is None:
            name = subprocess.check_output([*shlex.split(args.cc), "-mcmodel=small",
                                            "-march=v30", "-print-file-name=dos-mssl.ld"],
                                           text=True).strip()
            if not name or name == "dos-mssl.ld":
                raise ValueError("compiler did not locate installed dos-mssl.ld")
            source = Path(name)
        if source.resolve() == args.output.resolve():
            raise ValueError("output must not overwrite the installed linker script")
        output = generate(source.read_bytes())
        if args.output.exists() and args.output.read_bytes() == output:
            return
        # 出力を変更する前に検証を終え、一時ファイルから不可分に置き換える。
        # 生成失敗や中断によって、利用可能に見える不完全なスクリプトを残さないための処理。
        # Validation happens before any output change. Replace atomically so a
        # failed/interrupted generation cannot leave a usable partial script.
        with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(output)
        try:
            temporary.replace(args.output)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"QRTEST linker generation failed: {error}\n")


if __name__ == "__main__":
    main()
