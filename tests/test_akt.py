import pathlib
import struct
import unittest

from akscope.core import akt

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


def post(t, src, des, sig, msg_type=0x80, length=0):
    return struct.pack("<BIBBBBH", akt.ID_POST, t, src, des, sig, msg_type, length)


def exec_(t, duration, task, sig, msg_type=0x80, wait_ms=0):
    return struct.pack("<BIIBBBH", akt.ID_EXEC, t, duration, task, sig, msg_type, wait_ms)


class TestCrc(unittest.TestCase):
    def test_check_value(self):
        # catalogue check value of CRC-8/SMBUS
        self.assertEqual(akt.crc8(b"123456789"), 0xF4)


class TestCobs(unittest.TestCase):
    def test_round_trip(self):
        for data in (b"", b"\x00", b"\x00\x00", b"\x11\x22\x00\x33", bytes(range(256)) * 2):
            enc = akt.cobs_encode(data)
            self.assertNotIn(0, enc)
            self.assertEqual(akt.cobs_decode(enc), data)

    def test_known_vector(self):
        self.assertEqual(akt.cobs_encode(b"\x11\x22\x00\x33"), b"\x03\x11\x22\x02\x33")

    def test_invalid(self):
        self.assertIsNone(akt.cobs_decode(b"\x05\x11"))
        self.assertIsNone(akt.cobs_decode(b"\x02\x00"))


class TestDecodeRecord(unittest.TestCase):
    def test_sizes_match_spec(self):
        # sizes from the table in docs/trace-format.md
        self.assertEqual(akt.RECORD_SIZE, {1: 20, 2: 11, 3: 14, 4: 12, 5: 7, 6: 9, 7: 7, 8: 10})

    def test_post(self):
        rec = post(0x01020304, 6, 17, 10, 0xC0, 11)
        r = akt.decode_record(rec + bytes([akt.crc8(rec)]))
        self.assertEqual(r.kind, "POST")
        self.assertEqual(r.t, 0x01020304)
        self.assertEqual(r.fields, {"src": 6, "des": 17, "sig": 10, "msg_type": 0xC0, "len": 11})

    def test_bad_crc(self):
        rec = post(1, 2, 3, 4)
        self.assertIsNone(akt.decode_record(rec + bytes([akt.crc8(rec) ^ 1])))

    def test_wrong_length(self):
        rec = post(1, 2, 3, 4) + b"\x99"
        self.assertIsNone(akt.decode_record(rec + bytes([akt.crc8(rec)])))

    def test_unknown_id(self):
        rec = b"\x7f\x01\x02"
        self.assertEqual(akt.decode_record(rec + bytes([akt.crc8(rec)])), "unknown")


class TestStream(unittest.TestCase):
    def test_text_and_frames(self):
        data = (b"[BOOT] start\r\n" + akt.encode_frame(post(100, 239, 6, 10))
                + akt.encode_frame(exec_(150, 2267, 6, 10)) + b"hello\r\n"
                + akt.encode_frame(exec_(400, 10, 4, 10)))
        records, text, stats = akt.decode_bytes(data)
        self.assertEqual([r.kind for r in records], ["POST", "EXEC", "EXEC"])
        self.assertEqual(text, "[BOOT] start\r\nhello\r\n")
        self.assertEqual((stats.frames, stats.corrupt), (3, 0))

    def test_split_across_feeds(self):
        data = b"ab" + akt.encode_frame(post(1, 2, 3, 4)) + b"cd"
        dec = akt.StreamDecoder()
        items = []
        for b in data:
            items += dec.feed(bytes([b]))
        items += dec.finish()
        self.assertEqual([i if isinstance(i, str) else i.kind for i in items], ["ab", "POST", "cd"])

    def test_frame_broken_by_text(self):
        frame = akt.encode_frame(post(1, 2, 3, 4))
        broken = frame[:5] + b"ISR" + frame[5:]
        records, text, stats = akt.decode_bytes(broken + akt.encode_frame(post(9, 2, 3, 4)))
        self.assertEqual([r.t for r in records], [9])
        self.assertEqual(stats.corrupt, 1)

    def test_unwrap(self):
        data = b"".join(akt.encode_frame(post(t, 1, 2, 3)) for t in
                        (0xFFFFFF00, 0x00000010, 0x00000005, 0x00000100))
        records, _, _ = akt.decode_bytes(data)
        base = 0xFFFFFF00
        # 0x10 is after the wrap; 0x05 is an EXEC-style step back, not a second wrap
        self.assertEqual([r.t_abs - base for r in records], [0, 0x110, 0x105, 0x200])


class TestRealCapture(unittest.TestCase):
    def setUp(self):
        data = (SAMPLES / "11m-game-boot-akt.bin").read_bytes()
        self.records, self.text, self.stats = akt.decode_bytes(data)

    def test_counts(self):
        self.assertEqual((self.stats.frames, self.stats.corrupt), (68, 0))
        self.assertTrue(self.text.startswith("[BOOT] version: 0.0.1"))

    def test_info(self):
        info = self.records[0]
        self.assertEqual(info.kind, "INFO")
        self.assertEqual(info.fields["cpu_hz"], 32000000)
        self.assertEqual(info.fields["task_count"], 18)

    def test_time_span(self):
        span = self.records[-1].t_abs - self.records[0].t_abs
        self.assertAlmostEqual(span / 32e6, 20.0, delta=0.1)

    def test_display_init_time(self):
        # the kernel log of the same firmware reports 139 ms for this handler
        ex = [r for r in self.records if r.kind == "EXEC" and r.fields["task"] == 9]
        self.assertEqual(int(ex[0].fields["duration"] / 32000), 139)


if __name__ == "__main__":
    unittest.main()
