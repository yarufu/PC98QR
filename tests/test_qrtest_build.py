# 隔離環境で通常のmakeと、不明な形式を拒否するリンカー生成を検証する。
# 記録用コンパイラーでia16導入状況から独立させ、本番EXEの構築・監査は別途行う。
# ここでは本番EXEを上書きしない。
"""Isolated ordinary-make builds and fail-closed linker generation.

A recording compiler makes these host tests independent of installed ia16.
The real production EXE is built/audited separately, never overwritten here.
"""
import importlib.util
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qrtest_linker", ROOT / "tools/generate_qrtest_linker.py")
linker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(linker)
SOURCES = ["main", "pc98_graphics", "qr_encode", "frame_header", "file_size",
           "frame_count", "frame_offset", "file_chunk", "manual_transfer",
           "manual_selection", "pc98_key_input", "pc98_status_font",
           "filename_v2", "frame_v2"]
# 外部リンカー原文とは独立した合成データ。期待出力も実装から生成しない。
PREFIX = b"SECTIONS {\n/* synthetic test layout */\n"
TEXT_START = b"  .text : {\n    fixture_begin = .;\n"
BODY = b"    *(.fixture_startup)\n"
MARKER = (b"    *(.fixture_code)\n    __etext = .;\n    . = ALIGN(8);\n"
          b"    ASSERT(. < 4096, \"fixture bound\");\n  }\n")
SUFFIX = b"  .data : { *(.fixture_data) }\n}\n"
FIXTURE = PREFIX + TEXT_START + BODY + MARKER + SUFFIX
EXPECTED = (PREFIX + TEXT_START + BODY +
            b"    *(.fixture_code)\n    __etext = .;\n\t\tFILL(0x9090);\n"
            b"    . = ALIGN(8);\n    ASSERT(. < 4096, \"fixture bound\");\n  }\n" + SUFFIX)
TEXT_FINGERPRINT = (len(TEXT_START), sha256(TEXT_START).hexdigest())
MARKER_FINGERPRINT = (len(MARKER), sha256(MARKER).hexdigest())
INSERTION_OFFSET = 38


def generate_fixture(source):
    return linker._generate(source, TEXT_FINGERPRINT, MARKER_FINGERPRINT, INSERTION_OFFSET)


class QRTestBuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        shutil.copy(ROOT / "Makefile", self.root)
        shutil.copytree(ROOT / "src", self.root / "src")
        (self.root / "tools").mkdir()
        shutil.copy(ROOT / "tools/generate_qrtest_linker.py", self.root / "tools")
        # この隔離プロセス内だけで合成条件を注入し、実際のmain()とMakefileを通す。
        # 本番CLIには照合条件を変更する引数・環境変数・設定ファイルを追加しない。
        self.fixture_python = self.root / "fixture_python.py"
        self.fixture_python.write_text(f'''import functools, importlib.util, sys
script = sys.argv.pop(1)
spec = importlib.util.spec_from_file_location("fixture_linker", script)
linker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linker)
linker.generate = functools.partial(linker._generate,
    text_start={TEXT_FINGERPRINT!r}, marker={MARKER_FINGERPRINT!r},
    insertion_offset={INSERTION_OFFSET})
linker.main()
''')
        self.source = self.root / "dos-mssl.ld"
        self.source.write_bytes(FIXTURE)
        self.fake = self.root / "compiler.py"
        self.fake.write_text('''import json, sys
from pathlib import Path
args = sys.argv[1:]
if "-print-file-name=dos-mssl.ld" in args:
    assert args == ["-mcmodel=small", "-march=v30", "-print-file-name=dos-mssl.ld"]
    print(Path("dos-mssl.ld").resolve())
else:
    assert args.count("-T") == 1
    script = Path(args[args.index("-T")+1]).read_bytes()
    assert b"FILL(0x9090);" in script
    with Path("calls.jsonl").open("a") as stream:
        stream.write(json.dumps(args) + "\\n")
    Path(args[args.index("-o")+1]).write_bytes(b"recorded link")
''')

    def make(self, *args, ok=True):
        result = subprocess.run(["make", *args, f"CC={sys.executable} {self.fake}",
                                 f"PYTHON={sys.executable} {self.fixture_python}"], cwd=self.root,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_default_make_generates_script_and_links_each_source_once(self):
        self.make()
        generated = (self.root / "build/qrtest-v30.ld").read_bytes()
        self.assertEqual(generated, EXPECTED)
        self.assertEqual(self.source.read_bytes(), FIXTURE)
        self.assertEqual(len(self.calls()), 1)
        sources = [arg for arg in self.calls()[0] if arg.endswith(".c")]
        self.assertEqual(sources, [f"src/{name}.c" for name in SOURCES])
        self.assertEqual(len(sources), len(set(sources)))
        self.assertNotIn("phase4e1i", repr(self.calls()))
        self.assertNotIn("manual_file_probe", repr(self.calls()))

    def test_make_all_and_unchanged_regeneration_do_not_relink(self):
        self.make("all")
        target = self.root / "build/QRTEST.EXE"
        script = self.root / "build/qrtest-v30.ld"
        before = (target.stat().st_mtime_ns, script.stat().st_mtime_ns)
        self.make()
        self.make("all")
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(before, (target.stat().st_mtime_ns, script.stat().st_mtime_ns))

    def test_missing_generated_script_is_recreated_identically(self):
        self.make()
        path = self.root / "build/qrtest-v30.ld"
        before = path.read_bytes()
        path.unlink()
        self.make("all")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.calls()), 2)

    def test_every_manual_header_and_source_triggers_relink(self):
        self.make()
        for name in SOURCES[8:]:
            for suffix in (".c", ".h"):
                with self.subTest(name=name, suffix=suffix):
                    before = len(self.calls())
                    path = self.root / "src" / (name + suffix)
                    result = self.make("-n", "all", "-W", str(path.relative_to(self.root)))
                    self.assertIn("-o build/QRTEST.EXE", result.stdout)
                    # 時計の刻みでEXEと同じmtimeになる場合も、依存元を確実に新しくする。
                    mtime = (self.root / "build/QRTEST.EXE").stat().st_mtime_ns + 1
                    os.utime(path, ns=(path.stat().st_atime_ns, mtime))
                    self.assertGreater(path.stat().st_mtime_ns, mtime - 1)
                    self.make("all")
                    self.assertEqual(len(self.calls()), before + 1)
        # 本番ターゲットに明示された依存ファイルも検査する。
        # Also inspect the explicit prerequisites of the production target.
        database = self.make("-np", "all").stdout
        rule = next(line for line in database.splitlines() if line.startswith("build/QRTEST.EXE:"))
        for name in SOURCES[8:]:
            self.assertIn(f"src/{name}.c", rule.split())
            self.assertIn(f"src/{name}.h", rule.split())

    def test_toolchain_content_change_regenerates_and_relinks(self):
        self.make()
        self.source.write_bytes(FIXTURE + b"/* toolchain revision */\n")
        self.make()
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual((self.root / "build/qrtest-v30.ld").read_bytes(),
                         EXPECTED + b"/* toolchain revision */\n")

    def test_helper_and_makefile_are_script_dependencies(self):
        database = self.make("-np", "all").stdout
        rule = next(line for line in database.splitlines() if line.startswith("build/qrtest-v30.ld:"))
        for name in ("tools/generate_qrtest_linker.py", "Makefile", "FORCE"):
            self.assertIn(name, rule.split())

    def test_bad_toolchain_cannot_reuse_old_generated_script(self):
        self.make()
        script = self.root / "build/qrtest-v30.ld"
        before = script.read_bytes()
        mtime = script.stat().st_mtime_ns
        files = set(script.parent.iterdir())
        self.source.write_bytes(FIXTURE.replace(b"__etext = .;", b"__etext = ALIGN(4);"))
        result = self.make(ok=False)
        self.assertIn("unexpected dos-mssl.ld", result.stderr)
        self.assertEqual(script.read_bytes(), before)
        self.assertEqual(script.stat().st_mtime_ns, mtime)
        self.assertEqual(set(script.parent.iterdir()), files)
        self.assertEqual(len(self.calls()), 1)

    def test_marker_mismatches_are_rejected(self):
        for source in (b"", FIXTURE.replace(MARKER, b""), FIXTURE + MARKER,
                       FIXTURE + b"__etext = .;", FIXTURE.replace(TEXT_START, b""),
                       FIXTURE + TEXT_START, MARKER + TEXT_START,
                       FIXTURE.replace(b"ALIGN(8)", b"ALIGN(4)"),
                       FIXTURE.replace(b". < 4096", b". < 8192"), EXPECTED,
                       FIXTURE.replace(BODY, b"} .other : {\n"),
                       FIXTURE.replace(BODY, b"{\n"), FIXTURE.replace(BODY, b"}\n"),
                       FIXTURE.replace(BODY, b"FILL(0);\n")):
            with self.subTest(source=source[:60]):
                with self.assertRaises(ValueError):
                    generate_fixture(source)

    def test_synthetic_generation_has_independent_expected_bytes(self):
        self.assertEqual(generate_fixture(FIXTURE), EXPECTED)

    def test_each_matching_span_byte_is_checked(self):
        for span in (TEXT_START, MARKER):
            for index in range(len(span)):
                changed = span[:index] + bytes([span[index] ^ 1]) + span[index + 1:]
                with self.subTest(length=len(span), byte=index):
                    with self.assertRaises(ValueError):
                        generate_fixture(FIXTURE.replace(span, changed))

    def test_matching_span_lengths_are_checked(self):
        for span in (TEXT_START, MARKER):
            for changed in (span[:-1], span[:5] + b" " + span[5:]):
                with self.subTest(length=len(span), changed_length=len(changed)):
                    with self.assertRaises(ValueError):
                        generate_fixture(FIXTURE.replace(span, changed))

    def test_fingerprint_search_requires_one_match_including_input_edges(self):
        for span, fingerprint in ((TEXT_START, TEXT_FINGERPRINT), (MARKER, MARKER_FINGERPRINT)):
            with self.subTest(length=len(span)):
                self.assertEqual(linker._find_unique(span, fingerprint), 0)
                self.assertEqual(linker._find_unique(b"prefix" + span, fingerprint), 6)
                self.assertEqual(linker._find_unique(span + b"suffix", fingerprint), 0)
                for source in (b"", span[:-1], span + span, span + b"gap" + span):
                    with self.assertRaises(ValueError):
                        linker._find_unique(source, fingerprint)

    def test_insertion_offset_must_follow_etext_inside_marker(self):
        for offset in (-1, 0, INSERTION_OFFSET - 1, INSERTION_OFFSET + 1, len(MARKER) + 1):
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):
                    linker._generate(FIXTURE, TEXT_FINGERPRINT, MARKER_FINGERPRINT, offset)

    def test_valid_body_and_outside_changes_remain_accepted(self):
        for body in (b"", b"    *(.other_startup)\n"):
            with self.subTest(body=body):
                self.assertEqual(generate_fixture(FIXTURE.replace(BODY, body)),
                                 EXPECTED.replace(BODY, body))
        outside = b"/* outside text body: FILL { } */\n"
        self.assertEqual(generate_fixture(outside + FIXTURE + outside),
                         outside + EXPECTED + outside)

    def test_production_cli_rejects_synthetic_fingerprints(self):
        output = self.root / "out.ld"
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    output.write_bytes(b"previous output")
                result = subprocess.run([sys.executable, str(ROOT / "tools/generate_qrtest_linker.py"),
                                         "--source", str(self.source), "--output", str(output)],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("unexpected dos-mssl.ld", result.stderr)
                if existing:
                    self.assertEqual(output.read_bytes(), b"previous output")
                else:
                    self.assertFalse(output.exists())

    def test_failed_atomic_replace_preserves_output_and_cleans_temporary(self):
        output = self.root / "out.ld"
        output.write_bytes(b"previous output")
        before = set(self.root.iterdir())
        argv = ["generate_qrtest_linker.py", "--source", str(self.source), "--output", str(output)]
        with patch.object(sys, "argv", argv), patch.object(linker, "generate", generate_fixture), \
                patch.object(Path, "replace", side_effect=OSError("replace failed")), \
                patch("sys.stderr") as stderr:
            with self.assertRaises(SystemExit) as raised:
                linker.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("replace failed", "".join(call.args[0] for call in stderr.write.call_args_list))
        self.assertEqual(output.read_bytes(), b"previous output")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_generator_refuses_source_overwrite(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/generate_qrtest_linker.py"),
                                 "--source", str(self.source), "--output", str(self.source)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must not overwrite", result.stderr)
        self.assertEqual(self.source.read_bytes(), FIXTURE)

    def test_missing_compiler_fails_explicitly(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/generate_qrtest_linker.py"),
                                 "--cc", "/nonexistent/ia16-elf-gcc", "--output", str(self.root / "out.ld")],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("QRTEST linker generation failed", result.stderr)
        self.assertFalse((self.root / "out.ld").exists())

    def test_compiler_that_cannot_locate_script_fails_explicitly(self):
        self.fake.write_text('print("dos-mssl.ld")\n')
        result = subprocess.run([sys.executable, str(ROOT / "tools/generate_qrtest_linker.py"),
                                 "--cc", f"{sys.executable} {self.fake}",
                                 "--output", str(self.root / "out.ld")],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("compiler did not locate", result.stderr)
        self.assertFalse((self.root / "out.ld").exists())

    def test_generator_refuses_source_overwrite_through_symlink(self):
        alias = self.root / "alias.ld"
        alias.symlink_to(self.source)
        result = subprocess.run([sys.executable, str(ROOT / "tools/generate_qrtest_linker.py"),
                                 "--source", str(self.source), "--output", str(alias)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must not overwrite", result.stderr)
        self.assertEqual(self.source.read_bytes(), FIXTURE)
        self.assertTrue(alias.is_symlink())

    def test_host_targets_work_without_ia16_or_generated_script(self):
        (self.root / "tests").mkdir()
        for name in ("encode_main.c", "encode_v2_main.c", "render_main.c"):
            shutil.copy(ROOT / "tests" / name, self.root / "tests")
        result = subprocess.run(["make", "build/test_qr_encode", "build/test_qr_v2",
                                 "build/test_main_render", "CC=/nonexistent/ia16"],
                                cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "build/qrtest-v30.ld").exists())
        planned = subprocess.run(["make", "-n", "test", "CC=/nonexistent/ia16"],
                                 cwd=self.root, text=True, capture_output=True)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertIn("unittest discover -s tests -v", planned.stdout)
        self.assertNotIn("generate_qrtest_linker", planned.stdout)
