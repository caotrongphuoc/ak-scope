"""Runtime rules R01 to R08 on a recorded trace (a Timeline from akscope.core.timeline).

Findings are grouped per (task, signal) so a handler that is slow every frame gives one line
with a count and its worst case, not one line per run.
"""

from collections import deque

from akscope.verify import ERROR, WARNING, Finding, parse_duration_us


def _name(sym, task, sig=None):
    if sig is None:
        return sym.task(task)
    return "%s / %s" % (sym.task(task), sym.signal(task, sig))


def r01_exe_time(tl, sym, rules):
    """R01: handler run time (run to completion)."""
    warn = rules["exe_warn_ms"] * 1e3
    err = rules["exe_error_ms"] * 1e3
    worst = {}
    for e in tl.execs:
        us = tl.us(e.duration)
        if us < warn:
            continue
        w = worst.setdefault((e.task, e.sig), {"n": 0, "max": None})
        w["n"] += 1
        if w["max"] is None or us > tl.us(w["max"].duration):
            w["max"] = e
    out = []
    for (task, sig), w in sorted(worst.items()):
        e = w["max"]
        us = tl.us(e.duration)
        sev = ERROR if us >= err else WARNING
        limit = rules["exe_error_ms"] if sev == ERROR else rules["exe_warn_ms"]
        out.append(Finding("R01", sev, "%s ran %.1f ms (limit %g ms), %d run%s over %g ms"
                           % (_name(sym, task, sig), us / 1e3, limit, w["n"], "s" if w["n"] > 1 else "",
                              rules["exe_warn_ms"]), t_us=tl.us(e.t_begin), exec=e.index))
    return out


def r02_wait_time(tl, sym, rules):
    """R02: time a message waited in the queue."""
    limit = rules["wait_warn_ms"] * 1e3
    worst = {}
    for e in tl.execs:
        if e.wait >= 0:
            us = tl.us(e.wait)
        elif e.wait_ms_kernel != 0xFFFF:
            us = e.wait_ms_kernel * 1e3
        else:
            continue
        if us < limit:
            continue
        w = worst.setdefault((e.task, e.sig), {"n": 0, "us": 0, "e": None})
        w["n"] += 1
        if us > w["us"]:
            w["us"], w["e"] = us, e
    return [Finding("R02", WARNING, "%s waited %.1f ms in the queue (limit %g ms), %d time%s; a higher "
                    "priority task or a long handler ran first"
                    % (_name(sym, t, s), w["us"] / 1e3, rules["wait_warn_ms"], w["n"], "s" if w["n"] > 1 else ""),
                    t_us=tl.us(w["e"].t_begin), exec=w["e"].index)
            for (t, s), w in sorted(worst.items())]


def r03_pools(tl, sym, rules):
    """R03: message and timer pools close to full."""
    info = tl.info or {}
    if not tl.pools or not info:
        return []
    sizes = {"pure": info.get("pure_pool"), "common": info.get("common_pool"),
             "dynamic": info.get("dynamic_pool"), "timer": info.get("timer_pool")}
    last = tl.pools[-1]
    used = dict(zip(("pure", "common", "dynamic", "timer"), last[1:]))
    out = []
    for kind, size in sizes.items():
        if not size:
            continue
        pct = 100.0 * used[kind] / size
        if pct >= rules["pool_warn_pct"]:
            sev = ERROR if used[kind] >= size else WARNING
            out.append(Finding("R03", sev, "%s pool reached %d of %d (%.0f %%); raise it in ak.cfg.mk"
                               % (kind, used[kind], size, pct), t_us=tl.us(last[0])))
    return out


def r04_storm(tl, sym, rules):
    """R04: the same message posted too often."""
    limit = rules["storm_per_s"]
    window = 1e6
    times = {}
    for p in tl.posts:
        times.setdefault((p.src, p.des, p.sig), []).append(tl.us(p.t))
    out = []
    for (src, des, sig), ts in sorted(times.items()):
        q = deque()
        peak, at = 0, None
        for t in ts:
            q.append(t)
            while q and t - q[0] > window:
                q.popleft()
            if len(q) > peak:
                peak, at = len(q), q[0]
        if peak > limit:
            out.append(Finding("R04", WARNING, "%s -> %s posted %d times in 1 s (limit %d)"
                               % (sym.task(src), _name(sym, des, sig), peak, limit), t_us=at))
    return out


def r05_timer_drift(tl, sym, rules):
    """R05: a periodic timer that does not fire on its period."""
    pct = rules["timer_drift_pct"] / 100.0
    periods = {}  # (des, sig) -> (since_us, duty_us)
    events = []
    for t, kind, des, sig, periodic, duty in tl.timers:
        events.append((tl.us(t), 0, kind, des, sig, periodic, duty))
    for e in tl.execs:
        if e.src == "timer":
            events.append((tl.us(e.t_begin), 1, "EXEC", e.task, e.sig, 0, e))
    for t in tl.gaps:
        events.append((tl.us(t), 2, "GAP", None, None, 0, None))
    events.sort(key=lambda x: (x[0], x[1]))
    last = {}
    worst = {}
    for t, _, kind, des, sig, periodic, extra in events:
        key = (des, sig)
        if kind == "GAP":
            # runs may be missing before an OVERFLOW: no interval across it
            last.clear()
            continue
        if kind == "TIMER_SET":
            periods[key] = extra * 1e3 if periodic else None
            last.pop(key, None)
        elif kind == "TIMER_REMOVE":
            periods.pop(key, None)
            last.pop(key, None)
        else:
            period = periods.get(key)
            if not period:
                continue
            if key in last:
                gap = t - last[key]
                off = abs(gap - period) / period
                if off > pct:
                    w = worst.setdefault(key, {"n": 0, "off": 0, "gap": 0, "e": None, "period": period})
                    w["n"] += 1
                    if off > w["off"]:
                        w.update(off=off, gap=gap, e=extra)
            last[key] = t
    return [Finding("R05", WARNING, "%s fired %.1f ms after the previous run, period %.0f ms (%.0f %% off), "
                    "%d time%s" % (_name(sym, d, s), w["gap"] / 1e3, w["period"] / 1e3, 100 * w["off"], w["n"],
                                   "s" if w["n"] > 1 else ""),
                    t_us=tl.us(w["e"].t_begin), exec=w["e"].index)
            for (d, s), w in sorted(worst.items())]


def r06_unhandled(tl, sym, rules, handled=None):
    """R06: a message reached a task that has no case for it (needs the project sources)."""
    if not handled:
        return []
    seen = {}
    for e in tl.execs:
        values = handled.get(e.task)
        if values is None or e.sig in values:
            continue
        seen.setdefault((e.task, e.sig), []).append(e)
    return [Finding("R06", WARNING, "%s was delivered %d time%s but the task has no case for it"
                    % (_name(sym, t, s), len(es), "s" if len(es) > 1 else ""),
                    t_us=tl.us(es[0].t_begin), exec=es[0].index)
            for (t, s), es in sorted(seen.items())]


def r07_lost(tl, sym, rules, stream_stats=None):
    """R07: the trace itself is incomplete."""
    out = []
    if tl.dropped:
        out.append(Finding("R07", ERROR, "%d records dropped on the board (ring full); the trace is "
                           "incomplete, raise the baud rate or --ring" % tl.dropped))
    if stream_stats is not None and stream_stats.corrupt:
        out.append(Finding("R07", WARNING, "%d corrupt frames (text printed from an interrupt in the "
                           "middle of a frame)" % stream_stats.corrupt))
    return out


def _match(spec, task, sig, sym):
    def ok(want, got, name):
        if want is None:
            return True
        if isinstance(want, int):
            return want == got
        return str(want) == name
    return ok(spec.get("task"), task, sym.task(task)) and ok(spec.get("sig"), sig, sym.signal(task, sig))


def r08_expect(tl, sym, rules):
    """R08: expected sequences from the rules file."""
    out = []
    for exp in rules["expect"]:
        name = exp.get("name") or "expectation"
        after, then = exp.get("after") or {}, exp.get("then") or {}
        within = parse_duration_us(exp.get("within", "100ms"))
        starts = [e for e in tl.execs if _match(after, e.task, e.sig, sym)]
        targets = [tl.us(e.t_begin) for e in tl.execs if _match(then, e.task, e.sig, sym)]
        misses = []
        j = 0
        for e in starts:
            t0 = tl.us(e.t_begin)
            while j < len(targets) and targets[j] <= t0:
                j += 1
            if j >= len(targets) or targets[j] - t0 > within:
                misses.append(e)
        if not starts:
            out.append(Finding("R08", WARNING, "%s: no run of %s in the trace" % (name, _spec(after))))
        elif misses:
            out.append(Finding("R08", ERROR, "%s: %s not followed by %s within %g ms, %d of %d times"
                               % (name, _spec(after), _spec(then), within / 1e3, len(misses), len(starts)),
                               t_us=tl.us(misses[0].t_begin), exec=misses[0].index))
    return out


def _spec(s):
    return "/".join(str(s[k]) for k in ("task", "sig") if k in s) or "any"


def run(tl, sym, rules, handled=None, stream_stats=None):
    findings = []
    findings += r01_exe_time(tl, sym, rules)
    findings += r02_wait_time(tl, sym, rules)
    findings += r03_pools(tl, sym, rules)
    findings += r04_storm(tl, sym, rules)
    findings += r05_timer_drift(tl, sym, rules)
    findings += r06_unhandled(tl, sym, rules, handled)
    findings += r07_lost(tl, sym, rules, stream_stats)
    findings += r08_expect(tl, sym, rules)
    return sorted(findings, key=lambda f: (f.t_us is None, f.t_us or 0, f.rule))
