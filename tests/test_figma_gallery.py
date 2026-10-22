"""The gallery against the Figma file (tools/figma/compare.py), when the machine can run it.

Needs the design system file in AKSCOPE_FIG, geckodriver and Firefox on PATH, the generated
icons (python3 tools/figma/export.py all FILE.fig), and PyGObject with librsvg for the reference
images. Skipped otherwise; the comparison takes a few minutes.
"""

import importlib.util
import os
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIG = os.environ.get("AKSCOPE_FIG", "")


def _missing():
    if not FIG or not os.path.isfile(FIG):
        return "AKSCOPE_FIG is not set to the Figma file"
    if not shutil.which("geckodriver") or not shutil.which("firefox"):
        return "geckodriver or firefox not found"
    if not (ROOT / "akscope/web/icons").is_dir():
        return "icons not generated"
    for mod in ("gi", "cairo", "PIL"):
        if importlib.util.find_spec(mod) is None:
            return "python module %s missing" % mod
    return None


@unittest.skipIf(_missing(), _missing() or "")
class TestGallery(unittest.TestCase):
    def test_matches_figma(self):
        sys.path.insert(0, str(ROOT / "tools" / "figma"))
        import compare
        self.assertEqual(compare.main([FIG]), 0, "gallery entries differ from Figma, see the compare output")


if __name__ == "__main__":
    unittest.main()
