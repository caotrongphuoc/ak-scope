import contextlib
import io
import pathlib
import tempfile
import unittest

from akscope.cli import main
from akscope.core.aktfile import AktWriter, read_capture

SAMPLE = str(pathlib.Path(__file__).resolve().parent.parent / "samples" / "base-kit-boot-akt.bin")


class TestAktFile(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "run.akt"
            with AktWriter(path, {"port": "/dev/ttyUSB0", "baud": 115200}) as w:
                w.write(b"\x00\x01\x02")
                w.write(b"text\r\n")
            header, data = read_capture(path)
        self.assertEqual(header, {"port": "/dev/ttyUSB0", "baud": 115200})
        self.assertEqual(data, b"\x00\x01\x02text\r\n")

    def test_raw_file(self):
        header, data = read_capture(SAMPLE)
        self.assertEqual(header, {})
        self.assertGreater(len(data), 4000)


class TestDump(unittest.TestCase):
    def test_boot_sample(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ret = main(["dump", SAMPLE])
        self.assertEqual(ret, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 153)
        self.assertIn("INFO", lines[0])
        self.assertIn("153 frames, 0 corrupt", err.getvalue())
        # sorted by start time: task 7 runs, then posts to 12 from inside its handler
        i = next(i for i, l in enumerate(lines) if "EXEC" in l and "task 7 " in l)
        self.assertIn("7 -> 12", lines[i + 1])
        self.assertTrue(any("exe 154.671 ms" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
