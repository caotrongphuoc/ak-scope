"""Local web UI server. Standard library only; listens on 127.0.0.1.

GET  /                 web/index.html and the other files under web/
GET  /api/trace        the trace given on the command line, as timeline JSON (204 if none)
POST /api/decode       body is a .akt or raw capture picked in the browser, returns timeline JSON
                       (the server never reads paths sent by the page)
GET  /api/config       version and what this server can do: flash target, default port and baud
GET  /api/ports        serial ports on this machine
GET  /api/live         Server-Sent Events: status, snapshot, delta
POST /api/record/start {"port", "baud"}, JSON body
POST /api/record/stop
POST /api/flash        {"port", "baud"}, flashes the --project firmware and records from boot
POST /api/verify       {"source": "current" | "live"}: runtime rules on the open trace or the
                       live capture, plus the static rules of --project (run once, in the
                       background; the reply says when they are still running)

Every /api/ request must carry Host (and Origin when present) of this server, and the control
POSTs must be application/json. A web page from another site cannot then drive the kit:
its fetch() would need a CORS preflight that this server never answers, and DNS rebinding
fails the Host check.
"""

import hashlib
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

WEB_DIR = pathlib.Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8730
MAX_UPLOAD = 64 * 1024 * 1024
KEEPALIVE_S = 15

# decoded page JSON of opened captures, so opening a large trace again is instant
DECODED_KEEP = 20
DECODED_FORMAT = "1"  # change when the page JSON changes shape

mimetypes.add_type("text/javascript", ".js")


def decode_capture(header, data, sym=None, name=""):
    """(timeline JSON for the page, (Timeline, SymbolTable, stream stats) for verify)"""
    records, text, stats = akt.decode_bytes(data)
    if sym is None:
        sym = SymbolTable(header.get("symbols"))
    tl = timeline.build_timeline(records)
    out = timeline.to_json(tl, sym, text=text, stream_stats=stats)
    out["name"] = name
    out["header"] = {k: v for k, v in header.items() if k != "symbols"}
    out["has_symbols"] = bool(sym.tasks)
    return out, (tl, sym, stats)


def _decoded_dir():
    from akscope.build import CACHE_ROOT
    return CACHE_ROOT / "decoded"


def _cache_key(data, header, sym, name):
    h = hashlib.sha1(DECODED_FORMAT.encode())
    h.update(data)
    h.update(json.dumps(header, sort_keys=True, default=str).encode())
    if sym is not None:
        h.update(json.dumps([sym.tasks, sym.signals, sym.fallback], sort_keys=True, default=str).encode())
    h.update(name.encode())
    return h.hexdigest()


def _prune(folder, keep):
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        f.unlink(missing_ok=True)


def split_blob(blob):
    """Same as read_capture() for bytes already in memory."""
    if not blob.startswith(MAGIC):
        return {}, blob
    n = int.from_bytes(blob[len(MAGIC):len(MAGIC) + 4], "little")
    start = len(MAGIC) + 4
    return json.loads(blob[start:start + n].decode()), blob[start + n:]


class State:
    def __init__(self, trace_path=None, sym=None, live=None, port=None, baud=115200,
                 project=None, rules_path=None, base=None):
        self.trace_path = trace_path
        self.sym = sym
        self.live = live
        self.port = port
        self.baud = baud
        self.project = project
        self.rules_path = rules_path
        self.base = base
        self.lock = threading.Lock()
        self._cache = None
        self.current = None  # (Timeline, SymbolTable, stream stats) of the trace on the page
        self._current_src = None  # (header, data, sym, name) to build current on demand
        self._static = None  # StaticResult once done
        self._static_state = "none" if not project else "idle"
        self._static_error = None

    def static_result(self):
        """(state, StaticResult or None, error). Starts the static run the first time."""
        from akscope import verify

        with self.lock:
            if self._static_state == "idle":
                self._static_state = "running"

                def run():
                    try:
                        res = verify.run_static(self.project, base=self.base, log=lambda m: None)
                        with self.lock:
                            self._static, self._static_state = res, "done"
                    except Exception as e:  # build or preprocess failure, shown on the page
                        with self.lock:
                            self._static_error, self._static_state = str(e), "failed"

                threading.Thread(target=run, daemon=True).start()
            return self._static_state, self._static, self._static_error

    def verify(self, source):
        from akscope import verify

        rules = verify.load_rules(self.rules_path)
        state, static, error = self.static_result()
        findings = list(static.findings) if static else []
        handled = static.handled if static else None
        target = None
        if source == "live" and self.live is not None and self.live.builder is not None:
            with self.live.lock:
                target = (self.live.builder.tl, self.live.sym, self.live.decoder.stats)
                findings += verify.run_runtime(*target[:2], rules, handled, target[2])
        elif self._current_timeline() is not None:
            tl, sym, stats = self.current
            findings += verify.run_runtime(tl, sym, rules, handled, stats)
        findings = verify.ordered(verify.apply_ignore(findings, rules))
        errors, warnings = verify.summary(findings)
        return {"static": state, "static_error": error, "errors": errors, "warnings": warnings,
                "findings": [f.to_dict() for f in findings]}

    def config(self):
        from akscope import __version__
        return {
            "version": __version__,
            "flash_target": str(self.live.flash_target) if self.live and self.live.flash_target else None,
            "live": self.live is not None,
            "verify_static": self.project is not None,
            "port": self.port,
            "baud": self.baud,
        }

    def open(self, header, data, name):
        """Page JSON (a str) for a capture, from the decoded cache when it was opened before.
        The Timeline for verify is only built when verify asks for it."""
        with self.lock:
            self.current = None
            self._current_src = (header, data, self.sym, name)
            folder = _decoded_dir()
            path = folder / (_cache_key(data, header, self.sym, name) + ".json")
            if path.is_file():
                path.touch()
                return path.read_text()
            out, self.current = decode_capture(header, data, self.sym, name)
            text = json.dumps(out, separators=(",", ":"))
            try:
                folder.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(text)
                tmp.replace(path)
                _prune(folder, DECODED_KEEP)
            except OSError:
                pass  # no cache, still served
            return text

    def _current_timeline(self):
        with self.lock:
            if self.current is None and self._current_src is not None:
                header, data, sym, name = self._current_src
                self.current = decode_capture(header, data, sym, name)[1]
            return self.current

    def trace_json(self):
        if not self.trace_path:
            return None
        if self._cache is None:
            header, data = read_capture(self.trace_path)
            self._cache = self.open(header, data, pathlib.Path(self.trace_path).name)
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
        if path == "/api/verify":
            body = self._read_json()
            if body is None:
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send a JSON body")
                return
            try:
                self._json(self.state.verify(body.get("source", "current")))
            except (OSError, ValueError) as e:
                self._error(HTTPStatus.BAD_REQUEST, "verify: %s" % e)
            except Exception as e:  # report to the page instead of dropping the connection
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "verify failed: %s: %s" % (type(e).__name__, e))
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
            body = self.state.open(header, data, name)
        except (ValueError, KeyError) as e:
            self._error(HTTPStatus.BAD_REQUEST, "cannot decode: %s" % e)
            return
        self._send(HTTPStatus.OK, body.encode())


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
