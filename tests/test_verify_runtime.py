import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from akscope.cli import main
from akscope.core import timeline
from akscope.core.akt import Record
from akscope.core.symbols import SymbolTable
from akscope.verify import load_rules, parse_duration_us, runtime

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
MS = 32000  # cycles per ms at 32 MHz

SYM = SymbolTable({
    "tasks": {"4": {"name": "LIFE", "pri": 6}, "9": {"name": "DISPLAY", "pri": 4}},
    "signals": {"4": {"10": "LIFE_CHECK"}, "9": {"10": "DISPLAY_INIT", "11": "DISPLAY_LOGO"}},
})


def rec(kind, t_ms, **fields):
    t = int(t_ms * MS)
    return Record(kind=kind, rid=0, t=t & 0xFFFFFFFF, fields=fields, t_abs=t)


def info(**pools):
    f = dict(version=1, cpu_hz=32000000, task_count=10, pure_pool=32, common_pool=8,
             dynamic_pool=8, timer_pool=16, app_ver=[0, 0, 0, 1], flags=0)
    f.update(pools)
    return rec("INFO", 0, **f)


def exe(t_ms, dur_ms, task, sig, wait_ms=0):
    return rec("EXEC", t_ms, duration=int(dur_ms * MS), task=task, sig=sig, msg_type=0x80, wait_ms=wait_ms)


def post(t_ms, src, des, sig):
    return rec("POST", t_ms, src=src, des=des, sig=sig, msg_type=0x80, len=0)


def timer_set(t_ms, des, sig, duty, periodic=1):
    return rec("TIMER_SET", t_ms, des=des, sig=sig, periodic=periodic, duty_ms=duty)


def run(records, handled=None, **overrides):
    rules = load_rules()
    rules.update(overrides)
    tl = timeline.build_timeline([info()] + records)
    return runtime.run(tl, SYM, rules, handled)


class TestRules(unittest.TestCase):
    def test_r01_grouped_with_worst(self):
        f = [x for x in run([exe(10, 12, 9, 10), exe(100, 80, 9, 10), exe(300, 2, 9, 10)]) if x.rule == "R01"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, "error")
        self.assertIn("DISPLAY / DISPLAY_INIT ran 80.0 ms", f[0].message)
        self.assertIn("2 runs over 10 ms", f[0].message)
        self.assertEqual(f[0].exec, 1)

    def test_r02_wait(self):
        f = [x for x in run([post(0, 4, 9, 11), exe(150, 1, 9, 11)]) if x.rule == "R02"]
        self.assertEqual(len(f), 1)
        self.assertIn("waited 150.0 ms", f[0].message)

    def test_r03_pool(self):
        records = [rec("POOL", 1000, pure=30, common=8, dynamic=0, timer=2)]
        f = {x.message.split()[0]: x.severity for x in run(records) if x.rule == "R03"}
        self.assertEqual(f, {"pure": "warning", "common": "error"})

    def test_r04_storm(self):
        records = [post(i * 5, 4, 9, 10) for i in range(150)]
        f = [x for x in run(records) if x.rule == "R04"]
        self.assertEqual(len(f), 1)
        self.assertIn("posted 150 times in 1 s", f[0].message)

    def test_r05_drift(self):
        records = [timer_set(0, 4, 10, 100)] + [exe(t, 0.01, 4, 10) for t in (100, 200, 300, 450, 550)]
        f = [x for x in run(records) if x.rule == "R05"]
        self.assertEqual(len(f), 1)
        self.assertIn("fired 150.0 ms after the previous run, period 100 ms", f[0].message)

    def test_r06_needs_handled_set(self):
        records = [exe(10, 1, 9, 12)]
        self.assertEqual([x for x in run(records) if x.rule == "R06"], [])
        f = [x for x in run(records, handled={9: {10, 11}}) if x.rule == "R06"]
        self.assertEqual(len(f), 1)

    def test_r07_dropped(self):
        f = [x for x in run([rec("OVERFLOW", 5, dropped=7)]) if x.rule == "R07"]
        self.assertEqual(f[0].severity, "error")
        self.assertIn("7 records dropped", f[0].message)

    def test_r08_expect(self):
        expect = [{"name": "logo after init", "after": {"task": "DISPLAY", "sig": "DISPLAY_INIT"},
                   "then": {"task": 9, "sig": 11}, "within": "50ms"}]
        ok = [exe(0, 1, 9, 10), exe(20, 1, 9, 11)]
        late = [exe(0, 1, 9, 10), exe(80, 1, 9, 11)]
        self.assertEqual([x for x in run(ok, expect=expect) if x.rule == "R08"], [])
        f = [x for x in run(late, expect=expect) if x.rule == "R08"]
        self.assertEqual(len(f), 1)
        self.assertIn("1 of 1 times", f[0].message)

    def test_duration(self):
        self.assertEqual(parse_duration_us("60ms"), 60000)
        self.assertEqual(parse_duration_us("2 s"), 2e6)
        self.assertEqual(parse_duration_us(500), 500)
        with self.assertRaises(ValueError):
            parse_duration_us("fast")


class TestCli(unittest.TestCase):
    def verify(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ret = main(["verify", "--trace", str(SAMPLES / "11m-game-boot-akt.bin"), *extra])
        return ret, out.getvalue()

    def test_real_trace(self):
        ret, out = self.verify()
        self.assertEqual(ret, 1)
        self.assertIn("error R01 9 / 10 ran 139.1 ms (limit 50 ms)", out)
        self.assertTrue(out.strip().endswith("2 errors, 3 warnings"))
        # errors come first
        self.assertIn("error", out.splitlines()[0])

    def test_rules_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("exe_error_ms: 200\nignore:\n  - rule: R01\n    match: '2 / 20'\n")
        ret, out = self.verify("--rules", f.name, "--format", "json")
        pathlib.Path(f.name).unlink()
        data = json.loads(out)
        self.assertEqual(ret, 0)
        self.assertEqual(data["errors"], 0)
        self.assertFalse(any("2 / 20" in x["message"] for x in data["findings"]))

    def test_strict(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("exe_error_ms: 200\n")
        ret, _ = self.verify("--rules", f.name, "--strict")
        pathlib.Path(f.name).unlink()
        self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
