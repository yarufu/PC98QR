# Phase 4A-2のホスト上の生バイナリ転送試験。
# QRやDOS経路は含まない。
"""Phase 4A-2: raw binary transport on the host only; no QR or DOS path."""
from pathlib import Path
import random
import tempfile
import unittest

import frame_transport_model as transport
import test_frame_header as c_header

SPECIAL = b"\x00\x1a\x80\xff\r\nP98Q"
BOUNDARIES = (
    (1, [1]), (2, [2]), (2943, [2943]), (2944, [2944]),
    (2945, [2944, 1]), (5887, [2944, 2943]), (5888, [2944, 2944]),
    (5889, [2944, 2944, 1]), (6000, [2944, 2944, 112]),
)


def binary_fixture(size):
    raw = bytearray((index * 73 + 19) % 256 for index in range(size))
    # 特殊バイト列を2943/2944と5887/5888の両境界にまたがらせる。
    # Split the special sequence across both 2943/2944 and 5887/5888.
    for offset in (0, 2944 - 5, 5888 - 5):
        if offset + len(SPECIAL) <= size:
            raw[offset:offset + len(SPECIAL)] = SPECIAL
    if raw:
        raw[-1] = 0x1A
    return bytes(raw)


def wire_frame(index, count, payload):
    # パーサーと異常系のために独立して転送列を構成し、分割関数は使わない。
    # Independent wire construction for parser/error tests, not the splitter.
    return (b"P98Q\x01" + index.to_bytes(2, "big")
            + count.to_bytes(2, "big") + payload)


class FrameCountTests(unittest.TestCase):
    def test_split_boundaries(self):
        for size, lengths in BOUNDARIES:
            with self.subTest(size=size):
                self.assertEqual(transport.frame_count_for_size(size), len(lengths))

    def test_empty_and_negative_size_rejected(self):
        for size in (0, -1):
            with self.assertRaisesRegex(ValueError, "positive"):
                transport.frame_count_for_size(size)

    def test_candidate_is_not_a_protocol_limit(self):
        # 候補サイズ+1も501フレームに収まる。
        # 502枚目が必要になるのは1474945バイトから。
        # Candidate + 1 still fits in 501; the 502nd frame starts at 1474945.
        for size, count in ((1474560, 501), (1474561, 501),
                            (1474944, 501), (1474945, 502)):
            with self.subTest(size=size):
                self.assertEqual(transport.frame_count_for_size(size), count)

    def test_wire_count_limit_without_large_allocation(self):
        self.assertEqual(65535 * 2944, 192935040)
        for size, count in ((192932096, 65534), (192932097, 65535),
                            (192935040, 65535)):
            with self.subTest(size=size):
                self.assertEqual(transport.frame_count_for_size(size), count)
        with self.assertRaisesRegex(ValueError, "exceeds 65535"):
            transport.frame_count_for_size(192935041)


class FrameSplitTests(unittest.TestCase):
    def test_exact_chunks_at_all_boundaries(self):
        for size, lengths in BOUNDARIES:
            with self.subTest(size=size):
                raw = binary_fixture(size)
                frames = transport.split_payload(raw)
                self.assertIsInstance(frames, list)
                self.assertEqual(len(frames), len(lengths))
                self.assertEqual([len(frame) for frame in frames],
                                 [9 + length for length in lengths])
                offset = 0
                for index, (frame, length) in enumerate(zip(frames, lengths)):
                    self.assertIsInstance(frame, bytes)
                    payload = raw[offset:offset + length]
                    self.assertEqual(frame, wire_frame(index, len(lengths), payload))
                    self.assertEqual(transport.parse_full_frame(frame),
                                     (index, len(lengths), payload))
                    offset += length
                self.assertEqual(offset, size)
                self.assertEqual(transport.reassemble_frames(frames), raw)

    def test_6000_exact_headers_and_no_padding(self):
        raw = binary_fixture(6000)
        frames = transport.split_payload(raw)
        self.assertEqual([frame[:9] for frame in frames], [
            bytes.fromhex("503938510100000003"),
            bytes.fromhex("503938510100010003"),
            bytes.fromhex("503938510100020003"),
        ])
        self.assertEqual([len(frame) for frame in frames], [2953, 2953, 121])
        self.assertEqual(frames[2][9:], raw[5888:])
        self.assertEqual(2 * 2944 + len(frames[2]) - 9, 6000)

    def test_empty_payload_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            transport.split_payload(b"")

    def test_representative_headers_match_existing_c_serializer(self):
        for size, indices in ((6000, (0, 1, 2)),
                              (1474560, (0, 255, 256, 500)),
                              (1474945, (501,))):
            frames = transport.split_payload(binary_fixture(size))
            for index in indices:
                with self.subTest(size=size, index=index):
                    self.assertEqual(c_header.call("serialize", index, len(frames)),
                                     {"result": c_header.OK,
                                      "bytes": frames[index][:9].hex()})


class FullFrameTests(unittest.TestCase):
    def test_final_payload_length_bounds_and_large_indices(self):
        for count in (1, 255, 256, 501, 502, 65535):
            for length in (1, 112, 2560, 2943, 2944):
                with self.subTest(count=count, length=length):
                    payload = binary_fixture(length)
                    self.assertEqual(transport.parse_full_frame(
                        wire_frame(count - 1, count, payload)),
                        (count - 1, count, payload))

    def test_non_final_full_payload(self):
        for index, count in ((0, 2), (255, 501), (256, 501), (65533, 65535)):
            payload = binary_fixture(2944)
            self.assertEqual(transport.parse_full_frame(wire_frame(index, count, payload)),
                             (index, count, payload))

    def test_short_frames_including_header_without_payload(self):
        valid = wire_frame(0, 1, b"x")
        for length in range(10):
            with self.subTest(length=length):
                with self.assertRaisesRegex(ValueError, "frame length"):
                    transport.parse_full_frame(valid[:length])

    def test_oversize_frames_including_final_payload_2945(self):
        for index, count in ((0, 1), (0, 2), (1, 2)):
            for length in (2945, 4096):
                with self.subTest(index=index, count=count, length=length):
                    with self.assertRaisesRegex(ValueError, "frame length"):
                        transport.parse_full_frame(wire_frame(index, count, b"x" * length))

    def test_each_invalid_magic_byte(self):
        for offset in range(4):
            raw = bytearray(wire_frame(0, 1, b"x"))
            raw[offset] ^= 0xFF
            with self.assertRaisesRegex(ValueError, "magic"):
                transport.parse_full_frame(bytes(raw))

    def test_invalid_versions(self):
        for version in (0, 2, 255):
            raw = bytearray(wire_frame(0, 1, b"x"))
            raw[4] = version
            with self.assertRaisesRegex(ValueError, "version"):
                transport.parse_full_frame(bytes(raw))

    def test_zero_count(self):
        for index in (0, 65535):
            with self.assertRaisesRegex(ValueError, "frame_count must be nonzero"):
                transport.parse_full_frame(wire_frame(index, 0, b"x"))

    def test_index_at_or_above_count(self):
        for index, count in ((1, 1), (2, 1), (501, 501), (65535, 65535)):
            with self.assertRaisesRegex(ValueError, "frame_index"):
                transport.parse_full_frame(wire_frame(index, count, b"x"))

    def test_non_final_short_payloads(self):
        for length in (1, 2943):
            with self.assertRaisesRegex(ValueError, "non-final payload length"):
                transport.parse_full_frame(wire_frame(0, 2, b"x" * length))

    def test_zero_payload_for_non_final_and_final_frames(self):
        for index in (0, 1):
            with self.assertRaisesRegex(ValueError, "frame length"):
                transport.parse_full_frame(wire_frame(index, 2, b""))


class FrameReassemblyTests(unittest.TestCase):
    def setUp(self):
        self.raw = binary_fixture(6000)
        self.frames = transport.split_payload(self.raw)

    def assert_round_trip(self, frames):
        result = transport.reassemble_frames(frames)
        self.assertIsInstance(result, bytes)
        self.assertEqual(len(result), len(self.raw))
        self.assertEqual(result, self.raw)

    def test_reverse_order(self):
        self.assert_round_trip(reversed(self.frames))

    def test_fixed_seed_shuffle(self):
        frames = self.frames.copy()
        random.Random(0x4A2).shuffle(frames)
        self.assertNotEqual(frames, self.frames)
        self.assert_round_trip(frames)

    def test_final_frame_first(self):
        self.assert_round_trip([self.frames[i] for i in (2, 0, 1)])

    def test_index_zero_last(self):
        self.assert_round_trip([self.frames[i] for i in (1, 2, 0)])

    def test_one_identical_duplicate(self):
        self.assert_round_trip([self.frames[i] for i in (0, 1, 1, 2)])

    def test_multiple_identical_duplicates(self):
        self.assert_round_trip([self.frames[i] for i in (2, 2, 2, 0, 1, 2)])

    def test_every_frame_twice(self):
        self.assert_round_trip(self.frames + list(reversed(self.frames)))

    def test_conflicting_payload_byte_in_either_order(self):
        conflict = bytearray(self.frames[1])
        conflict[9] ^= 0x01
        for pair in ((self.frames[1], bytes(conflict)), (bytes(conflict), self.frames[1])):
            with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                transport.reassemble_frames([self.frames[0], *pair, self.frames[2]])

    def test_conflicting_valid_final_lengths_in_either_order(self):
        for conflict in (self.frames[2][:-1], self.frames[2] + b"\x00"):
            transport.parse_full_frame(conflict)  # Both lengths are individually valid. / 日本語: 二つの長さは個別には有効。
            for pair in ((self.frames[2], conflict), (conflict, self.frames[2])):
                with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                    transport.reassemble_frames([*pair, *self.frames[:2]])

    def test_conflict_after_all_indices_received_still_rejects(self):
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            transport.reassemble_frames([*self.frames, self.frames[2] + b"\x00"])

    def test_different_count_in_either_order(self):
        for index in (0, 3):
            foreign = wire_frame(index, 4, binary_fixture(2944))
            for frames in ([foreign, *self.frames], [*self.frames, foreign]):
                with self.assertRaisesRegex(ValueError, "different frame_count"):
                    transport.reassemble_frames(frames)

    def test_missing_each_index_including_final(self):
        for missing in range(3):
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ValueError, "missing frames"):
                    transport.reassemble_frames([frame for index, frame in enumerate(self.frames)
                                                 if index != missing])

    def test_final_frame_alone_is_incomplete(self):
        with self.assertRaisesRegex(ValueError, "missing frames"):
            transport.reassemble_frames([self.frames[2]])

    def test_duplicates_do_not_fill_missing_index(self):
        with self.assertRaisesRegex(ValueError, "missing frames"):
            transport.reassemble_frames([self.frames[i] for i in (0, 0, 2)])

    def test_empty_collection(self):
        with self.assertRaisesRegex(ValueError, "missing frames"):
            transport.reassemble_frames([])

    def test_malformed_frame_after_complete_collection_still_rejects(self):
        with self.assertRaisesRegex(ValueError, "frame length"):
            transport.reassemble_frames([*self.frames, b""])

    def test_binary_special_values_and_magic_across_chunk_boundaries(self):
        self.assertTrue({0, 0x1A, 0x80, 0xFF, 0x0D, 0x0A} <= set(self.raw))
        for offset in (0, 2939, 5883):
            self.assertEqual(self.raw[offset:offset + 10], SPECIAL)
        self.assertEqual(self.raw[-1:], b"\x1a")
        self.assert_round_trip(reversed(self.frames))
        all_values = bytes(range(256)) * 24 + b"\x00\x1a"
        result = transport.reassemble_frames(reversed(transport.split_payload(all_values)))
        self.assertEqual(len(result), len(all_values))
        self.assertEqual(result, all_values)

    def test_6000_binary_temp_file_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            destination = Path(directory) / "restored.bin"
            source.write_bytes(self.raw)
            frames = transport.split_payload(source.read_bytes())
            random.Random(0x4A2).shuffle(frames)
            destination.write_bytes(transport.reassemble_frames(frames))
            self.assertEqual(source.stat().st_size, 6000)
            self.assertEqual(destination.stat().st_size, 6000)
            self.assertEqual(source.read_bytes(), self.raw)
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_candidate_501_frame_round_trip(self):
        raw = binary_fixture(1474560)
        frames = transport.split_payload(raw)
        self.assertEqual(len(frames), 501)
        parsed = [transport.parse_full_frame(frame) for frame in frames]
        self.assertEqual([frame.frame_index for frame in parsed], list(range(501)))
        self.assertEqual([frame.frame_count for frame in parsed], [501] * 501)
        self.assertEqual([len(frame.payload) for frame in parsed], [2944] * 500 + [2560])
        self.assertEqual([len(frame) for frame in frames], [2953] * 500 + [2569])
        random.Random(0x4A2).shuffle(frames)
        self.assertNotEqual([transport.parse_full_frame(frame).frame_index for frame in frames],
                            list(range(501)))
        result = transport.reassemble_frames(frames)
        self.assertEqual(len(result), 1474560)
        self.assertEqual(result, raw)

    def test_above_candidate_and_actual_502_frame_boundary(self):
        for size, count, final_length in ((1474561, 501, 2561), (1474945, 502, 1)):
            with self.subTest(size=size):
                raw = binary_fixture(size)
                frames = transport.split_payload(raw)
                self.assertEqual(len(frames), count)
                self.assertEqual([len(frame) for frame in frames],
                                 [2953] * (count - 1) + [9 + final_length])
                result = transport.reassemble_frames(reversed(frames))
                self.assertEqual(len(result), size)
                self.assertEqual(result, raw)

    def test_known_limitation_same_count_mixed_files_are_structurally_accepted(self):
        other_raw = bytes(value ^ 0xFF for value in self.raw)
        other_frames = transport.split_payload(other_raw)
        mixed = [self.frames[2], other_frames[1], self.frames[0]]
        result = transport.reassemble_frames(mixed)
        self.assertEqual(len(result), 6000)
        self.assertEqual(result, self.raw[:2944] + other_raw[2944:5888] + self.raw[5888:])
        self.assertNotEqual(result, self.raw)
        self.assertNotEqual(result, other_raw)
        # 構造上の受理は、どちらかの元ファイルを正しく転送できたことを意味しない。
        # Structural acceptance is not a successful transfer of either source file.


if __name__ == "__main__":
    unittest.main()
