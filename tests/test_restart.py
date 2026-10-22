import pathlib
import struct
import unittest

from akscope.core import akt, timeline
from akscope.core.symbols import SymbolTable
from akscope.verify import load_rules, runtime

MS = 32000  # cycles at 32 MHz
SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


def info(t):
    return akt.encode_frame(struct.pack("<BIBIBBBBB4sB", akt.ID_INFO, t, 1, 32000000, 18, 32, 16, 8, 16,
                                        bytes([0, 0, 0, 3]), 0))


def post(t, src, des, sig):
    return akt.encode_frame(struct.pack("<BIBBBBH", akt.ID_POST, t, src, des, sig, 0x80, 0))


def exe(t, dur, task, sig):
    return akt.encode_frame(struct.pack("<BIIBBBH", akt.ID_EXEC, t, dur, task, sig, 0x80, 0))


def pool(t):
    return akt.encode_frame(struct.pack("<BIBBBB", akt.ID_POOL, t, 1, 0, 0, 1))


SYM = SymbolTable({"tasks": {"4": {"name": "AC_TASK_LIFE_ID"}, "9": {"name": "AC_TASK_DISPLAY_ID"}},
                   "signals": {"4": {"10": "AC_LIFE_SYSTEM_CHECK"}, "9": {"16": "AC_DISPLAY_RENDER"}}})


def capture(with_info=True):
    """3 s of running, a display message posted and never run (the handler before it hung),
    then the board boots again"""
    data = info(0)
    data += post(1000 * MS, 0, 4, 10) + exe(1001 * MS, MS // 100, 4, 10)
    data += pool(2000 * MS) + post(2500 * MS, 4, 9, 16) + exe(2500 * MS, MS, 9, 16)
    data += post(3000 * MS, 4, 9, 16)
    data += b"[BOOT] version: 0.0.1\r\n"
    if with_info:
        data += info(50)
    data += post(31 * MS, 0, 4, 10) + exe(32 * MS, MS // 100, 4, 10)
    return data


class TestUnwrapper(unittest.TestCase):
    def test_restart_continues_after_the_last_record(self):
        records, text, _ = akt.decode_bytes(capture())
        restart = [r for r in records if r.restart]
        self.assertEqual([r.kind for r in restart], ["INFO"])
        times = [r.t_abs for r in records]
        # the boot's records come after the run before it, not 3 s back over it
        self.assertEqual(times[-1], 3000 * MS + 32 * MS)
        self.assertIn("[BOOT]", text)

    def test_restart_without_info(self):
        records, _, _ = akt.decode_bytes(capture(with_info=False))
        self.assertEqual([(r.kind, r.t) for r in records if r.restart], [("POST", 31 * MS)])

    def test_periodic_info_is_not_a_restart(self):
        data = info(0) + pool(1000 * MS) + info(64000 * MS) + pool(65000 * MS)
        records, _, _ = akt.decode_bytes(data)
        self.assertFalse(any(r.restart for r in records))

    def test_wrap_is_not_a_restart(self):
        # the counter wraps every 134 s at 32 MHz; that is a step forward
        data = info(0) + pool(0xFFFFF000) + pool(0x00001000)
        records, _, _ = akt.decode_bytes(data)
        self.assertFalse(any(r.restart for r in records))
        self.assertEqual(records[-1].t_abs - records[-2].t_abs, 0x2000)


class TestTimeline(unittest.TestCase):
    def test_restart_and_waiting_post(self):
        records, _, _ = akt.decode_bytes(capture())
        tl = timeline.build_timeline(records)
        self.assertEqual(len(tl.restarts), 1)
        t, waiting = tl.restarts[0]
        self.assertEqual(t, 3000 * MS + 50)
        self.assertEqual([(tl.posts[i].des, tl.posts[i].sig) for i in waiting], [(9, 16)])
        # the post after the boot pairs with its own run, not with one from before the restart
        self.assertEqual(tl.execs[-1].post, len(tl.posts) - 1)
        out = timeline.to_json(tl, SYM)
        self.assertAlmostEqual(out["restarts"][0]["t"], 3000001.5625)

    def test_r09(self):
        records, _, _ = akt.decode_bytes(capture())
        tl = timeline.build_timeline(records)
        f = runtime.run(tl, SYM, load_rules(None))
        r09 = [x for x in f if x.rule == "R09"]
        self.assertEqual(len(r09), 1)
        self.assertEqual(r09[0].severity, "error")
        self.assertIn("last run before it: AC_TASK_DISPLAY_ID / AC_DISPLAY_RENDER at 2500.000 ms", r09[0].message)
        self.assertIn("never run: AC_TASK_DISPLAY_ID / AC_DISPLAY_RENDER posted at 3000.000 ms", r09[0].message)


class TestRealReboot(unittest.TestCase):
    def test_shell_reboot(self):
        records, text, _ = akt.decode_bytes((SAMPLES / "base-kit-reboot-akt.bin").read_bytes())
        self.assertEqual([r.kind for r in records if r.restart], ["INFO"])
        tl = timeline.build_timeline(records)
        self.assertEqual(len(tl.restarts), 1)
        self.assertAlmostEqual(tl.us(tl.restarts[0][0]) / 1e3, 5850.0, delta=1)
        # the boot's display init comes after the restart, at its own place on the time axis
        init = next(e for e in tl.execs if (e.task, e.sig) == (9, 10))
        self.assertAlmostEqual(tl.us(init.t_begin) / 1e3, 5982.0, delta=1)
        self.assertIn("Wellcome to Active Kernel", text)
        r09 = [f for f in runtime.run(tl, SymbolTable(), load_rules(None)) if f.rule == "R09"]
        self.assertEqual(len(r09), 1)


if __name__ == "__main__":
    unittest.main()
