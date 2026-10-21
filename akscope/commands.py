import csv
import dataclasses
import datetime
import json
import pathlib
import sys
import time

from akscope.core import akt, stats
from akscope.core.aktfile import AktWriter, read_capture
from akscope.core.console_log import load_trace
from akscope.core.model import msg_type_name
from akscope.core.symbols import SymbolTable

DEFAULT_CPU_HZ = 32000000

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

    symbols = None
    sym_source = args.sym or (args.flash if args.flash and pathlib.Path(args.flash).is_dir() else None)
    if sym_source:
        if _load_symbols(sym_source) is None:
            return 2
        p = pathlib.Path(sym_source).expanduser()
        if p.is_dir():
            from akscope.build import Builder
            p = Builder(p).symbols_path
        symbols = json.loads(p.read_text())

    if args.baud is None:
        args.baud = _project_baud(args.flash, args.sym) or 115200

    if args.flash:
        # flash first and open the port right after, so INFO and the init records are kept
        binary = _resolve_binary(args.flash)
        if binary is None:
            return 2
        from akscope.flash import FlashError, flash
        try:
            flash(args.port, binary, log=_log_err)
        except FlashError as e:
            _log_err("akscope: %s" % e)
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
    if symbols:
        header["symbols"] = symbols
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


def _load_symbols(path):
    """SymbolTable from a symbols.json file, or from a project built by `akscope build`."""
    from akscope.build import Builder, BuildError

    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        try:
            p = Builder(p).symbols_path
        except BuildError as e:
            _log_err("akscope: %s" % e)
            return None
    try:
        return SymbolTable(json.loads(p.read_text()))
    except (OSError, ValueError) as e:
        _log_err("akscope: cannot read symbols %s: %s" % (p, e))
        return None


def _describe(rec, sym):
    f = rec.fields
    if rec.kind == "POST":
        return "%s -> %s  sig %s  %s  len %d" % (
            sym.task(f["src"]), sym.task(f["des"]), sym.signal(f["des"], f["sig"]),
            msg_type_name(f["msg_type"]), f["len"])
    if rec.kind == "TIMER_SET":
        return "-> %s  sig %s  %d ms  %s" % (
            sym.task(f["des"]), sym.signal(f["des"], f["sig"]), f["duty_ms"],
            "periodic" if f["periodic"] else "one shot")
    if rec.kind == "TIMER_REMOVE":
        return "-> %s  sig %s" % (sym.task(f["des"]), sym.signal(f["des"], f["sig"]))
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
    if rec.kind == "EXEC":
        wait = "-" if f["wait_ms"] == 0xFFFF else "%d ms" % f["wait_ms"]
        return "task %s  sig %s  %s  exe %.3f ms  wait %s" % (
            sym.task(f["task"]), sym.signal(f["task"], f["sig"]), msg_type_name(f["msg_type"]),
            f["duration"] * 1000.0 / sym.cpu_hz, wait)
    return ""


def cmd_dump(args, out=None):
    out = out or sys.stdout
    header, data = read_capture(args.file)
    records, _, s = akt.decode_bytes(data)
    if not records:
        sys.stderr.write("akscope: no trace frames in %s\n" % args.file)
        return 2

    if args.sym:
        sym = _load_symbols(args.sym)
        if sym is None:
            return 2
    else:
        sym = SymbolTable(header.get("symbols"))

    info = next((r for r in records if r.kind == "INFO"), None)
    sym.cpu_hz = info.fields["cpu_hz"] if info else DEFAULT_CPU_HZ
    if info is None:
        sys.stderr.write("akscope: no INFO record, assuming %d Hz\n" % sym.cpu_hz)

    # EXEC arrives after the POSTs made inside the handler; sort so it reads in time order
    records.sort(key=lambda r: r.t_abs)
    t0 = records[0].t_abs
    for r in records:
        ms = (r.t_abs - t0) * 1000.0 / sym.cpu_hz
        out.write("%12.3f  %-12s %s\n" % (ms, r.kind, _describe(r, sym)))

    sys.stderr.write("%d frames, %d corrupt, %d text bytes\n" % (s.frames, s.corrupt, s.text_bytes))
    return 0


def _log_err(msg):
    sys.stderr.write(msg + "\n")


def cmd_build(args, out=None):
    from akscope.build import Builder, BuildError

    try:
        b = Builder(args.project, tools=args.tools, gcc=args.gcc, ring=args.ring,
                    console_baud=args.console_baud, log=_log_err)
        if args.clean:
            b.clean()
        binary, handlers = b.build()
    except BuildError as e:
        _log_err("akscope: %s" % e)
        return 2
    (out or sys.stdout).write("%s\n" % binary)
    return 0


def _project_baud(*paths):
    """Console baud rate of the first of paths that is a project built by akscope build."""
    from akscope.build import Builder, BuildError

    for p in paths:
        if p and pathlib.Path(p).expanduser().is_dir():
            try:
                baud = Builder(p, log=lambda m: None).console_baud
            except BuildError:
                continue
            if baud:
                return baud
    return None


def _resolve_binary(target):
    """A .bin path as is, or for a project folder the .bin that `akscope build` made."""
    from akscope.build import Builder, BuildError

    target = pathlib.Path(target).expanduser()
    if target.suffix == ".bin":
        return target
    try:
        bins = sorted(Builder(target).obj.glob("*.bin"))
    except BuildError as e:
        _log_err("akscope: %s" % e)
        return None
    if not bins:
        _log_err("akscope: nothing built for %s, run akscope build first" % target)
        return None
    return bins[0]


def cmd_flash(args, out=None):
    from akscope.flash import FlashError, flash

    target = _resolve_binary(args.target)
    if target is None:
        return 2

    try:
        flash(args.port, target, attempts=args.attempts, log=_log_err)
    except FlashError as e:
        _log_err("akscope: %s" % e)
        return 2
    return 0


def _make_server(args, port):
    """(httpd, live) for serve and app, or (None, None) after printing why not"""
    from akscope.live import LiveSession
    from akscope.server import State, make_server

    sym, sym_raw, flash_target = None, None, None
    sym_source = args.sym or args.project
    if sym_source:
        sym = _load_symbols(sym_source)
        if sym is None:
            return None, None
        p = pathlib.Path(sym_source).expanduser()
        if p.is_dir():
            from akscope.build import Builder
            p = Builder(p).symbols_path
        sym_raw = json.loads(p.read_text())
    if args.project:
        flash_target = _resolve_binary(args.project)
        if flash_target is None:
            return None, None

    if args.baud is None:
        args.baud = _project_baud(args.project) or 115200

    live = LiveSession(symbols=sym, symbols_raw=sym_raw, out_dir=args.captures,
                       flash_target=flash_target)
    try:
        httpd = make_server(State(args.file, sym, live, args.dev, args.baud, project=args.project,
                                  rules_path=args.rules, base=args.base), port)
    except OSError as e:
        _log_err("akscope: cannot listen on 127.0.0.1:%d: %s" % (port, e))
        return None, None
    return httpd, live


def cmd_serve(args, out=None):
    import webbrowser

    httpd, live = _make_server(args, args.port)
    if httpd is None:
        return 2
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    _log_err("akscope: serving %s, Ctrl-C to stop" % url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    live.stop()
    httpd.server_close()
    return 0


def cmd_app(args, out=None):
    try:
        from akscope import app
    except ImportError as e:
        _log_err("akscope: %s" % e)
        return 2
    # a free port: several windows (one per project) can run at the same time
    httpd, live = _make_server(args, 0)
    if httpd is None:
        return 2
    try:
        return app.run(httpd, live, args)
    finally:
        live.stop()
        httpd.server_close()


def cmd_install_desktop(args, out=None):
    out = out or sys.stdout
    from akscope import desktop
    for path in desktop.install(args.project):
        print(path, file=out)
    return 0


def cmd_verify(args, out=None):
    out = out or sys.stdout
    from akscope import verify
    from akscope.build import BuildError
    from akscope.core import timeline

    if not args.project and not args.trace:
        _log_err("akscope: give a project folder, a --trace file, or both")
        return 2
    try:
        rules = verify.load_rules(args.rules)
    except (OSError, ValueError) as e:
        _log_err("akscope: rules: %s" % e)
        return 2

    findings = []
    handled = None
    if args.project and not args.no_static:
        try:
            res = verify.run_static(args.project, args.tools, args.gcc, args.base, args.all, _log_err)
        except BuildError as e:
            _log_err("akscope: %s" % e)
            return 2
        findings += res.findings
        handled = res.handled

    for path in args.trace or []:
        header, data = read_capture(path)
        records, _, st = akt.decode_bytes(data)
        if not records:
            _log_err("akscope: no trace frames in %s" % path)
            return 2
        sym = None
        if args.project:
            sym = _load_symbols(args.project)
        if sym is None or not sym.tasks:
            sym = SymbolTable(header.get("symbols"))
        tl = timeline.build_timeline(records)
        for f in verify.run_runtime(tl, sym, rules, handled, st):
            f.file = f.file or ""
            f.message = "%s: %s" % (pathlib.Path(path).name, f.message) if len(args.trace) > 1 else f.message
            findings.append(f)

    findings = verify.ordered(verify.apply_ignore(findings, rules))
    errors, warnings = verify.summary(findings)
    if args.format == "json":
        json.dump({"errors": errors, "warnings": warnings,
                   "findings": [f.to_dict() for f in findings]}, out, indent=1)
        out.write("\n")
    else:
        for f in findings:
            out.write(f.text() + "\n")
        out.write("%d error%s, %d warning%s\n" % (errors, "" if errors == 1 else "s",
                                                  warnings, "" if warnings == 1 else "s"))
    if errors or (args.strict and warnings):
        return 1
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
    p.add_argument("--baud", type=int, help="default: the project's --console-baud, else 115200")
    p.add_argument("-o", "--output", default="run.akt")
    p.add_argument("--duration", type=float, help="seconds, default until Ctrl-C")
    p.add_argument("-q", "--quiet", action="store_true", help="do not echo console text")
    p.add_argument("--flash", metavar="TARGET",
                   help="flash this .bin or built project first, then record from boot")
    p.add_argument("--sym", help="symbols.json or built project, stored in the .akt file "
                                 "(default: the --flash project)")
    p.set_defaults(func=cmd_record)

    p = subparsers.add_parser("dump", help="print the trace records of a .akt or raw capture")
    p.add_argument("file")
    p.add_argument("--sym", help="symbols.json or built project, instead of the names in the file")
    p.set_defaults(func=cmd_dump)

    p = subparsers.add_parser("build", help="build an AK project with akt trace, project untouched")
    p.add_argument("project", help="project folder, or its application/ folder")
    p.add_argument("--tools", help="folder holding gcc-arm-none-eabi-10.3-2021.10-x86_64-linux "
                                   "(WORKSPACE_TOOLS_PATH of the AK Makefile)")
    p.add_argument("--gcc", help="toolchain folder (GCC_PATH), for projects that hard-code it")
    p.add_argument("--ring", type=int, help="bytes of RAM for queued records (default 512)")
    p.add_argument("--console-baud", type=int, metavar="BAUD",
                   help="console UART rate (SYS_CONSOLE_BAUDRATE), e.g. 460800 for busy games; "
                        "remembered, record and serve use it for this project")
    p.add_argument("--clean", action="store_true", help="drop the cached objects first")
    p.set_defaults(func=cmd_build)

    p = subparsers.add_parser("verify", help="check AK event-driven rules on the sources and traces")
    p.add_argument("project", nargs="?", help="project folder, for the static rules S01 to S12")
    p.add_argument("--trace", action="append", help=".akt capture for the runtime rules R01 to R08 "
                                                    "(can be given more than once)")
    p.add_argument("--rules", help="rules file, see akscope/rules/default.yml")
    p.add_argument("--base", help="AK base kit folder (default: ak-base-kit-stm32l151 next to the project)")
    p.add_argument("--all", action="store_true", help="also report code that comes unchanged from the base kit")
    p.add_argument("--no-static", action="store_true", help="only the runtime rules")
    p.add_argument("--tools", help="WORKSPACE_TOOLS_PATH of the AK Makefile")
    p.add_argument("--gcc", help="toolchain folder (GCC_PATH)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    p.set_defaults(func=cmd_verify)

    p = subparsers.add_parser("serve", help="open the web UI on http://127.0.0.1:8730")
    p.add_argument("file", nargs="?", help=".akt or raw capture to show")
    p.add_argument("--project", help="project built by akscope build: names, and the firmware "
                                     "the Flash button writes")
    p.add_argument("--sym", help="symbols.json or built project, instead of the names in the file")
    p.add_argument("--dev", help="serial port preselected in the page, e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, help="default: the project's --console-baud, else 115200")
    p.add_argument("--captures", default="captures", help="folder for live captures")
    p.add_argument("--rules", help="rules file for the Verify tab")
    p.add_argument("--base", help="AK base kit folder for the static rules")
    p.add_argument("--port", type=int, default=8730, help="HTTP port")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = subparsers.add_parser("app", help="the same UI in a desktop window (GTK 3, WebKit2GTK 4.0)")
    p.add_argument("file", nargs="?", help=".akt or raw capture to show")
    p.add_argument("--project", help="project built by akscope build: names, and the firmware "
                                     "the Flash button writes")
    p.add_argument("--sym", help="symbols.json or built project, instead of the names in the file")
    p.add_argument("--dev", help="serial port preselected in the page, e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, help="default: the project's --console-baud, else 115200")
    p.add_argument("--captures", default="captures", help="folder for live captures")
    p.add_argument("--rules", help="rules file for the Verify tab")
    p.add_argument("--base", help="AK base kit folder for the static rules")
    p.add_argument("--inspector", action="store_true", help="allow the WebKit inspector (right click)")
    p.set_defaults(func=cmd_app)

    p = subparsers.add_parser("install-desktop", help="add ak-scope to the desktop menu (~/.local/share)")
    p.add_argument("--project", help="open this project from the menu entry")
    p.set_defaults(func=cmd_install_desktop)

    p = subparsers.add_parser("flash", help="flash a kit with ak-flash, retrying on failure")
    p.add_argument("--port", required=True)
    p.add_argument("target", help=".bin file, or a project folder built by akscope build")
    p.add_argument("--attempts", type=int, default=4)
    p.set_defaults(func=cmd_flash)
