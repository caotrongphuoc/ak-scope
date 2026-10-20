import pathlib
import unittest

from akscope.core.console_log import parse_file, parse_line, parse_text
from akscope.core.model import COMMON_MSG_TYPE, PURE_MSG_TYPE

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


class TestParseLine(unittest.TestCase):
    def test_kernel_line(self):
        rec = parse_line("taskID: 3\tmsgType:0xc0\trefCnt:1\tsig:10\t\twaitTime:2\texeTime:90", 7)
        self.assertEqual(rec.origin, "console")
        self.assertEqual(rec.line, 7)
        self.assertEqual(rec.task_id, 3)
        self.assertEqual(rec.msg_type, COMMON_MSG_TYPE)
        self.assertEqual(rec.ref_count, 1)
        self.assertEqual(rec.sig, 10)
        self.assertEqual(rec.wait_ms, 2)
        self.assertEqual(rec.exe_ms, 90)

    def test_fatal_line(self):
        # format string from shell_fatal() case 'm' in app/shell.cpp
        line = "index: 5\ttask_id: 8\tmsg_type:0x80\tref_count:0\tsig: 12\t\twait_time: 3\texe_time: 41"
        rec = parse_line(line)
        self.assertEqual(rec.origin, "fatal")
        self.assertEqual(rec.index, 5)
        self.assertEqual(rec.task_id, 8)
        self.assertEqual(rec.msg_type, PURE_MSG_TYPE)
        self.assertEqual(rec.sig, 12)
        self.assertEqual(rec.wait_ms, 3)
        self.assertEqual(rec.exe_ms, 41)

    def test_other_text(self):
        for line in ("", "ver", "fatal m", "[PRINT] game start", "taskID: x"):
            self.assertIsNone(parse_line(line), line)

    def test_line_with_prefix(self):
        # shell echo and a log line can land on the same line
        rec = parse_line("fataltaskID: 4\tmsgType:0x80\trefCnt:1\tsig:10\t\twaitTime:0\texeTime:0")
        self.assertEqual(rec.task_id, 4)


class TestParseText(unittest.TestCase):
    def test_line_endings(self):
        rec = "taskID: 1\tmsgType:0x80\trefCnt:1\tsig:11\t\twaitTime:0\texeTime:0"
        for sep in ("\n", "\r\n", "\r"):
            records = parse_text(sep.join(["boot", rec, "ver", rec, ""]))
            self.assertEqual([r.line for r in records], [2, 4], repr(sep))

    def test_real_capture(self):
        records = parse_file(SAMPLES / "11m-game-idle-console.log")
        self.assertEqual(len(records), 24)
        self.assertTrue(all(r.origin == "console" for r in records))
        life = [r for r in records if r.task_id == 4]
        shell = [r for r in records if r.task_id == 3]
        self.assertEqual(len(life), 21)
        self.assertEqual([r.exe_ms for r in shell], [5, 1, 90])

    def test_boot_capture_with_text(self):
        # boot banner and -LINK_PRINT- lines sit between the kernel log lines
        records = parse_file(SAMPLES / "11m-game-boot-console.log")
        self.assertEqual(len(records), 84)
        self.assertEqual(records[0].line, 3)
        display = [r for r in records if r.task_id == 9]
        self.assertEqual([(r.sig, r.exe_ms) for r in display], [(10, 139), (14, 36), (104, 38)])


if __name__ == "__main__":
    unittest.main()
