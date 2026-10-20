import json
import struct
import tempfile
import time
import unittest
from unittest import mock

from akscope import live
from akscope.core.akt import ID_EXEC, ID_INFO, ID_POST, encode_frame
from akscope.core.aktfile import read_capture
from akscope.core.symbols import SymbolTable


def info():
    return encode_frame(struct.pack("<BIBIBBBBB4sB", ID_INFO, 0, 1, 32000000, 18, 32, 16, 8, 16,
                                    bytes([0, 0, 0, 3]), 0))


def post(t, src, des, sig):
    return encode_frame(struct.pack("<BIBBBBH", ID_POST, t, src, des, sig, 0x80, 0))


def exe(t, dur, task, sig):
    return encode_frame(struct.pack("<BIIBBBH", ID_EXEC, t, dur, task, sig, 0x80, 0))


def wait_for(viewer, kind, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            event, data = viewer.events.get(timeout=0.1)
        except Exception:
            continue
        if event == kind:
            return data
    raise AssertionError("no %s event" % kind)


class TestLiveSession(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        sym = {"tasks": {"6": {"name": "AC_TASK_UART_IF_ID", "handler": "task_uart_if", "pri": 4}},
               "signals": {"6": {"10": "AC_UART_IF_INIT"}}, "fallback_signals": {}}
        self.s = live.LiveSession(symbols=SymbolTable(sym), symbols_raw=sym, out_dir=self.dir.name)

    def tearDown(self):
        self.s.stop()
        self.dir.cleanup()

    def test_snapshot_then_deltas(self):
        v = self.s.add_viewer()
        self.assertEqual(wait_for(v, "status")["state"], "idle")
        self.assertTrue(self.s.start("loop://", 115200))
        self.assertEqual(wait_for(v, "status")["state"], "recording")
        snap = wait_for(v, "snapshot")
        self.assertEqual(snap["execs"], [])
        self.assertTrue(snap["live"])

        self.s._serial.write(b"[BOOT] start\r\n" + info() + post(100, 0xEF, 6, 10))
        self.s._serial.write(exe(150, 2267, 6, 10))
        d = wait_for(v, "delta")
        execs, posts = d["execs"], d["posts"]
        while not execs:
            d = wait_for(v, "delta")
            execs, posts = execs + d["execs"], posts + d["posts"]
        self.assertEqual(execs[0]["sig_name"], "AC_UART_IF_INIT")
        self.assertEqual(execs[0]["src_name"], "idle")
        self.assertEqual(execs[0]["post"], 0)
        self.assertEqual(d["info"]["app_ver"], [0, 0, 0, 3])

        # a viewer that joins now gets everything once, in its snapshot
        late = self.s.add_viewer()
        snap2 = wait_for(late, "snapshot")
        self.assertEqual(len(snap2["execs"]), 1)
        self.assertIn("[BOOT] start", snap2["text"])

    def test_capture_file(self):
        self.s.start("loop://", 115200)
        path = self.s.status["file"]
        self.s._serial.write(info() + exe(10, 5, 4, 10))
        time.sleep(0.5)
        self.s.stop()
        header, data = read_capture(path)
        self.assertEqual(header["port"], "loop://")
        self.assertIn("symbols", header)
        self.assertEqual(data, info() + exe(10, 5, 4, 10))

    def test_bad_port(self):
        self.assertFalse(self.s.start("/dev/does-not-exist", 115200))
        self.assertEqual(self.s.status["state"], "idle")
        self.assertTrue(self.s.status["message"])

    def test_flash_then_record(self):
        self.s.flash_target = "fw.bin"
        v = self.s.add_viewer()
        with mock.patch("akscope.flash.flash") as fl:
            self.assertTrue(self.s.flash("loop://", 115200))
            states = []
            while "recording" not in states:
                states.append(wait_for(v, "status")["state"])
        fl.assert_called_once()
        self.assertEqual(fl.call_args.args[:2], ("loop://", "fw.bin"))
        self.assertIn("flashing", states)

    def test_flash_needs_target(self):
        self.assertFalse(self.s.flash("loop://", 115200))


if __name__ == "__main__":
    unittest.main()
