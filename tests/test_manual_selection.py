# 純粋な選択状態、BIOSアダプター契約、実テキストprobeの統合試験。
"""Pure selection, BIOS-adapter contract, and real text-probe integration."""
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTER, BACKSPACE, ESCAPE = 256, 257, 258
UNCHANGED, EDITED, REJECTED, SELECTED, EXIT, ARGUMENT = range(6)
NONE, RANGE, OVERFLOW, TOO_LONG = range(4)


@lru_cache(maxsize=2)
def driver(probe=False):
    name = "manual_selection_probe" if probe else "manual_selection"
    override = os.environ.get("PC98QR_" + name.upper() + "_DRIVER")
    if override:
        return Path(override)
    target = ROOT / "build" / ("test_" + name)
    target.parent.mkdir(exist_ok=True)
    subprocess.run([*shlex.split(os.environ.get("HOST_CC", "cc")), "-std=c99",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
                    "-o", str(target), "tests/" + name + "_main.c",
                    "src/manual_selection.c"], check=True, cwd=ROOT)
    return target


def call(*args):
    result = subprocess.run([str(driver()), *map(str, args)], cwd=ROOT,
                            capture_output=True, text=True, timeout=10)
    if result.returncode or result.stderr:
        raise AssertionError(f"driver exit {result.returncode}: {result.stderr}")
    return json.loads(result.stdout)


def keys(text):
    return list(map(ord, text))


def trace(count, *events):
    return call("state", count, *events)


def selections(rows):
    return [(r["selected"], r["index"]) for r in rows if r["result"] == SELECTED]


def reference(count, events):
    # Cの逐次加算とは独立した任意精度の十進変換を使う。
    # Arbitrary-precision decimal conversion independent of the C accumulator.
    digits, error, selected, exited = "", NONE, 0, 0
    rows = []
    def record(result):
        rows.append(dict(result=result, digits=digits, length=len(digits),
                         count=count, selected=selected, index=selected-1,
                         error=error, exited=exited))
    record(UNCHANGED)
    for event in events:
        result = UNCHANGED
        if not exited:
            if event == ESCAPE:
                exited, result = 1, EXIT
            elif event == BACKSPACE and digits:
                digits, error, result = digits[:-1], NONE, EDITED
            elif 48 <= event <= 57 and error != TOO_LONG:
                if len(digits) == 5:
                    error, result = TOO_LONG, REJECTED
                else:
                    digits, error, result = digits + chr(event), NONE, EDITED
            elif event == ENTER and digits:
                result = REJECTED
                if error != TOO_LONG:
                    value = int(digits)
                    if value > 65535:
                        error = OVERFLOW
                    elif not 1 <= value <= count:
                        error = RANGE
                    else:
                        selected, digits, error, result = value, "", NONE, SELECTED
        record(result)
    return rows


class ManualSelectionTests(unittest.TestCase):
    def assert_selected(self, count, text, number):
        rows = trace(count, *keys(text), ENTER)
        self.assertEqual(selections(rows), [(number, number-1)])
        self.assertEqual(rows[-1]["digits"], "")
        self.assertEqual(rows[-1]["length"], 0)
        self.assertEqual(rows[-1]["error"], NONE)

    def test_1(self):
        self.assert_selected(1, "1", 1)

    def test_2(self):
        self.assert_selected(24, "2", 2)

    def test_23(self):
        self.assert_selected(24, "23", 23)

    def test_24(self):
        self.assert_selected(24, "24", 24)

    def test_501(self):
        self.assert_selected(501, "501", 501)

    def test_65535(self):
        self.assert_selected(65535, "65535", 65535)

    def test_leading_zero(self):
        self.assert_selected(24, "00001", 1)

    def test_signed_16bit_boundary(self):
        for value in (32767, 32768, 65534):
            with self.subTest(value=value):
                self.assert_selected(65535, str(value), value)

    def test_initial_state(self):
        self.assertEqual(trace(24), reference(24, []))

    def test_invalid_init_and_null(self):
        self.assertEqual(call("arguments"), {"arguments_checked": True})

    def test_reinit_after_exit(self):
        rows = call("reinit", 24, ord('2'), ENTER, ESCAPE)
        self.assertEqual(rows[-1], reference(3, [])[0])

    def test_range_rejections_preserve_input_and_selection(self):
        for count, text in ((24, "0"), (24, "00000"), (3, "4"),
                            (24, "25"), (501, "502")):
            with self.subTest(count=count, text=text):
                rows = trace(count, ord('1'), ENTER, *keys(text), ENTER)
                self.assertEqual(selections(rows), [(1, 0)])
                self.assertEqual(rows[-1]["digits"], text)
                self.assertEqual(rows[-1]["error"], RANGE)
                self.assertEqual(rows[-1]["selected"], 1)
                self.assertEqual(rows[-1]["result"], REJECTED)

    def test_overflow_keeps_all_digits(self):
        for text in ("65536", "65539", "65540", "99999"):
            with self.subTest(text=text):
                rows = trace(65535, ord('2'), ENTER, *keys(text), ENTER)
                self.assertEqual(rows[-1]["error"], OVERFLOW)
                self.assertEqual(rows[-1]["digits"], text)
                self.assertEqual(selections(rows), [(2, 1)])

    def test_overflow_backspace_correction(self):
        events = [*keys("65536"), ENTER, BACKSPACE, ord('5'), ENTER]
        rows = trace(65535, *events)
        self.assertEqual(rows, reference(65535, events))
        self.assertEqual(selections(rows), [(65535, 65534)])

    def test_sixth_digit_never_confirms_prefix(self):
        rows = trace(65535, *keys("123456"), ENTER, ENTER)
        self.assertEqual(rows[-1]["digits"], "12345")
        self.assertEqual(rows[-1]["error"], TOO_LONG)
        self.assertEqual(selections(rows), [])

    def test_excess_digits_remain_latched(self):
        rows = trace(65535, *keys("000001234567890"), 0, ENTER)
        self.assertEqual(rows[-1]["digits"], "00000")
        self.assertEqual(rows[-1]["error"], TOO_LONG)
        self.assertEqual(selections(rows), [])

    def test_too_long_backspace_also_removes_saved_digit(self):
        events = [*keys("123456"), ENTER, BACKSPACE, ENTER]
        rows = trace(65535, *events)
        self.assertEqual(rows[-2]["digits"], "1234")
        self.assertEqual(rows[-2]["error"], NONE)
        self.assertEqual(selections(rows), [(1234, 1233)])

    def test_too_long_does_not_change_previous_selection(self):
        rows = trace(65535, ord('2'), ENTER, *keys("655350"), ENTER)
        self.assertEqual(selections(rows), [(2, 1)])
        self.assertEqual(rows[-1]["selected"], 2)

    def test_empty_enter_repeats(self):
        rows = trace(24, ENTER, ENTER, ENTER)
        self.assertTrue(all(r["result"] == UNCHANGED for r in rows))

    def test_enter_repeats_after_commit_do_not_recommit(self):
        rows = trace(24, *keys("23"), ENTER, ENTER, ENTER)
        self.assertEqual(selections(rows), [(23, 22)])
        self.assertEqual(rows[-1]["result"], UNCHANGED)

    def test_repeated_digits_are_not_deduplicated(self):
        self.assert_selected(24, "22", 22)
        self.assert_selected(24, "11", 11)

    def test_enter_repeat_after_new_digit_can_commit(self):
        # 既知の制限を表すモデルであり、物理キーの安全性保証ではない。
        # This models the known limitation, not a physical-key safety guarantee.
        rows = trace(24, ENTER, ENTER, ord('2'), ENTER, ENTER, ord('3'), ENTER)
        self.assertEqual(selections(rows), [(2, 1), (3, 2)])

    def test_backspace_23_to_24(self):
        rows = trace(24, *keys("23"), BACKSPACE, ord('4'), ENTER)
        self.assertEqual(selections(rows), [(24, 23)])

    def test_backspace_empty_and_repeat(self):
        events = [BACKSPACE, ord('2'), BACKSPACE, BACKSPACE, BACKSPACE, ENTER]
        self.assertEqual(trace(24, *events), reference(24, events))

    def test_range_error_correction(self):
        rows = trace(24, *keys("25"), ENTER, BACKSPACE, ord('4'), ENTER)
        self.assertEqual(selections(rows), [(24, 23)])

    def test_append_after_zero_error(self):
        rows = trace(24, ord('0'), ENTER, ord('1'), ENTER)
        self.assertEqual(selections(rows), [(1, 0)])

    def test_escape_from_all_states_is_terminal(self):
        for prefix in ([], keys("23"), [*keys("25"), ENTER],
                       [*keys("123456"), ENTER], [ord('1'), ENTER]):
            with self.subTest(prefix=prefix):
                rows = trace(24, *prefix, ESCAPE, *keys("24"), ENTER, BACKSPACE, ESCAPE)
                at_exit = rows[len(prefix)+1]
                self.assertEqual(at_exit["result"], EXIT)
                for row in rows[len(prefix)+2:]:
                    self.assertEqual(row, dict(at_exit, result=UNCHANGED))

    def test_unrelated_events_preserve_input_and_error(self):
        events = [*keys("25"), ENTER, ord('A'), 0, 0xFFFF, 8, 13, 27]
        rows = trace(24, *events)
        self.assertEqual(rows, reference(24, events))
        self.assertEqual(rows[-1]["digits"], "25")

    def test_successive_selections(self):
        rows = trace(24, ord('1'), ENTER, *keys("24"), ENTER, *keys("23"), ENTER)
        self.assertEqual(selections(rows), [(1, 0), (24, 23), (23, 22)])

    def test_decimal_boundary_oracle(self):
        values = list(range(65520, 65550)) + [0, 1, 3, 4, 24, 25, 501, 502, 9999, 10000, 99999]
        for value in values:
            for count in (24, 65535):
                events = [*keys(str(value)), ENTER]
                self.assertEqual(trace(count, *events), reference(count, events))

    def test_deterministic_event_sequences(self):
        rng = random.Random(0x4E1A)
        for count in (1, 3, 24, 501, 65535):
            events = [rng.choice(keys("0123456789") + [ENTER]*3 + [BACKSPACE]*2 + [0, 65535])
                      for _ in range(500)]
            events += [ESCAPE, ord('1'), ENTER]
            self.assertEqual(trace(count, *events), reference(count, events))


class PC98KeyInputTests(unittest.TestCase):
    def test_exact_control_keys(self):
        self.assertEqual(call("classify", 0x1C0D, 0x0E08, 0x001B), [ENTER, BACKSPACE, ESCAPE])

    def test_control_character_aliases_are_not_control_keys(self):
        self.assertEqual(call("classify", 0x2D0D, 0x2308, 0x1B1B, 0x3B08, 0x000D), [0]*5)

    def test_number_row(self):
        values = [0x0A30] + [(n << 8) | (48+n) for n in range(1, 10)]
        self.assertEqual(call("classify", *values), keys("0123456789"))

    def test_keypad(self):
        scans = [0x4E, 0x4A, 0x4B, 0x4C, 0x46, 0x47, 0x48, 0x42, 0x43, 0x44]
        self.assertEqual(call("classify", *[(s << 8) | (48+n) for n, s in enumerate(scans)]), keys("0123456789"))

    def test_non_ascii_digits_and_other_keys(self):
        self.assertEqual(call("classify", 0x01C7, 0x1E73, 0x0F09, 0x6200, 0x4F2C, 0x502E, 0x023F), [0]*7)

    def test_empty_poll_never_reads_stale_ax(self):
        self.assertEqual(call("poll", 0, 0x1C0D, 0x0232),
                         dict(result=0, ax=0xA55A, peeks=1, reads=0))

    def test_valid_poll_reads_exactly_once(self):
        self.assertEqual(call("poll", 1, 0x0232, 0x0232),
                         dict(result=1, ax=0x0232, peeks=1, reads=1))

    def test_consumed_ax_wins_over_peek(self):
        self.assertEqual(call("poll", 1, 0x1C0D, 0x0232),
                         dict(result=1, ax=0x0232, peeks=1, reads=1))

    def test_unexpected_bh_never_reads(self):
        for bh in (2, 127, 255):
            self.assertEqual(call("poll", bh, 0x1C0D, 0x0232),
                             dict(result=3, ax=0xA55A, peeks=1, reads=0))

    def test_null_output_never_calls_bios(self):
        self.assertEqual(call("poll", 1, 0x1C0D, 0x0232, "null"),
                         dict(result=2, ax=0xA55A, peeks=0, reads=0))


class ManualSelectionProbeTests(unittest.TestCase):
    def run_probe(self, args=("24",), script="001b", reads=1, **settings):
        env = os.environ.copy()
        for name in list(env):
            if name.startswith("PC98QR_NUMPROBE_"):
                del env[name]
        env.update(PC98QR_NUMPROBE_KEYS=script, PC98QR_NUMPROBE_READS=str(reads))
        env.update({"PC98QR_NUMPROBE_"+k: str(v) for k, v in settings.items()})
        return subprocess.run([str(driver(True)), *args], capture_output=True,
                              cwd=ROOT, env=env, timeout=10)

    def test_valid_counts_and_normal_escape_exit(self):
        for count in ("1", "3", "24", "501", "65535", "00001"):
            with self.subTest(count=count):
                r = self.run_probe((count,))
                self.assertEqual((r.returncode, r.stderr), (0, b""))
                self.assertIn(f"Frame range: 1 - {int(count)}\r\n".encode(), r.stdout)
                self.assertTrue(r.stdout.endswith(b"\r\nDone.\r\n"))

    def test_invalid_cli_does_not_poll(self):
        for args in ((), ("",), ("0",), ("00000",), ("65536",), ("99999",),
                     ("123456",), ("000001",), ("+1",), ("-1",), ("1x",),
                     (" 1",), ("1 ",), ("0x10",), ("24", "extra")):
            with self.subTest(args=args):
                r = self.run_probe(args, reads=0)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(r.stdout, b"")
                self.assertIn(b"Usage: NUMPROBE COUNT", r.stderr)

    def test_basic_and_multiple_selections(self):
        r = self.run_probe(script="0232 0333 1c0d 0232 1c0d 0232 0434 1c0d 001b", reads=9)
        self.assertEqual((r.returncode, r.stderr), (0, b""))
        self.assertIn(b"\rInput: 23", r.stdout)
        self.assertEqual(r.stdout.count(b"Selected:"), 3)
        for n, idx in ((23, 22), (2, 1), (24, 23)):
            self.assertIn(f"Selected: {n}\r\nInternal frame index: {idx}\r\n\rInput:      ".encode(), r.stdout)

    def test_backspace_erases_old_digit(self):
        r = self.run_probe(script="0232 0333 0e08 0434 1c0d 001b", reads=6)
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"\rInput: 23   \rInput: 2    \rInput: 24   ", r.stdout)
        self.assertIn(b"Selected: 24\r\nInternal frame index: 23", r.stdout)

    def test_range_error_is_reported_once_and_editable(self):
        r = self.run_probe(script="0232 0535 1c0d 1c0d 0e08 0434 1c0d 001b", reads=8)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.count(b"Error: out of range."), 1)
        self.assertIn(b"Selected: 24", r.stdout)

    def test_overflow_then_correction(self):
        r = self.run_probe(("65535",), script="0636 0535 0535 0333 0636 1c0d 0e08 0535 1c0d 001b", reads=10)
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"Error: number exceeds 65535.", r.stdout)
        self.assertIn(b"Selected: 65535\r\nInternal frame index: 65534", r.stdout)

    def test_excess_repeat_reports_once_and_cannot_select(self):
        r = self.run_probe(script=" ".join(["0232"]*20 + ["1c0d"]*20 + ["001b"]), reads=41)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.count(b"Error: too many digits"), 1)
        self.assertNotIn(b"Selected:", r.stdout)

    def test_empty_polls_and_enter_repeats_do_not_flood_output(self):
        baseline = self.run_probe()
        r = self.run_probe(script=" ".join(["-", "1c0d"]*40 + ["001b"]), reads=41)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, baseline.stdout)

    def test_queued_digits_are_processed_without_flush(self):
        r = self.run_probe(script="0232 0232 1c0d 001b", reads=4)
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"Selected: 22\r\nInternal frame index: 21", r.stdout)

    def test_enter_repeat_can_confirm_new_digit(self):
        r = self.run_probe(script="1c0d 1c0d 0232 1c0d 1c0d 001b", reads=6)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.count(b"Selected:"), 1)
        self.assertIn(b"Selected: 2\r\nInternal frame index: 1", r.stdout)

    def test_keypad_501(self):
        r = self.run_probe(("501",), script="4735 4e30 4a31 1c0d 001b", reads=5)
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"Selected: 501\r\nInternal frame index: 500", r.stdout)

    def test_escape_leaves_later_bios_events_unconsumed(self):
        r = self.run_probe(script="001b 0232 1c0d", reads=1)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn(b"Selected:", r.stdout)

    def test_invalid_bh_exits_without_read(self):
        r = self.run_probe(script="! 001b", reads=0)
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"unexpected BIOS keyboard status", r.stderr)

    def test_short_writes_preserve_complete_output(self):
        normal = self.run_probe(script="0232 1c0d 001b", reads=3)
        short = self.run_probe(script="0232 1c0d 001b", reads=3, WRITE_LIMIT=2)
        self.assertEqual((short.returncode, short.stdout, short.stderr),
                         (normal.returncode, normal.stdout, normal.stderr))

    def test_output_failure_stops_before_poll(self):
        for extra in ({}, {"ZERO_WRITE": 1}):
            r = self.run_probe(reads=0, WRITE_BYTES=5, **extra)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout, b"PC98Q")

    def test_usage_output_failure_is_io_exit(self):
        r = self.run_probe((), reads=0, WRITE_BYTES=0)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
