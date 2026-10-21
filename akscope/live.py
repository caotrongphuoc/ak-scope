"""A live capture for the web UI: one serial port, one .akt file, any number of viewers.

A reader thread owns the port. It writes every byte to the capture file, decodes frames and
feeds a TimelineBuilder. Each viewer has its own cursor; every TICK_S it gets the records
added since its cursor, so a viewer that connects late gets one snapshot and then deltas,
never the same record twice.

Flash stops the capture, runs ak-flash (with retries) on the same port and starts a new
capture right after, so the boot of the new firmware is recorded.
"""

import datetime
import pathlib
import queue
import threading
import time

from akscope.core import akt, timeline
from akscope.core.aktfile import AktWriter
from akscope.core.symbols import SymbolTable

TICK_S = 0.25
STATS_EVERY_S = 1.0


class Viewer:
    def __init__(self):
        self.events = queue.Queue(maxsize=256)
        self.cursor = None  # counts already sent: execs, posts, timers, pools, marks, text
        self.last_stats = 0.0

    def push(self, event, data):
        try:
            self.events.put_nowait((event, data))
        except queue.Full:
            # a viewer that stopped reading; drop it rather than block the capture
            pass


class LiveSession:
    def __init__(self, symbols=None, out_dir="captures", flash_target=None, symbols_raw=None):
        self.sym = symbols or SymbolTable()
        self.symbols_raw = symbols_raw
        self.out_dir = pathlib.Path(out_dir)
        self.flash_target = flash_target
        self.lock = threading.Lock()
        self.viewers = []
        self.status = {"state": "idle", "port": None, "file": None, "message": ""}
        self._reset()
        self._stop = threading.Event()
        self._thread = None
        self._serial = None
        threading.Thread(target=self._publisher, daemon=True).start()

    def _reset(self):
        self.builder = None
        self.decoder = None
        self.unwrap = None
        # console text pieces; never trimmed, viewer cursors index into it
        self.text = []

    # status

    def _set_status(self, **kw):
        with self.lock:
            self.status.update(kw)
            status = dict(self.status)
            viewers = list(self.viewers)
        for v in viewers:
            v.push("status", status)

    # viewers

    def add_viewer(self):
        v = Viewer()
        with self.lock:
            self.viewers.append(v)
            v.push("status", dict(self.status))
            if self.builder is not None:
                v.push("snapshot", self._snapshot_locked(v))
        return v

    def remove_viewer(self, v):
        with self.lock:
            if v in self.viewers:
                self.viewers.remove(v)

    def _counts(self):
        tl = self.builder.tl
        return [len(tl.execs), len(tl.posts), len(tl.timers), len(tl.pools), len(tl.marks),
                len(self.text)]

    def _snapshot_locked(self, v):
        tl = self.builder.tl
        out = timeline.to_json(tl, self.sym, text="".join(self.text),
                               stream_stats=self.decoder.stats)
        out["name"] = self.status["file"]
        out["has_symbols"] = bool(self.sym.tasks)
        out["live"] = True
        v.cursor = self._counts()
        v.last_stats = time.monotonic()
        return out

    def _delta_locked(self, v, now):
        tl = self.builder.tl
        e0, p0, t0, pl0, m0, x0 = v.cursor
        if v.cursor == self._counts() and now - v.last_stats < STATS_EVERY_S:
            return None
        d = {
            "execs": [timeline.exec_json(tl, e, self.sym) for e in tl.execs[e0:]],
            "posts": [timeline.post_json(tl, p, self.sym) for p in tl.posts[p0:]],
            "timers": [timeline.timer_json(tl, x, self.sym) for x in tl.timers[t0:]],
            "pools": [timeline.pool_json(tl, x) for x in tl.pools[pl0:]],
            "marks": [timeline.mark_json(tl, x) for x in tl.marks[m0:]],
            "text": "".join(self.text[x0:]),
            "duration_us": tl.us(tl.duration),
            "dropped": tl.dropped,
            "info": tl.info,
            "cpu_hz": tl.cpu_hz,
            "stream": vars(self.decoder.stats),
        }
        if p0 < len(tl.posts) or e0 < len(tl.execs):
            d["tasks"] = timeline.tasks_json(tl, self.sym)
        if now - v.last_stats >= STATS_EVERY_S:
            d.update(timeline.stats_json(tl, self.sym))
            v.last_stats = now
        v.cursor = self._counts()
        return d

    def _publisher(self):
        while True:
            time.sleep(TICK_S)
            now = time.monotonic()
            with self.lock:
                if self.builder is None:
                    continue
                for v in self.viewers:
                    if v.cursor is None:
                        v.push("snapshot", self._snapshot_locked(v))
                        continue
                    d = self._delta_locked(v, now)
                    if d is not None:
                        v.push("delta", d)

    # capture

    def start(self, port, baud):
        import serial

        self.stop()
        try:
            # serial_for_url opens device paths as usual, and loop:// for the tests
            ser = serial.serial_for_url(port, baud, timeout=0.1)
        except serial.SerialException as e:
            self._set_status(state="idle", message=str(e))
            return False
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S.akt")
        path = self.out_dir / name
        header = {
            "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "port": port,
            "baud": baud,
        }
        if self.symbols_raw:
            header["symbols"] = self.symbols_raw
        writer = AktWriter(path, header)
        with self.lock:
            self.builder = timeline.TimelineBuilder()
            self.decoder = akt.StreamDecoder()
            self.unwrap = akt.Unwrapper()
            self.text = []
            # viewers start over with a snapshot of the new capture
            for v in self.viewers:
                v.cursor = None
        self._serial = ser
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, args=(ser, writer), daemon=True)
        self._thread.start()
        self._set_status(state="recording", port=port, file=str(path), message="")
        return True

    def _reader(self, ser, writer):
        last_flush = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    data = ser.read(4096)
                except Exception as e:  # port unplugged
                    self._set_status(state="idle", message="port error: %s" % e)
                    break
                if not data:
                    continue
                writer.write(data)
                with self.lock:
                    for item in self.decoder.feed(data):
                        if isinstance(item, akt.Record):
                            self.builder.add(self.unwrap(item))
                        else:
                            self.text.append(item)
                if time.monotonic() - last_flush > 1.0:
                    writer.flush()
                    last_flush = time.monotonic()
        finally:
            writer.close()
            try:
                ser.close()
            except Exception:
                pass

    def stop(self):
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
            self._serial = None
            self._set_status(state="idle")

    def flash(self, port, baud):
        """Flash flash_target on port, then record from boot. Runs in its own thread."""
        if not self.flash_target:
            self._set_status(message="no firmware to flash, start serve with --project")
            return False

        def run():
            from akscope.flash import FlashError, flash

            self.stop()
            self._set_status(state="flashing", port=port, message="flashing %s" % self.flash_target)
            try:
                # the firmware on the kit runs its console at the rate it is recorded at
                flash(port, self.flash_target, console_baud=baud,
                      log=lambda msg: self._set_status(message=msg.replace("akscope: ", "")))
            except FlashError as e:
                self._set_status(state="idle", message=str(e))
                return
            self.start(port, baud)

        threading.Thread(target=run, daemon=True).start()
        return True
