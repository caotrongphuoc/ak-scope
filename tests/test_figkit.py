import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "figma"))
import figkit  # noqa: E402
import export as figma_export  # noqa: E402


def blob(*cmds):
    out = b""
    for op, *vals in cmds:
        out += bytes([op]) + struct.pack("<%df" % len(vals), *vals)
    return out


class TestPath(unittest.TestCase):
    def test_commands(self):
        b = blob((1, 0, 0), (2, 10, 0), (4, 10, 5, 5, 10, 0, 10), (3, 0, 5, 0, 0), (0,))
        self.assertEqual(figkit.path_d(b), "M0.000 0.000 L10.000 0.000 C10.000 5.000 5.000 10.000 0.000 10.000 "
                                           "Q0.000 5.000 0.000 0.000 Z")

    def test_leading_close_dropped(self):
        # glyph blobs start with a close, which SVG rejects before the first move
        self.assertEqual(figkit.path_d(blob((0,), (1, 1, 2), (0,))), "M1.000 2.000 Z")

    def test_color(self):
        self.assertEqual(figkit.color_hex({"r": 0.0941, "g": 0.5647, "b": 1.0}), "#1890ff")


class TestNames(unittest.TestCase):
    def test_css_name(self):
        self.assertEqual(figma_export.css_name("color/background/base"), "--color-background-base")
        self.assertEqual(figma_export.css_name("font/fontSize/lg-14"), "--font-fontsize-lg-14")
        self.assertEqual(figma_export.css_name("color/background/input field"), "--color-background-input-field")


if __name__ == "__main__":
    unittest.main()
