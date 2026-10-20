import contextlib
import io
import unittest

from akscope import __version__
from akscope.cli import main


class TestCli(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), "akscope " + __version__)

    def test_no_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ret = main([])
        self.assertEqual(ret, 1)
        self.assertIn("usage: akscope", out.getvalue())


if __name__ == "__main__":
    unittest.main()
