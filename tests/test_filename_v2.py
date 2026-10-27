# 実Cヘルパーによる正規化ファイル名とDOSパスの字句規則の検証。
"""Canonical filename and DOS lexical path contracts through real C helpers."""
import unittest

from test_frame_v2 import call, constants

OK, ARGUMENT, PATH, NAME, RESERVED, CAPACITY = range(6)
SPECIAL = b"!#$%&'()-@^_`{}~"
ALLOWED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" + SPECIAL)


def extract(path, capacity=12, mask=0):
    if isinstance(path, str):
        path = path.encode("ascii")
    return call("path", path.hex() or "-", capacity, mask)


def validate(name, length=None, null=0):
    if isinstance(name, str):
        name = name.encode("ascii")
    return call("validate", name.hex() or "-", len(name) if length is None else length, null)["result"]


class FilenameV2Tests(unittest.TestCase):
    def assert_name(self, path, expected):
        data = extract(path)
        self.assertEqual(data, dict(result=OK, length=len(expected), hex=expected.encode().hex()))

    def test_required_examples(self):
        for name in ("TEST.MAG", "GAME~1.MAG", "DATA_01.BIN", "A&B.DAT",
                     "TEST$1.MAG", "README"):
            with self.subTest(name=name):
                self.assert_name(name, name)

    def test_drive_rooted(self):
        self.assert_name(r"B:\TEST.MAG", "TEST.MAG")

    def test_drive_relative(self):
        self.assert_name("B:TEST.MAG", "TEST.MAG")

    def test_relative_and_rooted_without_drive(self):
        for path in (r"DIR\TEST.MAG", r"\DIR\TEST.MAG", r"\TEST.MAG"):
            self.assert_name(path, "TEST.MAG")

    def test_nested_directories(self):
        self.assert_name(r"B:\DIR\SUB.DIR\TEST.MAG", "TEST.MAG")

    def test_every_drive_letter(self):
        for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
            for middle in (":", ":\\"):
                self.assert_name(c + middle + "a.bin", "A.BIN")

    def test_lowercase_normalization_without_input_writes(self):
        for path in (r"b:\test.mag", r"b:dir\TeSt.MaG", "abcdefgh.xyz"):
            expected = "ABCDEFGH.XYZ" if path == "abcdefgh.xyz" else "TEST.MAG"
            self.assert_name(path, expected)

    def test_all_ascii_lowercase_letters(self):
        for c in "abcdefghijklmnopqrstuvwxyz":
            self.assert_name(c + "." + c, c.upper() + "." + c.upper())

    def test_directory_navigation_only(self):
        for path in (r".\TEST.MAG", r"..\TEST.MAG", r"B:..\TEST.MAG",
                     r"B:\DIR\..\.\TEST.MAG"):
            self.assert_name(path, "TEST.MAG")

    def test_all_sixteen_special_characters(self):
        self.assertEqual(len(SPECIAL), 16)
        for c in SPECIAL:
            name = bytes([c]) + b"." + bytes([c])
            self.assertEqual(validate(name), OK)
            self.assert_name(b"B:\\" + name, name.decode())

    def test_all_uppercase_and_digits(self):
        for c in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            self.assertEqual(validate(bytes([c])), OK)

    def test_allowed_characters_in_directories(self):
        for c in SPECIAL:
            self.assert_name(b"B:\\A" + bytes([c]) + b"\\test.mag", "TEST.MAG")

    def test_all_256_values_in_filename(self):
        for c in range(256):
            with self.subTest(byte=c):
                expected = OK if c in ALLOWED or c == ord('.') else NAME
                self.assertEqual(validate(b"A" + bytes([c]) + b"B"), expected)

    def test_validator_requires_canonical_uppercase(self):
        for name in ("test.mag", "TEST.mag", "TeSt.MAG"):
            self.assertEqual(validate(name), NAME)

    def test_reserved_bases_with_and_without_extension(self):
        names = ["CON", "CONIN$", "CONOUT$", "PRN", "AUX", "NUL", "CLOCK$", "COM", "LPT", "LST"]
        names += [prefix + str(i) for prefix in ("COM", "LPT") for i in range(10)]
        for name in names:
            for ext in ("", ".MAG"):
                with self.subTest(name=name+ext):
                    self.assertEqual(validate(name+ext), RESERVED)
                    self.assertEqual(extract((name+ext).lower())["result"], RESERVED)

    def test_reserved_prefix_is_not_reserved_name(self):
        for name in ("CONSOLE.TXT", "COMPRESS.DAT", "COM10.BIN", "LPT10", "NUL1", "AUXILIAR",
                     "CONIN$1.TXT", "CONOUT$1.TXT"):
            self.assert_name(name, name)

    def test_reserved_directory_rejected(self):
        for path in (r"B:\con\OK.BIN", r"LPT0.TXT\OK.BIN", r"CLOCK$\OK"):
            self.assertEqual(extract(path)["result"], PATH)

    def test_all_valid_83_lengths(self):
        for base in range(1, 9):
            for ext in range(4):
                name = "A" * base + ("." + "B" * ext if ext else "")
                self.assert_name(name, name)

    def test_invalid_83_structure(self):
        for name in ("", ".", "..", ".A", "A.", "A..B", "A.B.C", "ABCDEFGHI",
                     "ABCDEFGHI.A", "A.ABCD", "ABCDEFGH.ABCD", "...", "A..."):
            self.assertEqual(validate(name), NAME)
            self.assertNotEqual(extract(name)["result"], OK)

    def test_whitespace_controls_del(self):
        for c in (*range(1, 33), 127):
            self.assertNotEqual(extract(b"A" + bytes([c]) + b".BIN")["result"], OK)
            self.assertNotEqual(extract(b"DIR" + bytes([c]) + b"\\A.BIN")["result"], OK)

    def test_nonascii_anywhere_in_path(self):
        for c in range(128, 256):
            self.assertNotEqual(extract(b"B:\\A" + bytes([c]) + b"\\OK.BIN")["result"], OK)
            self.assertNotEqual(extract(b"B:\\OK\\A" + bytes([c]))["result"], OK)

    def test_cp932_trail_backslash_is_rejected(self):
        for path in (b"B:\\\x83\\\\OK.BIN", b"B:\\OK\\\x83\\.BIN"):
            self.assertNotEqual(extract(path)["result"], OK)

    def test_forbidden_punctuation_and_wildcards(self):
        for c in b'/*?"<>|+,;=[]:':
            # コロンの前を2文字にして、合法なA:Bを誤って試験しないようにする。
            # Two letters before ':' prevent accidentally testing legal A:B.
            self.assertNotEqual(extract(b"AA" + bytes([c]) + b"B.MAG")["result"], OK)
            self.assertNotEqual(extract(b"DD" + bytes([c]) + b"R\\A.MAG")["result"], OK)

    def test_colon_placement(self):
        for path in (":A", "1:A", "AB:A", "B::A", r"B:\C:A", r"DIR\C:A", r"\B:A"):
            self.assertNotEqual(extract(path)["result"], OK)

    def test_empty_root_or_drive_only(self):
        for path in ("", "B:", "B:\\", "\\"):
            self.assertEqual(extract(path)["result"], PATH)

    def test_trailing_repeated_separator_and_unc(self):
        for path in ("A\\", "B:\\DIR\\", r"DIR\\A.BIN", r"\\SERVER\SHARE\A.BIN"):
            self.assertEqual(extract(path)["result"], PATH)

    def test_invalid_directory_components(self):
        for component in ("ABCDEFGHI", "DIR.ABCD", ".DIR", "DIR.", "...", "D..R"):
            self.assertEqual(extract(component + "\\A.BIN")["result"], PATH)

    def test_no_truncation_of_long_components(self):
        for path in ("A"*60000, "A"*60000 + "\\OK.BIN"):
            self.assertNotEqual(extract(path)["result"], OK)

    def test_long_path_of_valid_components(self):
        self.assert_name("DIR\\"*1000 + "A.BIN", "A.BIN")

    def test_output_capacity_and_untouched_guards(self):
        for capacity in range(8):
            self.assertEqual(extract("TEST.MAG", capacity)["result"], CAPACITY)
        for capacity in (8, 12, 13, 64, 65535):
            self.assertEqual(extract("TEST.MAG", capacity)["hex"], b"TEST.MAG".hex())

    def test_no_terminator_at_exact_capacity(self):
        self.assertEqual(extract("ABCDEFGH.XYZ", 12)["length"], 12)

    def test_null_arguments_preserve_outputs(self):
        for mask in range(1, 8):
            self.assertEqual(extract("A", 12, mask)["result"], ARGUMENT)
        self.assertEqual(validate("A", null=1), ARGUMENT)

    def test_validation_order(self):
        self.assertEqual(extract("CON", 0)["result"], RESERVED)
        self.assertEqual(extract("A.", 0)["result"], NAME)
        self.assertEqual(extract("", 0, 1)["result"], ARGUMENT)
        self.assertEqual(validate("A", 0, 1), ARGUMENT)

    def test_explicit_length_and_embedded_nul(self):
        self.assertEqual(validate(b"A\0B"), NAME)
        self.assertEqual(validate(b"AB", 1), OK)
        for length in (0, 13, 65535, constants()["uint_max"]):
            self.assertEqual(validate("A", length), NAME)


if __name__ == "__main__":
    unittest.main()
