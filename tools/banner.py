"""Make the README banner: docs/images/banner/banner-ak-scope.gif and banner-ak-scope-2x1.png.

    python3 tools/banner.py

The banner is a 1280 x 640 page drawn here and shot in headless Firefox (tools/figma/compare.py
starts it): title, the path from the kit to the page, and a timeline of the base kit boot drawn
from samples/base-kit-boot-akt.bin, the init chain from the idle task to the link layer. The GIF
moves a playhead over it the way the live view follows a capture. Needs Firefox, geckodriver
and Pillow. Colors are the dark tokens of the page.
"""

import base64
import functools
import http.server
import io
import json
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "figma"))

from akscope.core import akt, timeline  # noqa: E402
from akscope.core.symbols import SymbolTable  # noqa: E402

OUT = ROOT / "docs" / "images" / "banner"
W, H = 1280, 640
FRAMES = 24
FRAME_MS = 120

# the boot chain: 31.8 to 34.4 ms after INFO
T0, T1 = 31.80, 34.40
ROWS = ["idle", "AC_TASK_RF24_IF_ID", "AC_TASK_UART_IF_ID", "AC_LINK_ID", "AC_LINK_MAC_ID", "AC_LINK_PHY_ID"]
# task colors of the page (app-tokens.css, dark): Tag border fill, Tag text on it
COLORS = ["#363636", "#4d318b", "#0f5049", "#20427b", "#67320c", "#3c5d09"]
TEXT = ["#c2c2c2", "#e6daff", "#c4eee9", "#cbdfff", "#ffdcc4", "#d7efb3"]


def boot_chain():
    data = (ROOT / "samples" / "base-kit-boot-akt.bin").read_bytes()
    items, _ = akt.decode_stream(data)
    tl = timeline.build_timeline([x for x in items if isinstance(x, akt.Record)])
    sym = SymbolTable(json.loads((ROOT / "samples" / "base-kit-symbols.json").read_text()))
    ms = lambda c: tl.us(c) / 1e3  # noqa: E731
    runs = [(sym.task(e.task), ms(e.t_begin), ms(e.duration), sym.signal(e.task, e.sig))
            for e in tl.execs if ms(e.t_begin) < T1]
    posts = [(sym.task(p.src), sym.task(p.des), ms(p.t), ms(tl.execs[p.exec].t_begin) if p.exec >= 0 else None)
             for p in tl.posts if ms(p.t) < T1]
    return runs, posts


def timeline_svg(w, h):
    runs, posts = boot_chain()
    gutter, axis, row = 176, 26, (h - 26) / len(ROWS)
    x = lambda t: gutter + (t - T0) / (T1 - T0) * (w - gutter - 8)  # noqa: E731
    y = lambda name: axis + ROWS.index(name) * row  # noqa: E731
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (w, h, w, h)]
    for i in range(len(ROWS)):
        if i % 2:
            out.append('<rect x="0" y="%.1f" width="%d" height="%.1f" fill="rgba(255,255,255,0.04)"/>' % (axis + i * row, w, row))
    for t in (32.0, 32.5, 33.0, 33.5, 34.0):
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#2c2c2c"/>' % (x(t), axis, x(t), h))
        out.append('<text x="%.1f" y="17" fill="#8b8b8b" font-size="11">%.1f ms</text>' % (x(t) + 4, t))
    out.append('<line x1="%d" y1="0" x2="%d" y2="%d" stroke="#363636"/>' % (gutter, gutter, h))
    for i, name in enumerate(ROWS):
        cy = axis + i * row + row / 2
        out.append('<rect x="12" y="%.1f" width="10" height="10" rx="3" fill="%s"/>' % (cy - 5, COLORS[i]))
        out.append('<text x="30" y="%.1f" fill="#ffffff" font-size="12.5" dominant-baseline="middle">%s</text>' % (cy, name))
    for task, t, dur, sig in runs:
        if task not in ROWS:
            continue
        i = ROWS.index(task)
        x0, x1 = x(t), min(x(t + dur), w - 8)
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s"/>'
                   % (x0, y(task) + 8, max(x1 - x0, 3), row - 16, COLORS[i]))
        if x1 - x0 > 120:
            label = sig if x1 < w - 8 else "%s  %.1f ms" % (sig, dur)
            out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" dominant-baseline="middle">%s</text>'
                       % (x0 + 6, y(task) + row / 2, TEXT[i], label))
    for src, des, t, t_run in posts:
        if src not in ROWS or des not in ROWS or t_run is None:
            continue
        x0, y0, x1, y1 = x(t), y(src) + row / 2, x(t_run), y(des) + 8
        if y1 < y0:
            y1 = y(des) + row - 8
        out.append('<path d="M%.1f %.1f L%.1f %.1f" stroke="#1890ff" stroke-width="1.4" fill="none" '
                   'marker-end="url(#a)"/>' % (x0, y0, x1, y1))
    out.insert(1, '<defs><marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
                  'orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#1890ff"/></marker></defs>')
    out.append('<line id="playhead" x1="0" y1="%d" x2="0" y2="%d" stroke="#de2c2c" stroke-width="1.5" '
               'stroke-dasharray="4 3" visibility="hidden"/>' % (axis, h))
    out.append("</svg>")
    return "\n".join(out), gutter, w - 8


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="/akscope/web/css/fonts.css">
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #000; }}
#banner {{
  position: relative; width: {w}px; height: {h}px; overflow: hidden; color: #fff;
  font-family: Inter, sans-serif;
  background:
    radial-gradient(900px 500px at 78% 30%, rgba(24,144,255,0.16), transparent 60%),
    linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px) 0 0 / 32px 32px,
    linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px) 0 0 / 32px 32px,
    #0b0d11;
}}
.left {{ position: absolute; left: 72px; top: 92px; width: 520px; }}
.kicker {{ font-size: 15px; letter-spacing: 0.18em; color: #81c3ff; font-weight: 500; }}
h1 {{ margin: 18px 0 0; font-size: 104px; line-height: 1; font-weight: 600; letter-spacing: -0.02em; }}
h1 span {{ color: #1890ff; }}
.tag {{ margin-top: 22px; font-size: 27px; line-height: 1.3; color: #e9e9e9; }}
.rule {{ width: 380px; height: 2px; margin: 26px 0 20px; background: linear-gradient(90deg, #1890ff, transparent); }}
.desc {{ font-size: 17px; line-height: 1.55; color: #b8b6b6; }}
.win {{
  position: absolute; left: 620px; top: 72px; width: 600px; height: 330px;
  border: 1px solid #363636; border-radius: 12px; background: #131313; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0,0,0,0.5);
}}
.bar {{ height: 34px; display: flex; align-items: center; gap: 8px; padding: 0 14px; border-bottom: 1px solid #2c2c2c; }}
.bar i {{ width: 11px; height: 11px; border-radius: 50%; background: #363636; display: block; }}
.bar b {{ margin-left: 10px; font-weight: 500; font-size: 12.5px; color: #8b8b8b; }}
.rec {{ margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #e9e9e9; }}
.rec i {{ background: #de2c2c; }}
.flow {{ position: absolute; left: 72px; right: 60px; top: 468px; display: flex; align-items: center; gap: 14px; }}
.box {{ border: 1px solid #363636; border-radius: 10px; padding: 10px 16px; background: rgba(19,19,19,0.9); text-align: center; }}
.box b {{ display: block; font-size: 15px; font-weight: 600; letter-spacing: 0.04em; }}
.box small {{ display: block; margin-top: 3px; font-size: 12px; color: #8b8b8b; }}
.box.hl {{ border-color: #1890ff; }}
.box.hl b {{ color: #81c3ff; }}
.arr {{ flex: 1; height: 0; border-top: 2px dashed #20427b; position: relative; min-width: 24px; }}
.arr::after {{ content: ""; position: absolute; right: -2px; top: -6px; border: 5px solid transparent; border-left: 8px solid #2d7dff; }}
.cols {{ position: absolute; left: 72px; right: 60px; top: 556px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 28px; }}
.cols div {{ border-left: 2px solid #20427b; padding-left: 14px; }}
.cols b {{ display: block; font-size: 13px; letter-spacing: 0.08em; color: #81c3ff; }}
.cols span {{ display: block; margin-top: 4px; font-size: 13px; color: #b8b6b6; line-height: 1.4; }}
</style></head>
<body><div id="banner">
  <div class="left">
    <div class="kicker">STM32L151 / ACTIVE KERNEL 1.3 / AK BASE KIT</div>
    <h1>ak-<span>scope</span></h1>
    <div class="tag">Message trace and verify<br>for AK event-driven firmware</div>
    <div class="rule"></div>
    <div class="desc">Every post, handler run and timer of a running kit,<br>on a timeline, checked against the AK rules.</div>
  </div>
  <div class="win">
    <div class="bar"><i></i><i></i><i></i><b>akscope app &middot; base kit boot</b>
      <span class="rec"><i id="rec"></i>REC 460800</span></div>
    {svg}
  </div>
  <div class="flow">
    <div class="box"><b>AK BASE KIT</b><small>STM32L151, AK kernel 1.3</small></div>
    <div class="arr"></div>
    <div class="box"><b>akt TRACE</b><small>linked in, console UART</small></div>
    <div class="arr"></div>
    <div class="box hl"><b>akscope</b><small>web page or desktop app, Linux</small></div>
    <div class="arr"></div>
    <div class="box"><b>VERIFY</b><small>13 static, 9 runtime rules</small></div>
  </div>
  <div class="cols">
    <div><b>RECORD</b><span>live from the kit, or flash and record from boot</span></div>
    <div><b>TIMELINE</b><span>who posted what, queue wait, run time</span></div>
    <div><b>FLOW</b><span>messages as a sequence diagram</span></div>
    <div><b>VERIFY</b><span>rule checks, text or JSON for CI</span></div>
  </div>
</div></body></html>
"""


def main():
    svg, x_first, x_last = timeline_svg(600, 330 - 34)
    page = PAGE.format(w=W, h=H, svg=svg)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    (build / "banner.html").write_text(page)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    import compare
    from PIL import Image

    wd = compare.WebDriver()
    frames = []
    try:
        wd.call("POST", wd.s + "/window/rect", {"width": W + 40, "height": H + 160})
        wd.call("POST", wd.s + "/url", {"url": "http://127.0.0.1:%d/build/banner.html" % httpd.server_address[1]})
        wd.js("return document.fonts.ready.then(() => true)")
        time.sleep(1)
        el = wd.call("POST", wd.s + "/element", {"using": "css selector", "value": "#banner"})
        eid = next(iter(el.values()))

        def shot():
            png = base64.b64decode(wd.call("GET", wd.s + "/element/%s/screenshot" % eid))
            return Image.open(io.BytesIO(png)).convert("RGB")

        OUT.mkdir(parents=True, exist_ok=True)
        shot().save(OUT / "banner-ak-scope-2x1.png", optimize=True)
        for n in range(FRAMES):
            x = x_first + (x_last - x_first) * n / (FRAMES - 1)
            wd.js("const p = document.getElementById('playhead'); p.setAttribute('x1', %.1f); p.setAttribute('x2', %.1f);"
                  "p.setAttribute('visibility', 'visible'); document.getElementById('rec').style.opacity = %s;"
                  % (x, x, 1 if (n // 3) % 2 == 0 else 0.25))
            frames.append(shot())
    finally:
        wd.close()
        httpd.shutdown()

    # the task colors cover few pixels; a strip of them makes sure the palette keeps them exact
    swatch = Image.new("RGB", (W, H + 40), "#000000")
    swatch.paste(frames[0], (0, 0))
    for i, c in enumerate(COLORS + TEXT + ["#1890ff", "#de2c2c", "#81c3ff"]):
        swatch.paste(Image.new("RGB", (40, 40), c), (i * 40, H))
    palette = swatch.quantize(colors=255, method=Image.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.NONE) for f in frames]
    gif[0].save(OUT / "banner-ak-scope.gif", save_all=True, append_images=gif[1:], duration=FRAME_MS,
                loop=0, optimize=True)
    for f in ("banner-ak-scope.gif", "banner-ak-scope-2x1.png"):
        print(OUT / f, (OUT / f).stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
