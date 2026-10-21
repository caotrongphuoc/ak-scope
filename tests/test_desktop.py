import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from akscope import desktop

try:
    from akscope import app
except ImportError:
    app = None


class TestDesktopEntry(unittest.TestCase):
    def test_entry(self):
        text = desktop.desktop_entry(python="/usr/bin/python3")
        self.assertIn("Exec=/usr/bin/python3 -m akscope app\n", text)
        self.assertIn("Icon=akscope\n", text)
        self.assertIn("StartupWMClass=akscope\n", text)

    def test_entry_with_project(self):
        with tempfile.TemporaryDirectory() as d:
            proj = pathlib.Path(d) / "11m game"
            proj.mkdir()
            text = desktop.desktop_entry(proj, python="/usr/bin/python3")
            self.assertIn("Name=ak-scope (11m game)\n", text)
            # the path is quoted for the Exec line
            self.assertIn("--project '%s'" % proj.resolve(), text)

    def test_install(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d}), \
                mock.patch("shutil.which", return_value=None):
            entry, icon = desktop.install()
            self.assertEqual(entry, pathlib.Path(d) / "applications" / "akscope.desktop")
            self.assertTrue(entry.read_text().startswith("[Desktop Entry]\n"))
            self.assertIn(sys.executable, entry.read_text())
            self.assertTrue(icon.read_text().startswith("<svg"))


@unittest.skipIf(app is None, "GTK 3 / WebKit2GTK 4.0 for Python not installed")
class TestAppHelpers(unittest.TestCase):
    def test_app_id_per_project(self):
        with tempfile.TemporaryDirectory() as d:
            a = app.app_id(d)
            self.assertTrue(a.startswith(app.APP_ID + ".p"))
            self.assertEqual(a, app.app_id(d + "/"))
            self.assertNotEqual(a, app.app_id(os.path.dirname(d)))
        self.assertEqual(app.app_id(None), app.APP_ID)

    def test_window_state(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(app, "STATE_FILE", pathlib.Path(d) / "w.json"):
            self.assertEqual(app.load_state("x"), {})
            app.save_state("x", {"size": [800, 600]})
            app.save_state("y", {"maximized": True})
            self.assertEqual(app.load_state("x"), {"size": [800, 600]})
            self.assertEqual(app.load_state("y"), {"maximized": True})


if __name__ == "__main__":
    unittest.main()
