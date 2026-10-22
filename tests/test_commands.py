import contextlib
import csv
import io
import json
import pathlib
import unittest

from akscope.cli import main

SAMPLE = str(pathlib.Path(__file__).resolve().parent.parent / "samples" / "base-kit-idle-console.log")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        ret = main(argv)
    return ret, out.getvalue(), err.getvalue()


class TestImportLog(unittest.TestCase):
    def test_text(self):
        ret, out, _ = run(["import-log", SAMPLE])
        self.assertEqual(ret, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["line", "origin", "task", "sig", "type", "ref",
                                            "wait_ms", "exe_ms"])
        self.assertEqual(len(lines), 1 + 186)
        shell = [l.split()[2:] for l in lines[1:] if l.split()[2] == "3"]
        self.assertEqual(shell[-1], ["3", "10", "common", "1", "0", "235"])

    def test_csv(self):
        ret, out, _ = run(["import-log", SAMPLE, "--format", "csv"])
        self.assertEqual(ret, 0)
        rows = list(csv.DictReader(io.StringIO(out)))
        self.assertEqual(len(rows), 186)
        self.assertEqual(rows[0]["task_id"], "9")

    def test_json(self):
        ret, out, _ = run(["import-log", SAMPLE, "--format", "json"])
        self.assertEqual(ret, 0)
        records = json.loads(out)
        self.assertEqual(max(r["exe_ms"] for r in records), 235)

    def test_no_records(self):
        ret, out, err = run(["import-log", __file__])
        self.assertEqual(ret, 2)
        self.assertEqual(out, "")
        self.assertIn("AK_TASK_LOG_CONSOLE_ENABLE", err)


class TestStats(unittest.TestCase):
    def test_sample(self):
        ret, out, _ = run(["stats", SAMPLE])
        self.assertEqual(ret, 0)
        self.assertIn("186 messages, 8 tasks", out)
        # shell task: exe 63, 38, 235 ms
        task_rows = [l.split() for l in out.splitlines() if l.split()[:1] == ["3"]]
        self.assertEqual(task_rows[0], ["3", "3", "38", "112.0", "235", "336", "0"])


if __name__ == "__main__":
    unittest.main()
