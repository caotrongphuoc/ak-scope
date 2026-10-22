import json
import pathlib
import unittest

from akscope.core import akt, timeline
from akscope.core.akt import Record
from akscope.core.symbols import SymbolTable

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
HZ = 32000000


def rec(kind, t, **fields):
    return Record(kind=kind, rid=0, t=t & 0xFFFFFFFF, fields=fields, t_abs=t)


def post(t, src, des, sig):
    return rec("POST", t, src=src, des=des, sig=sig, msg_type=0x80, len=0)


def exe(t, dur, task, sig, wait_ms=0):
    return rec("EXEC", t, duration=dur, task=task, sig=sig, msg_type=0x80, wait_ms=wait_ms)


class TestPairing(unittest.TestCase):
    def test_fifo_per_task_and_sig(self):
        records = [
            rec("INFO", 0, cpu_hz=HZ),
            post(100, 5, 7, 10),
            post(110, 5, 7, 10),
            post(120, 5, 8, 10),  # same sig value, other task
            exe(200, 50, 8, 10),
            exe(300, 50, 7, 10),
            exe(400, 50, 7, 10),
        ]
        tl = timeline.build_timeline(records)
        e8, e7a, e7b = tl.execs
        self.assertEqual((e8.post, e7a.post, e7b.post), (2, 0, 1))
        self.assertEqual((e7a.wait, e7b.wait), (200, 290))
        self.assertEqual(tl.posts[1].exec, e7b.index)

    def test_timer_and_unknown(self):
        records = [
            rec("TIMER_SET", 10, des=4, sig=10, periodic=1, duty_ms=1000),
            exe(1000, 5, 4, 10),
            exe(2000, 5, 9, 10),
        ]
        tl = timeline.build_timeline(records)
        self.assertEqual(tl.execs[0].src, timeline.SRC_TIMER)
        self.assertEqual(tl.execs[1].src, timeline.SRC_UNKNOWN)
        self.assertEqual(tl.execs[1].wait, -1)

    def test_self_post_goes_to_next_run(self):
        # task 3 posts to itself during its own handler
        records = [exe(100, 50, 3, 11), post(120, 3, 3, 11), exe(200, 10, 3, 11)]
        tl = timeline.build_timeline(records)
        self.assertEqual(tl.execs[0].post, -1)
        self.assertEqual(tl.execs[1].post, 0)
        self.assertEqual(tl.execs[1].src, 3)

    def test_no_pairing_across_overflow(self):
        # the run of the first post was dropped; the next run must not take that post
        records = [post(100, 5, 7, 10), rec("OVERFLOW", 500, dropped=3), exe(900, 10, 7, 10)]
        tl = timeline.build_timeline(records)
        self.assertEqual(tl.execs[0].post, -1)
        self.assertEqual(tl.gaps, [400])

    def test_overflow_counted(self):
        tl = timeline.build_timeline([rec("OVERFLOW", 5, dropped=3), rec("OVERFLOW", 9, dropped=2)])
        self.assertEqual(tl.dropped, 5)


class TestRealCapture(unittest.TestCase):
    def setUp(self):
        records, text, self.stats = akt.decode_bytes((SAMPLES / "base-kit-boot-akt.bin").read_bytes())
        self.tl = timeline.build_timeline(records)
        self.sym = SymbolTable(json.loads((SAMPLES / "base-kit-symbols.json").read_text()))
        self.text = text

    def test_every_post_handled(self):
        # 8 posts, the last one from the MODE button interrupt
        self.assertEqual(len(self.tl.posts), 8)
        self.assertTrue(all(p.exec >= 0 for p in self.tl.posts))

    def test_waits_match_kernel(self):
        # the kernel takes both ends in whole ms ticks, so it can be off by up to 2 ms (the button
        # post: 0.94 ms paired, 2 ms in the kernel); the paired one is exact
        for e in self.tl.execs:
            if e.wait >= 0:
                self.assertAlmostEqual(self.tl.us(e.wait) / 1000, e.wait_ms_kernel, delta=2.0)

    def test_json(self):
        out = timeline.to_json(self.tl, self.sym, self.text, self.stats)
        json.dumps(out)
        uart = next(e for e in out["execs"] if e["sig_name"] == "AC_UART_IF_INIT")
        self.assertEqual(uart["src_name"], "idle")
        link = next(e for e in out["execs"] if e["sig_name"] == "AC_LINK_INIT")
        self.assertEqual(link["src_name"], "AC_TASK_UART_IF_ID")
        display = next(s for s in out["task_stats"] if s["name"] == "AC_TASK_DISPLAY_ID")
        self.assertAlmostEqual(display["exe_max_us"] / 1000, 338.6, delta=0.1)
        self.assertEqual(out["stream"]["corrupt"], 0)


if __name__ == "__main__":
    unittest.main()
