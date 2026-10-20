"""Local web UI server. Standard library only; listens on 127.0.0.1.

GET  /                 web/index.html and the other files under web/
GET  /api/trace        the trace given on the command line, as timeline JSON (204 if none)
POST /api/decode       body is a .akt or raw capture picked in the browser, returns timeline JSON
                       (the server never reads paths sent by the page)
"""

import json
import mimetypes
import pathlib
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from akscope.core import akt, timeline
from akscope.core.aktfile import MAGIC, read_capture
from akscope.core.symbols import SymbolTable

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8730
MAX_UPLOAD = 64 * 1024 * 1024

mimetypes.add_type("text/javascript", ".js")


def decode_capture(header, data, sym=None, name=""):
    records, text, stats = akt.decode_bytes(data)
    if sym is None:
        sym = SymbolTable(header.get("symbols"))
    tl = timeline.build_timeline(records)
    out = timeline.to_json(tl, sym, text=text, stream_stats=stats)
    out["name"] = name
    out["header"] = {k: v for k, v in header.items() if k != "symbols"}
    out["has_symbols"] = bool(sym.tasks)
    return out


def split_blob(blob):
    """Same as read_capture() for bytes already in memory."""
    if not blob.startswith(MAGIC):
        return {}, blob
    n = int.from_bytes(blob[len(MAGIC):len(MAGIC) + 4], "little")
    start = len(MAGIC) + 4
    return json.loads(blob[start:start + n].decode()), blob[start + n:]


class State:
    def __init__(self, trace_path=None, sym=None):
        self.trace_path = trace_path
        self.sym = sym
        self.lock = threading.Lock()
        self._cache = None

    def trace_json(self):
        if not self.trace_path:
            return None
        with self.lock:
            if self._cache is None:
                header, data = read_capture(self.trace_path)
                self._cache = json.dumps(decode_capture(header, data, self.sym,
                                                        pathlib.Path(self.trace_path).name))
            return self._cache


class Handler(BaseHTTPRequestHandler):
    server_version = "akscope"
    state = None  # set by make_server

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body=b"", ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, msg):
        self._send(status, json.dumps({"error": msg}).encode())

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/trace":
            body = self.state.trace_json()
            if body is None:
                self._send(HTTPStatus.NO_CONTENT)
            else:
                self._send(HTTPStatus.OK, body.encode())
            return

        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (WEB_DIR / rel).resolve()
        # keep requests inside web/
        if WEB_DIR not in f.parents or not f.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, f.read_bytes(), ctype)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/decode":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            self._error(HTTPStatus.BAD_REQUEST, "empty or too large")
            return
        blob = self.rfile.read(n)
        name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name", [""])[0]
        try:
            header, data = split_blob(blob)
            out = decode_capture(header, data, self.state.sym, name)
        except (ValueError, KeyError) as e:
            self._error(HTTPStatus.BAD_REQUEST, "cannot decode: %s" % e)
            return
        self._send(HTTPStatus.OK, json.dumps(out).encode())


def make_server(state, port=DEFAULT_PORT):
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
