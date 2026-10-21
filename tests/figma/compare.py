"""Compare the gallery page with the Figma design system, pixel by pixel.

    python3 tests/figma/compare.py FILE.fig [--only GROUP] [--out DIR]

Every gallery entry names a component variant of the Figma file. The variant is drawn from
the .fig data (tools/figkit.py) on the same surface color as the gallery cell, the page is
shot in headless Firefox through geckodriver, and the two images are compared:

- size must be equal
- "raw" is the share of pixels whose color differs by more than TOLERANCE in any channel
- "off" is the same after a 1 px gaussian blur of both images, and must stay under MAX_OFF.
  Text is drawn by Firefox from Inter 3.19 (hinted, baseline on whole pixels) and by librsvg
  from Figma's glyph outlines, so letter edges never match pixel for pixel; the blur keeps
  that out while a wrong color, a shape off by a pixel or more, or a wrong size still fails

An entry may name rectangles to leave out ("ignore": [[x, y, w, h], ...] in target pixels),
for the places where the Figma file shows a VMS product icon that ak-scope does not use.

A side by side image (Figma | page | difference) is written for every entry to DIR.
Exit code 1 when an entry fails.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import pathlib
import pickle
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import figkit  # noqa: E402
from PIL import Image, ImageChops, ImageFilter  # noqa: E402

TOLERANCE = 40
MAX_OFF = 0.01
CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "akscope" / "figma"
WD_PORT = 4466


def load_doc(fig):
    st = os.stat(fig)
    key = hashlib.sha1(("%s %d %d" % (fig, st.st_size, st.st_mtime_ns)).encode()).hexdigest()[:16]
    path = CACHE / (key + ".pkl")
    if path.is_file():
        with open(path, "rb") as f:
            return pickle.load(f)
    doc = figkit.Doc(fig)
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(doc, f)
    return doc


class WebDriver:
    def __init__(self):
        self.log = open(CACHE / "geckodriver.log", "w")
        self.proc = subprocess.Popen(["geckodriver", "--port", str(WD_PORT)], stdout=self.log,
                                     stderr=subprocess.STDOUT)
        # the snap Firefox can take a while to start
        for _ in range(150):
            try:
                self.call("GET", "/status")
                break
            except OSError:
                time.sleep(0.2)
        caps = {"capabilities": {"alwaysMatch": {"moz:firefoxOptions": {"args": ["-headless"]}}}}
        self.sid = self.call("POST", "/session", caps)["sessionId"]
        self.s = "/session/" + self.sid
        self.call("POST", self.s + "/window/rect", {"width": 1800, "height": 1200})

    def call(self, method, path, body=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (WD_PORT, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["value"]

    def js(self, script):
        return self.call("POST", self.s + "/execute/sync", {"script": script, "args": []})

    def element_png(self, css):
        el = self.call("POST", self.s + "/element", {"using": "css selector", "value": css})
        eid = next(iter(el.values()))
        return Image.open(io.BytesIO(base64.b64decode(self.call("GET", self.s + "/element/%s/screenshot" % eid))))

    def close(self):
        try:
            self.call("DELETE", self.s)
        finally:
            self.proc.terminate()
            self.proc.wait()
            self.log.close()


def serve():
    from akscope.server import State, make_server
    httpd = make_server(State(), port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def find_symbol(doc, page_name, ref):
    page = doc.page(page_name)
    for n in doc.walk(page):
        if n.get("type") == "SYMBOL" and n.get("name") == ref:
            return n
    return None


def color_modes(doc, mode):
    """{color set guid: mode guid} for "dark" or "light"."""
    for k, n in doc.nodes.items():
        if n.get("type") == "VARIABLE_SET" and n.get("name") == "1. Color mode":
            for m in n.get("variableSetModes", []):
                if m["name"] == "default-" + mode:
                    yield k, figkit.gid(m["id"])


def reference(doc, sym, mode, background, pad=0):
    r = figkit.Renderer(doc)
    r.modes = dict(color_modes(doc, mode))
    svg = r.svg(sym, pad=pad, background=background)
    tmp = CACHE / "ref.png"
    figkit.svg_to_png(svg, tmp)
    return Image.open(tmp).convert("RGB")


def _share_over(diff):
    px = diff.load()
    w, h = diff.size
    return sum(1 for y in range(h) for x in range(w) if max(px[x, y]) > TOLERANCE) / float(w * h)


def compare(ref, page_img, ignore=()):
    """(raw share, blurred share, diff image), or Nones when the sizes differ."""
    a, b = ref.convert("RGB"), page_img.convert("RGB")
    if a.size != b.size:
        return None, None, None
    for x, y, w, h in ignore:
        a.paste((128, 128, 128), (x, y, x + w, y + h))
        b.paste((128, 128, 128), (x, y, x + w, y + h))
    diff = ImageChops.difference(a, b)
    blur = ImageChops.difference(a.filter(ImageFilter.GaussianBlur(1)), b.filter(ImageFilter.GaussianBlur(1)))
    return _share_over(diff), _share_over(blur), diff


def side_by_side(ref, page_img, diff):
    w = ref.width + page_img.width + (diff.width if diff else 0) + 16
    h = max(ref.height, page_img.height)
    out = Image.new("RGB", (w, h), (128, 128, 128))
    out.paste(ref.convert("RGB"), (0, 0))
    out.paste(page_img.convert("RGB"), (ref.width + 8, 0))
    if diff:
        out.paste(diff.point(lambda v: min(255, v * 4)), (ref.width + page_img.width + 16, 0))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fig")
    ap.add_argument("--only", help="compare one gallery group, e.g. Button")
    ap.add_argument("--out", default=str(CACHE / "compare"))
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t = time.time()
    doc = load_doc(args.fig)
    print("figma file loaded in %.1f s" % (time.time() - t))

    httpd = serve()
    wd = WebDriver()
    failed = 0
    rows = []
    try:
        wd.call("POST", wd.s + "/url", {"url": "http://127.0.0.1:%d/gallery.html" % httpd.server_address[1]})
        for _ in range(100):
            if wd.js("return window.GALLERY_READY === true"):
                break
            time.sleep(0.1)
        entries = wd.js("return window.GALLERY")
        for e in entries:
            if args.only and e["group"] != args.only:
                continue
            sym = find_symbol(doc, e["page"], e["ref"])
            if sym is None:
                rows.append((e, "missing in figma", None))
                failed += 1
                continue
            bg = wd.js("return getComputedStyle(document.querySelector('[data-n=\"%d\"]')).backgroundColor" % e["n"])
            rgb = tuple(int(v) for v in bg[bg.index("(") + 1:bg.index(")")].split(",")[:3])
            ref = reference(doc, sym, e["mode"], "#%02x%02x%02x" % rgb, e.get("pad", 0))
            shot = wd.element_png('[data-n="%d"] .g-target' % e["n"])
            raw, off, diff = compare(ref, shot, e.get("ignore") or ())
            name = "%03d-%s-%s" % (e["n"], e["mode"], "".join(c if c.isalnum() else "_" for c in e["ref"]))[:120]
            side_by_side(ref, shot, diff).save(out / (name + ".png"))
            if off is None:
                status = "size %dx%d, figma %dx%d" % (shot.width, shot.height, ref.width, ref.height)
                failed += 1
            elif off > MAX_OFF:
                status = "%.1f %% off (raw %.1f %%)" % (100 * off, 100 * raw)
                failed += 1
            else:
                status = "ok (%.1f %%, raw %.1f %%)" % (100 * off, 100 * raw)
            rows.append((e, status, name))
    finally:
        wd.close()
        httpd.shutdown()

    for e, status, name in rows:
        print("%-5s %-6s %-62s %s" % (e["group"][:5], e["mode"], e["ref"][:62], status))
    print("%d of %d failed, images in %s" % (failed, len(rows), out))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
