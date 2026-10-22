"""Turn decoded akt records into a timeline: handler runs, the post that caused each one,
and per task / per signal statistics. Used by the web UI and by verify.

Pairing: AK keeps FIFO order per priority queue, so per destination task the n-th EXEC of
(task, sig) is caused by the n-th POST of (task, sig) still waiting. After an OVERFLOW the
waiting posts are dropped from pairing, since their runs may be lost. An EXEC with no POST
waiting was posted inside the kernel; if a TIMER_SET for (task, sig) was seen it is a timer
expiry, otherwise its source is unknown.
"""

from collections import deque
from dataclasses import dataclass, field

from akscope.core.model import msg_type_name
from akscope.core.runlog import RunLog
from akscope.core.symbols import SymbolTable

SRC_TIMER = "timer"
SRC_UNKNOWN = "unknown"


@dataclass
class Exec:
    index: int  # position in Timeline.execs
    task: int
    sig: int
    msg_type: int
    t_begin: int  # cycles, unwrapped, from the first record
    duration: int  # cycles
    wait_ms_kernel: int  # 0xFFFF when unknown
    src: object = SRC_UNKNOWN  # task id, SRC_TIMER or SRC_UNKNOWN
    post: int = -1  # index in Timeline.posts, -1 if none
    wait: int = -1  # cycles from the post to t_begin, -1 if no post


@dataclass
class Post:
    index: int
    t: int
    src: int
    des: int
    sig: int
    msg_type: int
    length: int
    exec: int = -1  # index in Timeline.execs, -1 while still queued at the end


class Agg:
    """Running count, min, max, total of handler run time, and the longest wait, in cycles."""

    __slots__ = ("count", "exe_min", "exe_max", "exe_total", "wait_max")

    def __init__(self):
        self.count = 0
        self.exe_min = None
        self.exe_max = 0
        self.exe_total = 0
        self.wait_max = -1

    def add(self, e):
        self.count += 1
        self.exe_min = e.duration if self.exe_min is None else min(self.exe_min, e.duration)
        self.exe_max = max(self.exe_max, e.duration)
        self.exe_total += e.duration
        self.wait_max = max(self.wait_max, e.wait)

    def summary(self, us):
        return {
            "count": self.count,
            "exe_min_us": us(self.exe_min),
            "exe_avg_us": us(self.exe_total) / self.count,
            "exe_max_us": us(self.exe_max),
            "exe_total_us": us(self.exe_total),
            "wait_max_us": us(self.wait_max) if self.wait_max >= 0 else None,
        }


@dataclass
class Timeline:
    cpu_hz: int
    t0: int  # raw t_abs of the first record, all times below are relative to it
    duration: int
    info: dict
    execs: list = field(default_factory=list)
    posts: list = field(default_factory=list)
    timers: list = field(default_factory=list)  # (t, kind, des, sig, periodic, duty_ms)
    pools: list = field(default_factory=list)  # (t, pure, common, dynamic, timer)
    marks: list = field(default_factory=list)  # (t, user_id, value)
    dropped: int = 0
    gaps: list = field(default_factory=list)  # t of each OVERFLOW: records are missing before it
    task_agg: dict = field(default_factory=dict)  # task -> Agg
    sig_agg: dict = field(default_factory=dict)  # (task, sig) -> Agg
    logs: dict = field(default_factory=dict)  # exec index -> console lines of that run (runlog)

    def us(self, cycles):
        return cycles * 1e6 / self.cpu_hz


class TimelineBuilder:
    """Adds records one at a time, for files and for a live capture.

    Records may come in arrival order (live) or sorted by time (files). Both pair the same
    way: an EXEC is sent after its handler returns, so it always arrives after the POST that
    caused it, and a POST made inside a handler to its own task is later than that run's
    start, which the t check below rejects.
    """

    def __init__(self, default_cpu_hz=32000000):
        self.tl = Timeline(cpu_hz=default_cpu_hz, t0=None, duration=0, info={})
        self._waiting = {}  # (des, sig) -> deque of post indexes
        self._timer_sigs = set()
        self.links = []  # (post index, exec index) paired since the last take_links()

    def add(self, r):
        """Returns the Exec for an EXEC record, else None."""
        tl = self.tl
        added = None
        if tl.t0 is None:
            tl.t0 = r.t_abs
        t = r.t_abs - tl.t0
        f = r.fields
        if r.kind == "POST":
            p = Post(len(tl.posts), t, f["src"], f["des"], f["sig"], f["msg_type"], f["len"])
            tl.posts.append(p)
            self._waiting.setdefault((p.des, p.sig), deque()).append(p.index)
        elif r.kind == "EXEC":
            e = Exec(len(tl.execs), f["task"], f["sig"], f["msg_type"], t, f["duration"],
                     f["wait_ms"])
            q = self._waiting.get((e.task, e.sig))
            if q and tl.posts[q[0]].t <= t:
                p = tl.posts[q.popleft()]
                p.exec = e.index
                e.post, e.src, e.wait = p.index, p.src, t - p.t
                self.links.append((p.index, e.index))
            elif (e.task, e.sig) in self._timer_sigs:
                e.src = SRC_TIMER
            tl.execs.append(e)
            added = e
            tl.task_agg.setdefault(e.task, Agg()).add(e)
            tl.sig_agg.setdefault((e.task, e.sig), Agg()).add(e)
            tl.duration = max(tl.duration, t + e.duration)
        elif r.kind in ("TIMER_SET", "TIMER_REMOVE"):
            if r.kind == "TIMER_SET":
                self._timer_sigs.add((f["des"], f["sig"]))
            tl.timers.append((t, r.kind, f["des"], f["sig"], f.get("periodic", 0),
                              f.get("duty_ms", 0)))
        elif r.kind == "POOL":
            tl.pools.append((t, f["pure"], f["common"], f["dynamic"], f["timer"]))
        elif r.kind == "OVERFLOW":
            tl.dropped += f["dropped"]
            tl.gaps.append(t)
            # the runs that would pair with the posts still waiting may be among the dropped
            # records; pairing across the gap would invent waits of many seconds
            self._waiting.clear()
        elif r.kind == "USER":
            tl.marks.append((t, f["user_id"], f["value"]))
        elif r.kind == "INFO" and not tl.info:
            tl.info = f
            tl.cpu_hz = f["cpu_hz"]
        tl.duration = max(tl.duration, t)
        return added

    def take_links(self):
        links, self.links = self.links, []
        return links


def build_timeline(records, default_cpu_hz=32000000):
    records = sorted(records, key=lambda r: r.t_abs)
    b = TimelineBuilder(default_cpu_hz)
    # cpu_hz must be known before the first time is converted, take INFO first
    info = next((r for r in records if r.kind == "INFO"), None)
    if info is not None:
        b.tl.info = info.fields
        b.tl.cpu_hz = info.fields["cpu_hz"]
    for r in records:
        e = b.add(r)
        if e is not None:
            r.exec_index = e.index
    if b.tl.t0 is None:
        b.tl.t0 = 0
    return b.tl


def attach_logs(tl, items, sym, baud=None):
    """Fill tl.logs from the decoded items in UART order (akt.decode_stream), after
    build_timeline has numbered the EXEC records. baud: console rate of the capture."""
    log = RunLog(sym.signal, baud, sym.signal_names())
    for x in items:
        if isinstance(x, str):
            log.text(x)
        elif x.kind == "EXEC" and x.exec_index >= 0:
            log.exec(x.exec_index, x.fields["task"], x.fields["sig"], tl.us(x.fields["duration"]))
    tl.logs = log.logs


def task_stats(tl):
    span = tl.us(tl.duration) or 1.0
    out = {}
    for task, agg in sorted(tl.task_agg.items()):
        s = agg.summary(tl.us)
        s["load_pct"] = 100.0 * s["exe_total_us"] / span
        out[task] = s
    return out


def signal_stats(tl):
    return {k: agg.summary(tl.us) for k, agg in sorted(tl.sig_agg.items())}


def exec_json(tl, e, sym):
    us = tl.us
    return {
        "i": e.index, "task": e.task, "sig": e.sig, "sig_name": sym.signal(e.task, e.sig),
        "type": msg_type_name(e.msg_type), "t": us(e.t_begin), "dur": us(e.duration),
        "src": e.src, "src_name": e.src if isinstance(e.src, str) else sym.task(e.src),
        "post": e.post, "wait": us(e.wait) if e.wait >= 0 else None,
        "wait_ms_kernel": None if e.wait_ms_kernel == 0xFFFF else e.wait_ms_kernel,
        "log": tl.logs.get(e.index),
    }


def post_json(tl, p, sym):
    return {
        "i": p.index, "t": tl.us(p.t), "src": p.src, "src_name": sym.task(p.src), "des": p.des,
        "sig": p.sig, "sig_name": sym.signal(p.des, p.sig), "type": msg_type_name(p.msg_type),
        "len": p.length, "exec": p.exec,
    }


def timer_json(tl, item, sym):
    t, kind, des, sig, periodic, duty = item
    return {"t": tl.us(t), "kind": kind, "des": des, "sig": sig, "sig_name": sym.signal(des, sig),
            "periodic": bool(periodic), "duty_ms": duty}


def pool_json(tl, item):
    t, a, b, c, d = item
    return {"t": tl.us(t), "pure": a, "common": b, "dynamic": c, "timer": d}


def mark_json(tl, item):
    t, i, v = item
    return {"t": tl.us(t), "id": i, "value": v}


def stats_json(tl, sym):
    return {
        "task_stats": [dict(task=t, name=sym.task(t), **s) for t, s in task_stats(tl).items()],
        "signal_stats": [dict(task=t, sig=g, task_name=sym.task(t), sig_name=sym.signal(t, g), **s)
                         for (t, g), s in signal_stats(tl).items()],
    }


def tasks_json(tl, sym):
    task_ids = sorted({e.task for e in tl.execs} | {p.des for p in tl.posts} |
                      {p.src for p in tl.posts} | set(sym.tasks))
    out = []
    for t in task_ids:
        meta = sym.tasks.get(t, {})
        out.append({"id": t, "name": sym.task(t), "pri": meta.get("pri"),
                    "handler": meta.get("handler")})
    return out


def to_json(tl, sym=None, text="", stream_stats=None):
    """Plain data for the web UI. Times in microseconds from the first record."""
    sym = sym or SymbolTable()
    out = {
        "cpu_hz": tl.cpu_hz,
        "duration_us": tl.us(tl.duration),
        "info": tl.info,
        "dropped": tl.dropped,
        "stream": vars(stream_stats) if stream_stats else None,
        "tasks": tasks_json(tl, sym),
        "execs": [exec_json(tl, e, sym) for e in tl.execs],
        "posts": [post_json(tl, p, sym) for p in tl.posts],
        "timers": [timer_json(tl, x, sym) for x in tl.timers],
        "pools": [pool_json(tl, x) for x in tl.pools],
        "marks": [mark_json(tl, x) for x in tl.marks],
        "text": text,
    }
    out.update(stats_json(tl, sym))
    return out
