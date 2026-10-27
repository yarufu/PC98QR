# 実MSTARTと実ヘルパーにBIOS/I/O故障を注入する試験。
"""MSTART production main with real helpers and injected BIOS / I/O faults."""
import ctypes
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "PC98QR_MSTART_"
START = b"\r\nStart requested.\r\nQR display is not implemented in this probe.\r\n"
CANCEL = b"\r\nCancelled.\r\n"
USAGE = b"Usage: MSTART PATH\r\n"
FILE_ERROR = b"Error: file size acquisition failed.\r\n"
OPEN_ERROR = b"Error: could not open file.\r\n"
COUNT_ERROR = b"Error: transport frame count is not supported.\r\n"
KEY_ERROR = b"Error: unexpected BIOS keyboard status.\r\n"


@lru_cache(maxsize=1)
def driver():
    override = os.environ.get("PC98QR_MANUAL_START_PROBE_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build/phase4e1b/test_manual_start_probe"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "tests/manual_start_probe_main.c", "src/file_size.c",
                    "src/frame_count.c", "-o", str(target)], check=True, cwd=ROOT)
    return target


def screen(path, size, count):
    return (f"PC98QR - Manual Transfer\r\n\r\nFile       : {path}\r\n"
            f"File size  : {size} bytes\r\nQR frames  : {count}\r\n"
            f"Frame range: 1 - {count}\r\n\r\nS   : Start\r\nEsc : Cancel\r\n").encode()


class ManualStartProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "T6000.BIN"
        # NUL、0x1A、上位ビットの立った全バイトも通常のファイル内容として扱う。
        # NUL, 0x1A and all high bytes must be ordinary file contents.
        self.path.write_bytes((bytes(range(256)) * 24)[:6000])

    def run_probe(self, args=None, keys="1e53", reads=1, polls=None,
                  file_calls=1, count_calls=1, **settings):
        env = {k: v for k, v in os.environ.items() if not k.startswith(PREFIX)}
        env.update({PREFIX+k: str(v) for k, v in settings.items()})
        env.update({PREFIX+"KEYS": keys, PREFIX+"READS": str(reads),
                    PREFIX+"POLLS": str(reads if polls is None else polls),
                    PREFIX+"FILE_CALLS": str(file_calls),
                    PREFIX+"COUNT_CALLS": str(count_calls)})
        return subprocess.run([str(driver()), *map(str, [self.path] if args is None else args)],
                              cwd=ROOT, env=env, capture_output=True, timeout=10)

    def check(self, result, code, stdout=b"", stderr=b""):
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (code, stdout, stderr))

    def check_size(self, size, count):
        with self.path.open("wb") as stream:
            stream.truncate(size)
        self.check(self.run_probe(), 0, screen(self.path, size, count) + START)
        self.assertEqual(self.path.stat().st_size, size)

    def test_one_byte(self):
        self.check_size(1, 1)

    def test_full_payload(self):
        self.check_size(2944, 1)

    def test_payload_plus_one(self):
        self.check_size(2945, 2)

    def test_6000_bytes(self):
        self.check_size(6000, 3)

    def test_70000_bytes(self):
        self.check_size(70000, 24)

    def test_1474560_bytes(self):
        self.check_size(1474560, 501)

    def test_candidate_size_is_not_a_limit(self):
        self.check_size(1474561, 501)

    def test_maximum_wire_count(self):
        self.check_size(192935040, 65535)

    def test_wire_count_overflow(self):
        with self.path.open("wb") as stream:
            stream.truncate(192935041)
        self.check(self.run_probe(keys="", reads=0), 4, stderr=COUNT_ERROR)

    def test_unsigned_32bit_size_rejected_without_truncation(self):
        self.check(self.run_probe(keys="", reads=0, SIZE=4294967295),
                   4, stderr=COUNT_ERROR)

    def test_decimal_unsigned_long_boundaries(self):
        values = [0, 1, 9, 10, 100, 65535, 65536, 70000, 1474560, 4294967295, "max"]
        for value in values:
            with self.subTest(value=value):
                expected = (1 << (8 * ctypes.sizeof(ctypes.c_ulong))) - 1 if value == "max" else value
                self.check(self.run_probe(args=[], keys="", reads=0,
                                          file_calls=0, count_calls=0, DECIMAL=value),
                           0, str(expected).encode())

    def test_exact_dos_path_and_startup_labels(self):
        path = r"B:\T70000.BIN"
        self.check(self.run_probe([path], SIZE=70000), 0, screen(path, 70000, 24) + START)

    def test_long_path_is_not_truncated(self):
        # 表示には固定パスバッファがない。
        # ホストではDOSコマンド末尾より長い入力も試す。
        # Display has no fixed path buffer; host exercises more than a DOS tail.
        parent = Path(self.temp.name) / ("a" * 110) / ("b" * 110)
        parent.mkdir(parents=True)
        path = parent / "Mixed Case File.BIN"
        path.write_bytes(b"\x1a")
        self.check(self.run_probe([path]), 0, screen(path, 1, 1) + START)

    def test_binary_contents_unchanged_on_start_and_cancel(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        for key, code, tail in (("1e53", 0, START), ("001b", 1, CANCEL)):
            self.check(self.run_probe(keys=key), code, screen(self.path, 6000, 3) + tail)
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_uppercase_s(self):
        self.check(self.run_probe(), 0, screen(self.path, 6000, 3) + START)

    def test_lowercase_s(self):
        self.check(self.run_probe(keys="1e73"), 0, screen(self.path, 6000, 3) + START)

    def test_escape(self):
        self.check(self.run_probe(keys="001b"), 1, screen(self.path, 6000, 3) + CANCEL)

    def test_enter_is_ignored(self):
        self.check(self.run_probe(keys="1c0d 1e53", reads=2),
                   0, screen(self.path, 6000, 3) + START)

    def test_three_enters_then_start_once(self):
        self.check(self.run_probe(keys="1c0d 1c0d 1c0d 1e53 1e53", reads=4),
                   0, screen(self.path, 6000, 3) + START)

    def test_two_enters_then_cancel(self):
        self.check(self.run_probe(keys="1c0d 1c0d 001b 1e53", reads=3),
                   1, screen(self.path, 6000, 3) + CANCEL)

    def test_enter_repeat_with_empty_polls(self):
        for ending, code, tail in (("1e53", 0, START), ("001b", 1, CANCEL)):
            self.check(self.run_probe(keys="- 1c0d " * 200 + ending,
                                      reads=201, polls=401),
                       code, screen(self.path, 6000, 3) + tail)

    def test_digits_are_ignored(self):
        self.check(self.run_probe(keys="0232 0333 4735 4e30 4a31 1e53", reads=6),
                   0, screen(self.path, 6000, 3) + START)

    def test_backspace_is_ignored(self):
        self.check(self.run_probe(keys="0e08 0e08 1e53", reads=3),
                   0, screen(self.path, 6000, 3) + START)

    def test_other_keys_and_control_aliases_are_ignored(self):
        # Ctrl+S、かなS、矢印、Tab、Ctrl+[、ASCIIのCRだけでは操作を起動しない。
        # Ctrl+S, kana S, arrows, tab, Ctrl+[ and ASCII CR are not actions.
        self.check(self.run_probe(keys="1e13 1ec4 6200 0f09 1b1b 000d 1e73", reads=7),
                   0, screen(self.path, 6000, 3) + START)

    def test_only_first_start_event_is_consumed(self):
        self.check(self.run_probe(keys="1e53 1e73 001b"),
                   0, screen(self.path, 6000, 3) + START)

    def test_only_first_cancel_event_is_consumed(self):
        self.check(self.run_probe(keys="001b 001b 1e53"),
                   1, screen(self.path, 6000, 3) + CANCEL)

    def test_empty_poll_does_not_use_stale_s(self):
        self.check(self.run_probe(keys="- - 001b", polls=3),
                   1, screen(self.path, 6000, 3) + CANCEL)

    def test_missing_argument(self):
        self.check(self.run_probe([], keys="", reads=0, file_calls=0, count_calls=0),
                   2, stderr=USAGE)

    def test_extra_argument(self):
        self.check(self.run_probe([self.path, "EXTRA"], keys="", reads=0,
                                  file_calls=0, count_calls=0), 2, stderr=USAGE)

    def test_empty_path(self):
        self.check(self.run_probe([""], keys="", reads=0, file_calls=0, count_calls=0),
                   2, stderr=USAGE)

    def test_nonexistent_file(self):
        self.check(self.run_probe([self.path.with_name("MISSING.BIN")], keys="",
                                  reads=0, count_calls=0), 3, stderr=OPEN_ERROR)

    def test_empty_file(self):
        self.path.write_bytes(b"")
        self.check(self.run_probe(keys="", reads=0), 4, stderr=b"Error: file is empty.\r\n")

    def test_file_helper_failures(self):
        for error in (1, 2, 3, 4):
            with self.subTest(error=error):
                self.check(self.run_probe(keys="", reads=0, count_calls=0, FILE_ERROR=error),
                           3, stderr=OPEN_ERROR if error == 2 else FILE_ERROR)

    def test_frame_count_helper_failures(self):
        for error in (1, 2):
            with self.subTest(error=error):
                self.check(self.run_probe(keys="", reads=0, COUNT_ERROR=error),
                           4, stderr=COUNT_ERROR)

    def test_bios_error_does_not_consume_or_start(self):
        self.check(self.run_probe(keys="! 1e53", reads=0, polls=1),
                   5, screen(self.path, 6000, 3), KEY_ERROR)

    def test_short_writes_preserve_output(self):
        for limit in (1, 2, 7):
            for key, code, tail in (("1e53", 0, START), ("001b", 1, CANCEL)):
                with self.subTest(limit=limit, key=key):
                    self.check(self.run_probe(keys=key, WRITE_LIMIT=limit),
                               code, screen(self.path, 6000, 3) + tail)

    def test_startup_failure_at_every_output_byte_never_polls(self):
        expected = screen(self.path, 6000, 3)
        for zero in (False, True):
            for budget in range(len(expected)):
                with self.subTest(zero=zero, budget=budget):
                    options = {"WRITE_ZERO": 1} if zero else {}
                    self.check(self.run_probe(keys="", reads=0, WRITE_BUDGET=budget, **options),
                               6, expected[:budget])

    def test_action_output_failure_never_polls_again(self):
        base = screen(self.path, 6000, 3)
        for key, tail in (("1e53", START), ("001b", CANCEL)):
            for zero in (False, True):
                for budget in range(len(tail)):
                    with self.subTest(key=key, zero=zero, budget=budget):
                        options = {"WRITE_ZERO": 1} if zero else {}
                        self.check(self.run_probe(keys=key, WRITE_BUDGET=len(base)+budget, **options),
                                   6, base + tail[:budget])

    def test_error_messages_support_short_writes(self):
        self.check(self.run_probe([], keys="", reads=0, file_calls=0, count_calls=0,
                                  WRITE_LIMIT=1), 2, stderr=USAGE)
        self.check(self.run_probe(keys="", reads=0, count_calls=0, FILE_ERROR=3,
                                  WRITE_LIMIT=1), 3, stderr=FILE_ERROR)

    def test_error_reporting_failure_is_output_exit(self):
        cases = [([], 0, 0, {}, USAGE), ([self.path], 1, 0, {"FILE_ERROR": 3}, FILE_ERROR),
                 ([self.path], 1, 1, {"COUNT_ERROR": 2}, COUNT_ERROR)]
        for args, fc, cc, injected, message in cases:
            for zero in (False, True):
                for budget in (0, len(message)-1):
                    with self.subTest(injected=injected, zero=zero, budget=budget):
                        options = {"WRITE_ZERO": 1} if zero else {}
                        self.check(self.run_probe(args, keys="", reads=0, file_calls=fc,
                                                  count_calls=cc, WRITE_BUDGET=budget,
                                                  **injected, **options),
                                   6, stderr=message[:budget])

    def test_bios_error_reporting_failure_is_output_exit(self):
        base = screen(self.path, 6000, 3)
        for zero in (False, True):
            options = {"WRITE_ZERO": 1} if zero else {}
            self.check(self.run_probe(keys="!", reads=0, polls=1,
                                      WRITE_BUDGET=len(base)+3, **options),
                       6, base, KEY_ERROR[:3])


if __name__ == "__main__":
    unittest.main()
