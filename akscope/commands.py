import csv
import dataclasses
import json
import sys

from akscope.core import stats
from akscope.core.console_log import load_trace
from akscope.core.model import msg_type_name

_RECORD_FIELDS = ("line", "origin", "index", "task_id", "msg_type", "ref_count", "sig",
                  "wait_ms", "exe_ms")


def _table(header, rows, out):
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    for row in (header, *rows):
        out.write("  ".join(str(x).rjust(w) for x, w in zip(row, widths)).rstrip() + "\n")


def _load(path):
    trace = load_trace(path)
    if not trace.dispatches:
        sys.stderr.write("akscope: no message records in %s\n" % path)
        sys.stderr.write("akscope: build the firmware with "
                         "LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE\n")
    return trace


def cmd_import_log(args, out=None):
    out = out or sys.stdout
    trace = _load(args.file)
    if not trace.dispatches:
        return 2

    if args.format == "json":
        json.dump([dataclasses.asdict(d) for d in trace.dispatches], out, indent=1)
        out.write("\n")
    elif args.format == "csv":
        w = csv.writer(out, lineterminator="\n")
        w.writerow(_RECORD_FIELDS)
        for d in trace.dispatches:
            w.writerow(getattr(d, f) for f in _RECORD_FIELDS)
    else:
        rows = [(d.line, d.origin, d.task_id, d.sig, msg_type_name(d.msg_type), d.ref_count,
                 d.wait_ms, d.exe_ms) for d in trace.dispatches]
        _table(("line", "origin", "task", "sig", "type", "ref", "wait_ms", "exe_ms"), rows, out)
    return 0


def cmd_stats(args, out=None):
    out = out or sys.stdout
    trace = _load(args.file)
    if not trace.dispatches:
        return 2

    out.write("%s: %d messages, %d tasks\n\n" % (trace.source, len(trace.dispatches),
                                                  len(trace.task_ids())))
    header = ("count", "exe_min", "exe_avg", "exe_max", "exe_total", "wait_max")

    def cols(s):
        return (s.count, s.exe_min, "%.1f" % s.exe_avg, s.exe_max, s.exe_total, s.wait_max)

    out.write("per task (ms)\n")
    _table(("task",) + header, [(t,) + cols(s) for t, s in stats.per_task(trace).items()], out)

    out.write("\nper signal (ms)\n")
    _table(("task", "sig") + header,
           [(t, sig) + cols(s) for (t, sig), s in stats.per_signal(trace).items()], out)
    return 0


def register(subparsers):
    p = subparsers.add_parser("import-log", help="read records from a console capture")
    p.add_argument("file", help="text captured from the board console")
    p.add_argument("--format", choices=("text", "csv", "json"), default="text")
    p.set_defaults(func=cmd_import_log)

    p = subparsers.add_parser("stats", help="exe and wait time per task and per signal")
    p.add_argument("file", help="text captured from the board console")
    p.set_defaults(func=cmd_stats)
