"""Turn decoded akt records into a timeline: handler runs, the post that caused each one,
and per task / per signal statistics. Used by the web UI and by verify.

Pairing: AK keeps FIFO order per priority queue, so per destination task the n-th EXEC of
(task, sig) is caused by the n-th POST of (task, sig) still waiting. An EXEC with no POST
waiting was posted inside the kernel; if a TIMER_SET for (task, sig) was seen it is a timer
expiry, otherwise its source is unknown.
"""

from collections import deque
from dataclasses import dataclass, field

from akscope.core.model import msg_type_name
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

    def us(self, cycles):
        return cycles * 1e6 / self.cpu_hz


def build_timeline(records, default_cpu_hz=32000000):
    records = sorted(records, key=lambda r: r.t_abs)
    info = next((r.fields for r in records if r.kind == "INFO"), {})
    cpu_hz = info.get("cpu_hz", default_cpu_hz)
    t0 = records[0].t_abs if records else 0
    tl = Timeline(cpu_hz=cpu_hz, t0=t0, duration=0, info=info)

    waiting = {}  # (des, sig) -> deque of post indexes
    timer_sigs = set()

    # sorted by time, a POST always comes before the EXEC it causes: a message cannot start
    # before it was posted, even when a handler posts to its own task
    for r in records:
        t = r.t_abs - t0
        f = r.fields
        if r.kind == "POST":
            p = Post(len(tl.posts), t, f["src"], f["des"], f["sig"], f["msg_type"], f["len"])
            tl.posts.append(p)
            waiting.setdefault((p.des, p.sig), deque()).append(p.index)
        elif r.kind == "EXEC":
            e = Exec(len(tl.execs), f["task"], f["sig"], f["msg_type"], t, f["duration"],
                     f["wait_ms"])
            q = waiting.get((e.task, e.sig))
            if q and tl.posts[q[0]].t <= t:
                p = tl.posts[q.popleft()]
                p.exec = e.index
                e.post, e.src, e.wait = p.index, p.src, t - p.t
            elif (e.task, e.sig) in timer_sigs:
                e.src = SRC_TIMER
            tl.execs.append(e)
            tl.duration = max(tl.duration, t + e.duration)
        elif r.kind in ("TIMER_SET", "TIMER_REMOVE"):
            if r.kind == "TIMER_SET":
                timer_sigs.add((f["des"], f["sig"]))
            tl.timers.append((t, r.kind, f["des"], f["sig"], f.get("periodic", 0),
                              f.get("duty_ms", 0)))
        elif r.kind == "POOL":
            tl.pools.append((t, f["pure"], f["common"], f["dynamic"], f["timer"]))
        elif r.kind == "OVERFLOW":
            tl.dropped += f["dropped"]
        elif r.kind == "USER":
            tl.marks.append((t, f["user_id"], f["value"]))
        tl.duration = max(tl.duration, t)
    return tl


def _summary(execs, us):
    durs = [us(e.duration) for e in execs]
    waits = [us(e.wait) for e in execs if e.wait >= 0]
    return {
        "count": len(durs),
        "exe_min_us": min(durs),
        "exe_avg_us": sum(durs) / len(durs),
        "exe_max_us": max(durs),
        "exe_total_us": sum(durs),
        "wait_max_us": max(waits) if waits else None,
    }


def task_stats(tl):
    by_task = {}
    for e in tl.execs:
        by_task.setdefault(e.task, []).append(e)
    span = tl.us(tl.duration) or 1.0
    out = {}
    for task, execs in sorted(by_task.items()):
        s = _summary(execs, tl.us)
        s["load_pct"] = 100.0 * s["exe_total_us"] / span
        out[task] = s
    return out


def signal_stats(tl):
    by_sig = {}
    for e in tl.execs:
        by_sig.setdefault((e.task, e.sig), []).append(e)
    return {k: _summary(v, tl.us) for k, v in sorted(by_sig.items())}


def to_json(tl, sym=None, text="", stream_stats=None):
    """Plain data for the web UI. Times in microseconds from the first record."""
    sym = sym or SymbolTable()
    us = tl.us

    task_ids = sorted({e.task for e in tl.execs} | {p.des for p in tl.posts} |
                      {p.src for p in tl.posts if isinstance(p.src, int)} | set(sym.tasks))
    tasks = []
    for t in task_ids:
        meta = sym.tasks.get(t, {})
        tasks.append({"id": t, "name": sym.task(t), "pri": meta.get("pri"),
                      "handler": meta.get("handler")})

    def src_name(src):
        return src if isinstance(src, str) else sym.task(src)

    return {
        "cpu_hz": tl.cpu_hz,
        "duration_us": us(tl.duration),
        "info": tl.info,
        "dropped": tl.dropped,
        "stream": vars(stream_stats) if stream_stats else None,
        "tasks": tasks,
        "execs": [{
            "i": e.index, "task": e.task, "sig": e.sig, "sig_name": sym.signal(e.task, e.sig),
            "type": msg_type_name(e.msg_type), "t": us(e.t_begin), "dur": us(e.duration),
            "src": e.src, "src_name": src_name(e.src), "post": e.post,
            "wait": us(e.wait) if e.wait >= 0 else None,
            "wait_ms_kernel": None if e.wait_ms_kernel == 0xFFFF else e.wait_ms_kernel,
        } for e in tl.execs],
        "posts": [{
            "i": p.index, "t": us(p.t), "src": p.src, "src_name": sym.task(p.src), "des": p.des,
            "sig": p.sig, "sig_name": sym.signal(p.des, p.sig), "type": msg_type_name(p.msg_type),
            "len": p.length, "exec": p.exec,
        } for p in tl.posts],
        "timers": [{
            "t": us(t), "kind": kind, "des": des, "sig": sig, "sig_name": sym.signal(des, sig),
            "periodic": bool(periodic), "duty_ms": duty,
        } for t, kind, des, sig, periodic, duty in tl.timers],
        "pools": [{"t": us(t), "pure": a, "common": b, "dynamic": c, "timer": d}
                  for t, a, b, c, d in tl.pools],
        "marks": [{"t": us(t), "id": i, "value": v} for t, i, v in tl.marks],
        "task_stats": [dict(task=t, name=sym.task(t), **s) for t, s in task_stats(tl).items()],
        "signal_stats": [dict(task=t, sig=g, task_name=sym.task(t), sig_name=sym.signal(t, g), **s)
                         for (t, g), s in signal_stats(tl).items()],
        "text": text,
    }
