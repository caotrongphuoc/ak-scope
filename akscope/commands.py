import csv
import dataclasses
import datetime
import json
import sys
import time

from akscope.core import akt, stats
from akscope.core.aktfile import AktWriter, read_capture
from akscope.core.console_log import load_trace
from akscope.core.model import msg_type_name

DEFAULT_CPU_HZ = 32000000

# task ids the kernel uses outside the task table
_SPECIAL_TASKS = {0xEE: "isr", 0xEF: "idle"}

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


def cmd_record(args, out=None):
    out = out or sys.stdout
    try:
        import serial
    except ImportError:
        sys.stderr.write("akscope: record needs pyserial (pip install pyserial)\n")
        return 2

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        sys.stderr.write("akscope: %s\n" % e)
        return 2

    header = {
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "port": args.port,
        "baud": args.baud,
    }
    dec = akt.StreamDecoder()
    t_start = time.monotonic()
    t_status = t_start

    with port, AktWriter(args.output, header) as w:
        try:
            while args.duration is None or time.monotonic() - t_start < args.duration:
                data = port.read(4096)
                if data:
                    w.write(data)
                    for item in dec.feed(data):
                        if isinstance(item, str) and not args.quiet:
                            out.write(item)
                            out.flush()
                now = time.monotonic()
                if now - t_status >= 1.0:
                    t_status = now
                    w.flush()
                    s = dec.stats
                    sys.stderr.write("\r%6.1f s  %d frames  %d corrupt  " %
                                     (now - t_start, s.frames, s.corrupt))
        except KeyboardInterrupt:
            pass

    s = dec.stats
    sys.stderr.write("\n%s: %d frames, %d corrupt, %d text bytes\n" %
                     (args.output, s.frames, s.corrupt, s.text_bytes))
    return 0


def _task_name(task_id):
    return _SPECIAL_TASKS.get(task_id, str(task_id))


def _describe(rec):
    f = rec.fields
    if rec.kind == "POST":
        return "%s -> %s  sig %d  %s  len %d" % (_task_name(f["src"]), _task_name(f["des"]),
                                                 f["sig"], msg_type_name(f["msg_type"]), f["len"])
    if rec.kind == "TIMER_SET":
        return "-> %d  sig %d  %d ms  %s" % (f["des"], f["sig"], f["duty_ms"],
                                             "periodic" if f["periodic"] else "one shot")
    if rec.kind == "TIMER_REMOVE":
        return "-> %d  sig %d" % (f["des"], f["sig"])
    if rec.kind == "POOL":
        return "used max: pure %d  common %d  dynamic %d  timer %d" % (
            f["pure"], f["common"], f["dynamic"], f["timer"])
    if rec.kind == "OVERFLOW":
        return "%d records dropped" % f["dropped"]
    if rec.kind == "USER":
        return "id %d  value %d" % (f["user_id"], f["value"])
    if rec.kind == "INFO":
        return "format %d  %d Hz  %d tasks  app %s" % (
            f["version"], f["cpu_hz"], f["task_count"], ".".join(map(str, f["app_ver"])))
    return ""


def cmd_dump(args, out=None):
    out = out or sys.stdout
    header, data = read_capture(args.file)
    records, _, s = akt.decode_bytes(data)
    if not records:
        sys.stderr.write("akscope: no trace frames in %s\n" % args.file)
        return 2

    info = next((r for r in records if r.kind == "INFO"), None)
    cpu_hz = info.fields["cpu_hz"] if info else DEFAULT_CPU_HZ
    if info is None:
        sys.stderr.write("akscope: no INFO record, assuming %d Hz\n" % cpu_hz)

    # EXEC arrives after the POSTs made inside the handler; sort so it reads in time order
    records.sort(key=lambda r: r.t_abs)
    t0 = records[0].t_abs
    for r in records:
        ms = (r.t_abs - t0) * 1000.0 / cpu_hz
        if r.kind == "EXEC":
            f = r.fields
            wait = "-" if f["wait_ms"] == 0xFFFF else "%d ms" % f["wait_ms"]
            line = "%-12s task %d  sig %d  %s  exe %.3f ms  wait %s" % (
                r.kind, f["task"], f["sig"], msg_type_name(f["msg_type"]),
                f["duration"] * 1000.0 / cpu_hz, wait)
        else:
            line = "%-12s %s" % (r.kind, _describe(r))
        out.write("%12.3f  %s\n" % (ms, line))

    sys.stderr.write("%d frames, %d corrupt, %d text bytes\n" % (s.frames, s.corrupt, s.text_bytes))
    return 0


def register(subparsers):
    p = subparsers.add_parser("import-log", help="read records from a console capture")
    p.add_argument("file", help="text captured from the board console")
    p.add_argument("--format", choices=("text", "csv", "json"), default="text")
    p.set_defaults(func=cmd_import_log)

    p = subparsers.add_parser("stats", help="exe and wait time per task and per signal")
    p.add_argument("file", help="text captured from the board console")
    p.set_defaults(func=cmd_stats)

    p = subparsers.add_parser("record", help="capture the console UART to a .akt file")
    p.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("-o", "--output", default="run.akt")
    p.add_argument("--duration", type=float, help="seconds, default until Ctrl-C")
    p.add_argument("-q", "--quiet", action="store_true", help="do not echo console text")
    p.set_defaults(func=cmd_record)

    p = subparsers.add_parser("dump", help="print the trace records of a .akt or raw capture")
    p.add_argument("file")
    p.set_defaults(func=cmd_dump)
