"""Local web UI server. Standard library only; listens on 127.0.0.1.

GET  /                 web/index.html and the other files under web/
GET  /api/trace        the trace given on the command line, as timeline JSON (204 if none)
POST /api/decode       body is a .akt or raw capture picked in the browser, returns timeline JSON
                       (the server never reads paths sent by the page)
GET  /api/config       what this server can do: flash target, default port and baud
GET  /api/ports        serial ports on this machine
GET  /api/live         Server-Sent Events: status, snapshot, delta
POST /api/record/start {"port", "baud"}, JSON body
POST /api/record/stop
POST /api/flash        {"port", "baud"}, flashes the --project firmware and records from boot

Every /api/ request must carry Host (and Origin when present) of this server, and the control
POSTs must be application/json. A web page from another site cannot then drive the kit:
its fetch() would need a CORS preflight that this server never answers, and DNS rebinding
fails the Host check.
"""

import json
import mimetypes
import pathlib
import queue
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
KEEPALIVE_S = 15

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
    def __init__(self, trace_path=None, sym=None, live=None, port=None, baud=115200):
        self.trace_path = trace_path
        self.sym = sym
        self.live = live
        self.port = port
        self.baud = baud
        self.lock = threading.Lock()
        self._cache = None

    def config(self):
        return {
            "flash_target": str(self.live.flash_target) if self.live and self.live.flash_target else None,
            "live": self.live is not None,
            "port": self.port,
            "baud": self.baud,
        }

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

    def _json(self, data):
        self._send(HTTPStatus.OK, json.dumps(data).encode())

    def _same_origin(self):
        port = self.server.server_address[1]
        allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {"http://" + a for a in allowed}

    def _read_json(self):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _sse(self):
        live = self.state.live
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        v = live.add_viewer()
        try:
            while True:
                try:
                    event, data = v.events.get(timeout=KEEPALIVE_S)
                    msg = "event: %s\ndata: %s\n\n" % (event, json.dumps(data))
                except queue.Empty:
                    msg = ": keepalive\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            live.remove_viewer(v)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/") and not self._same_origin():
            self._error(HTTPStatus.FORBIDDEN, "wrong host or origin")
            return
        if path == "/api/config":
            self._json(self.state.config())
            return
        if path == "/api/ports":
            try:
                from serial.tools import list_ports
                # legacy ttyS ports with nothing behind them (hwid n/a) only clutter the list
                ports = [{"device": p.device, "description": p.description}
                         for p in sorted(list_ports.comports(), key=lambda p: p.device)
                         if p.hwid != "n/a" or p.device == self.state.port]
            except ImportError:
                ports = []
            self._json(ports)
            return
        if path == "/api/live":
            if self.state.live is None:
                self._error(HTTPStatus.NOT_FOUND, "live capture not enabled")
                return
            self._sse()
            return
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
        if not self._same_origin():
            self._error(HTTPStatus.FORBIDDEN, "wrong host or origin")
            return
        if path in ("/api/record/start", "/api/record/stop", "/api/flash"):
            self._control(path)
            return
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


    def _control(self, path):
        live = self.state.live
        if live is None:
            self._error(HTTPStatus.NOT_FOUND, "live capture not enabled")
            return
        body = self._read_json()
        if body is None:
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send a JSON body")
            return
        if path == "/api/record/stop":
            live.stop()
            self._json({"ok": True})
            return
        port = body.get("port") or self.state.port
        baud = int(body.get("baud") or self.state.baud)
        if not port:
            self._error(HTTPStatus.BAD_REQUEST, "no port")
            return
        # only ports this machine lists, the page cannot open arbitrary device files
        try:
            from serial.tools import list_ports
            known = {p.device for p in list_ports.comports()}
        except ImportError:
            known = set()
        if port not in known:
            self._error(HTTPStatus.BAD_REQUEST, "unknown port %s" % port)
            return
        if path == "/api/flash":
            ok = live.flash(port, baud)
        else:
            ok = live.start(port, baud)
        self._json({"ok": bool(ok), "status": live.status})


def make_server(state, port=DEFAULT_PORT):
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
