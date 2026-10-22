"""Read a Figma .fig file and draw its nodes as SVG.

A .fig file is a zip with canvas.fig inside: "fig-kiwi", a version, then length-prefixed
chunks. Chunk 0 is the kiwi schema (raw deflate), chunk 1 the document (zstd or deflate),
encoded with that schema. Geometry is stored ready to draw: every node keeps its fill and
stroke outlines (fillGeometry, strokeGeometry) and every text node its glyph outlines, as
path commands in the document's blobs. Drawing those gives the shapes Figma itself shows,
without a layout engine and without the fonts installed.

Only what the ak-scope design system uses is handled: solid and linear gradient paints,
drop shadows, clipping frames, instances with their derived (resized, overridden) data.

Used by export.py and compare.py. Needs `zstd` on the PATH.
"""

import math
import re
import struct
import subprocess
import zipfile
import zlib

# kiwi -----------------------------------------------------------------------------------------

_BUILTIN = {-1: "bool", -2: "byte", -3: "int", -4: "uint", -5: "float", -6: "string",
            -7: "int64", -8: "uint64"}


class _Reader:
    def __init__(self, b):
        self.b = b
        self.o = 0

    def byte(self):
        v = self.b[self.o]
        self.o += 1
        return v

    def vu(self, limit=35):
        v = sh = 0
        while True:
            c = self.byte()
            v |= (c & 127) << sh
            sh += 7
            if not (c & 128) or sh >= limit:
                return v

    def vi(self):
        v = self.vu()
        return (v >> 1) ^ -(v & 1)

    def f32(self):
        # kiwi floats: 0 is one zero byte, else the IEEE bits rotated left by 9
        if self.b[self.o] == 0:
            self.o += 1
            return 0.0
        bits = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        bits = ((bits << 23) | (bits >> 9)) & 0xFFFFFFFF
        return struct.unpack("<f", struct.pack("<I", bits))[0]

    def string(self):
        e = self.b.index(0, self.o)
        v = self.b[self.o:e].decode("utf8", "replace")
        self.o = e + 1
        return v


def _schema(b):
    r = _Reader(b)
    defs = []
    for _ in range(r.vu()):
        name = r.string()
        kind = r.byte()
        fields = []
        for _ in range(r.vu()):
            fields.append((r.string(), r.vi(), r.byte(), r.vu()))
        defs.append((name, kind, fields))
    return defs


def _decode(defs, r, d):
    name, kind, fields = d
    if kind == 0:  # enum
        v = r.vu()
        for fname, _, _, val in fields:
            if val == v:
                return fname
        return v
    if kind == 1:  # struct
        return {f[0]: _field(defs, r, f) for f in fields}
    out = {}  # message
    byval = {f[3]: f for f in fields}
    while True:
        k = r.vu()
        if k == 0:
            return out
        f = byval[k]
        out[f[0]] = _field(defs, r, f)


def _one(defs, r, t):
    if t < 0:
        n = _BUILTIN[t]
        if n in ("bool", "byte"):
            return r.byte()
        if n == "int":
            return r.vi()
        if n == "uint":
            return r.vu()
        if n == "float":
            return r.f32()
        if n == "string":
            return r.string()
        v = r.vu(70)
        return (v >> 1) ^ -(v & 1) if n == "int64" else v
    return _decode(defs, r, defs[t])


def _field(defs, r, f):
    _, t, arr, _ = f
    if arr:
        n = r.vu()
        if t == -2:
            v = bytes(r.b[r.o:r.o + n])
            r.o += n
            return v
        return [_one(defs, r, t) for _ in range(n)]
    return _one(defs, r, t)


def _unpack(chunk):
    if chunk[:4] == b"\x28\xb5\x2f\xfd":
        return subprocess.run(["zstd", "-dc"], input=chunk, capture_output=True, check=True).stdout
    return zlib.decompress(chunk, -15)


def load_message(path):
    with zipfile.ZipFile(path) as z:
        b = z.read("canvas.fig")
    if not b.startswith(b"fig-kiwi"):
        raise ValueError("%s: not a fig-kiwi file" % path)
    o = 12
    chunks = []
    while o < len(b):
        n = struct.unpack_from("<I", b, o)[0]
        chunks.append(b[o + 4:o + 4 + n])
        o += 4 + n
    defs = _schema(_unpack(chunks[0]))
    root = next(d for d in defs if d[0] == "Message")
    return _decode(defs, _Reader(_unpack(chunks[1])), root)


# document -------------------------------------------------------------------------------------

def gid(g):
    return "%d:%d" % (g["sessionID"], g["localID"])


class Doc:
    def __init__(self, path):
        m = load_message(path)
        self.blobs = [b["bytes"] for b in m.get("blobs", [])]
        self.nodes = {}
        self.children = {}
        for n in m["nodeChanges"]:
            if "guid" not in n:
                continue
            self.nodes[gid(n["guid"])] = n
        for n in self.nodes.values():
            p = n.get("parentIndex")
            if p:
                self.children.setdefault(gid(p["guid"]), []).append(n)
        for kids in self.children.values():
            kids.sort(key=lambda n: n["parentIndex"].get("position", ""))
        self.variables = {k: n for k, n in self.nodes.items() if n.get("type") == "VARIABLE"}
        self.default_mode = {}
        for k, n in self.nodes.items():
            if n.get("type") == "VARIABLE_SET" and n.get("variableSetModes"):
                self.default_mode[k] = gid(n["variableSetModes"][0]["id"])

    def resolve(self, var_guid, modes, depth=0):
        """Value of a variable in the given modes {set guid: mode guid}, following aliases."""
        v = self.variables.get(gid(var_guid))
        if v is None or depth > 16 or "guid" not in (v.get("variableSetID") or {}):
            return None
        set_id = gid(v["variableSetID"]["guid"])
        mode = modes.get(set_id) or self.default_mode.get(set_id)
        entries = (v.get("variableDataValues") or {}).get("entries", [])
        entry = next((e for e in entries if gid(e["modeID"]) == mode), entries[0] if entries else None)
        if entry is None:
            return None
        value = entry["variableData"].get("value", {})
        if "alias" in value and "guid" in value["alias"]:
            return self.resolve(value["alias"]["guid"], modes, depth + 1)
        return value

    def kids(self, n):
        return self.children.get(gid(n["guid"]), [])

    def walk(self, n):
        yield n
        for c in self.kids(n):
            yield from self.walk(c)

    def find(self, pred):
        return [n for n in self.nodes.values() if pred(n)]

    def page(self, name_part):
        return next(n for n in self.nodes.values()
                    if n.get("type") == "CANVAS" and name_part.lower() in n.get("name", "").lower())


# geometry -------------------------------------------------------------------------------------

def path_d(blob):
    """SVG path data from a commandsBlob: op byte, then little endian float32 coordinates.
    1 move, 2 line, 3 quad, 4 cubic, 0 close."""
    out = []
    o = 0
    n = len(blob)
    while o < n:
        op = blob[o]
        o += 1
        if op == 0:
            # glyph blobs start with a close; SVG rejects a Z before the first move
            if out:
                out.append("Z")
            continue
        cnt = {1: 2, 2: 2, 3: 4, 4: 6}.get(op)
        if cnt is None:
            break
        vals = struct.unpack_from("<%df" % cnt, blob, o)
        o += 4 * cnt
        out.append("MLQC"[op - 1] + " ".join("%.3f" % v for v in vals))
    return " ".join(out)


def matrix(t):
    if not t:
        return (1, 0, 0, 1, 0, 0)
    return (t.get("m00", 1), t.get("m10", 0), t.get("m01", 0), t.get("m11", 1), t.get("m02", 0),
            t.get("m12", 0))


def _fmt_matrix(m):
    return "matrix(%s)" % " ".join("%.4f" % v for v in m)


def color_hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c[k] * 255))) for k in ("r", "g", "b"))


# svg rendering --------------------------------------------------------------------------------

class Renderer:
    def __init__(self, doc):
        self.doc = doc
        self.defs = []
        self.uid = 0
        self.modes = {}  # variable set guid -> mode guid, set by the frames being drawn
        self.props = {}  # component property values of the instance being drawn

    def _id(self, prefix):
        self.uid += 1
        return "%s%d" % (prefix, self.uid)

    def paint(self, p, size):
        """(fill attribute value, opacity) for one paint, or None if not drawable."""
        if not p.get("visible", 1):
            return None
        op = p.get("opacity", 1.0)
        t = p.get("type")
        if t == "SOLID":
            c = p.get("color", {"r": 0, "g": 0, "b": 0, "a": 1})
            alias = ((p.get("colorVar") or {}).get("value") or {}).get("alias") or {}
            if "guid" in alias:
                # bound to a variable: its value in the modes of the enclosing frames. Figma
                # copies the variable's alpha into the paint opacity, so do not apply it twice.
                v = self.doc.resolve(alias["guid"], self.modes)
                if v and "colorValue" in v:
                    c = v["colorValue"]
                    return color_hex(c), c.get("a", 1)
            return color_hex(c), op * c.get("a", 1)
        if t in ("GRADIENT_LINEAR", "GRADIENT_RADIAL"):
            gid_ = self._id("g")
            stops = "".join('<stop offset="%.4f" stop-color="%s" stop-opacity="%.4f"/>'
                            % (s["position"], color_hex(s["color"]), s["color"].get("a", 1))
                            for s in p.get("stops", []))
            # gradient transform maps the node box to the unit gradient space; invert it
            m = matrix(p.get("transform"))
            inv = _invert(m)
            w, h = size.get("x", 1), size.get("y", 1)
            if t == "GRADIENT_LINEAR":
                x1, y1 = _apply(inv, 0, 0.5)
                x2, y2 = _apply(inv, 1, 0.5)
                self.defs.append('<linearGradient id="%s" gradientUnits="userSpaceOnUse" x1="%.3f" y1="%.3f" '
                                 'x2="%.3f" y2="%.3f">%s</linearGradient>'
                                 % (gid_, x1 * w, y1 * h, x2 * w, y2 * h, stops))
            else:
                cx, cy = _apply(inv, 0.5, 0.5)
                ex, ey = _apply(inv, 1, 0.5)
                r = math.hypot((ex - cx) * w, (ey - cy) * h)
                self.defs.append('<radialGradient id="%s" gradientUnits="userSpaceOnUse" cx="%.3f" cy="%.3f" '
                                 'r="%.3f">%s</radialGradient>' % (gid_, cx * w, cy * h, r, stops))
            return "url(#%s)" % gid_, op
        if t == "IMAGE":
            return "#808080", op * 0.3
        return None

    def shapes(self, n, geometry, paints):
        out = []
        size = n.get("size", {})
        for p in paints or []:
            pt = self.paint(p, size)
            if not pt:
                continue
            for g in geometry or []:
                blob = self.doc.blobs[g["commandsBlob"]] if isinstance(g.get("commandsBlob"), int) else None
                if not blob:
                    continue
                rule = "evenodd" if g.get("windingRule") == "ODD" else "nonzero"
                out.append('<path d="%s" fill="%s" fill-opacity="%.4f" fill-rule="%s"/>'
                           % (path_d(blob), pt[0], pt[1], rule))
        return out

    def text(self, n, derived=None):
        d = derived or n.get("derivedTextData") or {}
        out = []
        for p in n.get("fillPaints") or []:
            pt = self.paint(p, n.get("size", {}))
            if not pt:
                continue
            for g in d.get("glyphs", []):
                blob = self.doc.blobs[g["commandsBlob"]] if isinstance(g.get("commandsBlob"), int) else None
                if not blob:
                    continue
                fs = g.get("fontSize", n.get("fontSize", 12))
                pos = g.get("position", {"x": 0, "y": 0})
                # glyph outlines are in em units with y up from the baseline
                out.append('<path transform="translate(%.3f %.3f) scale(%.4f %.4f)" d="%s" fill="%s" '
                           'fill-opacity="%.4f"/>' % (pos["x"], pos["y"], fs, -fs, path_d(blob), pt[0], pt[1]))
            break
        return out

    def effects(self, n):
        shadows = [e for e in n.get("effects") or [] if e.get("type") == "DROP_SHADOW" and e.get("visible", 1)]
        if not shadows:
            return None
        fid = self._id("f")
        # plain primitives instead of feDropShadow, which older librsvg leaves empty
        parts, merge = [], []
        for i, e in enumerate(shadows):
            c = e.get("color", {})
            off = e.get("offset", {"x": 0, "y": 0})
            parts.append('<feGaussianBlur in="SourceAlpha" stdDeviation="%.2f"/><feOffset dx="%.2f" dy="%.2f" result="o%d"/>'
                         '<feFlood flood-color="%s" flood-opacity="%.4f"/><feComposite in2="o%d" operator="in" result="s%d"/>'
                         % (e.get("radius", 0) / 2, off["x"], off["y"], i, color_hex(c), c.get("a", 1), i, i))
            merge.append('<feMergeNode in="s%d"/>' % i)
        parts.append("<feMerge>%s<feMergeNode in=\"SourceGraphic\"/></feMerge>" % "".join(merge))
        self.defs.append('<filter id="%s" x="-50%%" y="-50%%" width="200%%" height="200%%">%s</filter>' % (fid, "".join(parts)))
        return fid

    def node(self, n, override=None, master=None):
        """SVG for n and its children. override: derived data from an enclosing instance."""
        if not n.get("visible", 1):
            return ""
        ov = override or {}
        if ov.get("visible") == 0:
            return ""
        for ref in n.get("componentPropRefs") or []:
            if ref.get("componentPropNodeField") == "VISIBLE":
                value = self.props.get(gid(ref["defID"]))
                if value is not None and not value.get("boolValue", 1):
                    return ""
        saved_modes = self.modes
        entries = (n.get("variableModeBySetMap") or {}).get("entries", [])
        if entries:
            self.modes = dict(self.modes)
            for e in entries:
                self.modes[gid(e["variableSetID"]["guid"])] = gid(e["variableModeID"])
        try:
            return self._node(n, ov, master)
        finally:
            self.modes = saved_modes

    def _node(self, n, ov, master):
        t = n.get("type")
        m = matrix(ov.get("transform") or n.get("transform"))
        body = []
        if t == "TEXT":
            body += self.text(n, ov.get("derivedTextData"))
        elif t not in ("CANVAS", "DOCUMENT"):
            body += self.shapes(n, ov.get("fillGeometry") or n.get("fillGeometry"),
                                ov.get("fillPaints") or n.get("fillPaints"))
        kids_svg = []
        if t == "INSTANCE":
            kids_svg.append(self.instance(n, ov, master))
        elif t == "BOOLEAN_OPERATION":
            # the children are the operands; fillGeometry is the result
            pass
        else:
            for c in self.doc.kids(n):
                kids_svg.append(self.node(c, self._derived_for(master, c), master))
        if t != "TEXT":
            body_strokes = self.shapes(n, ov.get("strokeGeometry") or n.get("strokeGeometry"),
                                       ov.get("strokePaints") or n.get("strokePaints"))
        else:
            body_strokes = []
        if body_strokes:
            body_strokes = self._align_strokes(n, ov, body_strokes)
        clip = ""
        if t in ("FRAME", "SYMBOL", "INSTANCE") and not n.get("frameMaskDisabled") and kids_svg:
            geom = ov.get("fillGeometry") or n.get("fillGeometry")
            if geom and isinstance(geom[0].get("commandsBlob"), int):
                cid = self._id("c")
                self.defs.append('<clipPath id="%s"><path d="%s"/></clipPath>' % (cid, path_d(self.doc.blobs[geom[0]["commandsBlob"]])))
                clip = ' clip-path="url(#%s)"' % cid
        fid = self.effects(n)
        filt = ' filter="url(#%s)"' % fid if fid else ""
        op = ov.get("opacity", n.get("opacity", 1.0))
        opa = ' opacity="%.4f"' % op if op < 1 else ""
        inner = "".join(body) + ("<g%s>%s</g>" % (clip, "".join(kids_svg)) if kids_svg else "") + "".join(body_strokes)
        return '<g transform="%s"%s%s>%s</g>' % (_fmt_matrix(m), filt, opa, inner)

    def _align_strokes(self, n, ov, strokes):
        """strokeGeometry of an inside or outside stroke is a centered stroke twice as wide;
        Figma keeps the half inside (or outside) of the fill shape"""
        align = n.get("strokeAlign", "CENTER")
        geom = ov.get("fillGeometry") or n.get("fillGeometry")
        if align == "CENTER" or not geom or not isinstance(geom[0].get("commandsBlob"), int):
            return strokes
        d = path_d(self.doc.blobs[geom[0]["commandsBlob"]])
        cid = self._id("c")
        if align == "INSIDE":
            self.defs.append('<clipPath id="%s"><path d="%s"/></clipPath>' % (cid, d))
            return ['<g clip-path="url(#%s)">%s</g>' % (cid, "".join(strokes))]
        size = n.get("size", {})
        w, h = size.get("x", 0), size.get("y", 0)
        m = max(w, h) + 2 * n.get("strokeWeight", 1) + 2
        self.defs.append('<mask id="%s" maskUnits="userSpaceOnUse" x="%.3f" y="%.3f" width="%.3f" height="%.3f">'
                         '<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#fff"/><path d="%s" fill="#000"/></mask>'
                         % (cid, -m, -m, w + 2 * m, h + 2 * m, -m, -m, w + 2 * m, h + 2 * m, d))
        return ['<g mask="url(#%s)">%s</g>' % (cid, "".join(strokes))]

    OVERRIDDEN = ("visible", "fillPaints", "strokePaints", "opacity", "componentPropAssignments", "overriddenSymbolID")

    def instance(self, n, ov, outer=None):
        sd = n.get("symbolData") or {}
        sym_id = sd.get("symbolID")
        # an instance swap, by an override or by a property of the enclosing instance
        if ov.get("overriddenSymbolID"):
            sym_id = ov["overriddenSymbolID"]
        for ref in n.get("componentPropRefs") or []:
            if ref.get("componentPropNodeField") == "OVERRIDDEN_SYMBOL_ID":
                value = self.props.get(gid(ref["defID"])) or {}
                if (value.get("symbolIdValue") or {}).get("guid"):
                    sym_id = value["symbolIdValue"]["guid"]
        sym = self.doc.nodes.get(gid(sym_id)) if sym_id else None
        if not sym:
            return ""
        # derived data of this instance, keyed by the guid path of the node inside the symbol
        derived = {}

        def merge(path, o, keys):
            merged = dict(derived.get(path, {}))
            for k in keys:
                if k in o:
                    merged[k] = o[k]
            derived[path] = merged

        for d in n.get("derivedSymbolData") or []:
            path = tuple(gid(g) for g in (d.get("guidPath") or {}).get("guids", []))
            if path:
                derived[path] = d
        props = {gid(a["defID"]): _prop_value(a) for a in n.get("componentPropAssignments") or []}
        for o in sd.get("symbolOverrides") or []:
            path = tuple(gid(g) for g in (o.get("guidPath") or {}).get("guids", []))
            if path:
                merge(path, o, self.OVERRIDDEN)
        # an enclosing instance reaches into this one with longer guid paths
        if outer:
            keys = {gid(n["guid"])} | ({gid(n["overrideKey"])} if n.get("overrideKey") else set())
            for path, o in outer.items():
                if len(path) > 1 and path[0] in keys:
                    merge(path[1:], o, tuple(o.keys()))
            for path, o in outer.items():
                if len(path) == 1 and path[0] in keys:
                    for a in o.get("componentPropAssignments") or []:
                        props[gid(a["defID"])] = _prop_value(a)
        saved = self.props
        self.props = props
        try:
            out = []
            for c in self.doc.kids(sym):
                out.append(self.node(c, self._derived_for(derived, c), derived))
        finally:
            self.props = saved
        return "".join(out)

    @staticmethod
    def _derived_for(derived, child):
        if not derived:
            return None
        keys = [gid(child["guid"])]
        if child.get("overrideKey"):
            keys.append(gid(child["overrideKey"]))
        for k in keys:
            d = derived.get((k,))
            if d is not None:
                return d
        return None

    def svg(self, n, pad=0, background=None):
        """A standalone SVG document of node n at its own size (its transform is ignored)."""
        size = n.get("size", {"x": 0, "y": 0})
        w, h = size["x"] + 2 * pad, size["y"] + 2 * pad
        self.defs = []
        saved = n.get("transform")
        n = dict(n)
        n["transform"] = {"m00": 1, "m01": 0, "m02": pad, "m10": 0, "m11": 1, "m12": pad}
        body = self.node(n)
        n["transform"] = saved
        bg = '<rect width="100%%" height="100%%" fill="%s"/>' % background if background else ""
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" viewBox="0 0 %.3f %.3f">'
                '<defs>%s</defs>%s%s</svg>' % (math.ceil(w), math.ceil(h), w, h, "".join(self.defs), bg, body))


def _prop_value(assignment):
    """the value of a component property assignment; newer files keep it in varValue"""
    v = assignment.get("value") or {}
    if not v:
        v = ((assignment.get("varValue") or {}).get("value")) or {}
    return v


def _invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c or 1e-9
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def svg_to_png(svg_text, path, scale=1):
    """Draw an SVG with librsvg (python3-gi) into a PNG file."""
    import cairo
    import gi
    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg

    handle = Rsvg.Handle.new_from_data(svg_text.encode())
    w = int(float(re.search(r'width="([\d.]+)"', svg_text).group(1)) * scale)
    h = int(float(re.search(r'height="([\d.]+)"', svg_text).group(1)) * scale)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(w, 1), max(h, 1))
    ctx = cairo.Context(surf)
    ctx.scale(scale, scale)
    handle.render_cairo(ctx)
    surf.write_to_png(str(path))
