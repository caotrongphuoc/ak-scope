"""Check that firmware follows the AK event-driven rules.

static.py   rules S01 to S12 on the project sources
runtime.py  rules R01 to R08 on a recorded trace
"""

import pathlib
import re
from dataclasses import asdict, dataclass

DEFAULT_RULES = pathlib.Path(__file__).resolve().parent.parent / "rules" / "default.yml"

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    file: str = ""  # static rules
    line: int = 0
    t_us: float = None  # runtime rules: time of the first or worst case
    exec: int = -1  # runtime rules: exec index in the timeline, for the web UI

    def where(self):
        if self.file:
            return "%s:%d" % (self.file, self.line) if self.line else self.file
        if self.t_us is not None:
            return "@ %.3f ms" % (self.t_us / 1000)
        return ""

    def text(self):
        w = self.where()
        return "%s%s %s %s" % (w + ": " if w else "", self.severity, self.rule, self.message)

    def to_dict(self):
        return asdict(self)


def load_rules(path=None):
    import yaml

    with open(DEFAULT_RULES) as f:
        rules = yaml.safe_load(f)
    if path:
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        if not isinstance(user, dict):
            raise ValueError("%s: expected a mapping at the top" % path)
        rules.update(user)
    rules["ignore"] = rules.get("ignore") or []
    rules["expect"] = rules.get("expect") or []
    return rules


def apply_ignore(findings, rules):
    out = []
    for f in findings:
        skip = False
        for ig in rules["ignore"]:
            if isinstance(ig, str):
                ig = {"rule": ig}
            if ig.get("rule") != f.rule:
                continue
            m = ig.get("match")
            if not m or m in f.message or m in f.where():
                skip = True
                break
        if not skip:
            out.append(f)
    return out


def parse_duration_us(value):
    """'60ms', '500us', '2s' or a number of microseconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*(us|ms|s)?\s*", str(value))
    if not m:
        raise ValueError("bad duration %r, use for example 60ms" % value)
    n = float(m.group(1))
    return n * {"us": 1, "ms": 1e3, "s": 1e6, None: 1}[m.group(2)]


def ordered(findings):
    """Errors first; static findings by file and line, then runtime ones by time."""
    return sorted(findings, key=lambda f: (f.severity != ERROR, f.t_us is not None, f.file, f.line,
                                           f.t_us or 0, f.rule))


def summary(findings):
    errors = sum(1 for f in findings if f.severity == ERROR)
    warnings = sum(1 for f in findings if f.severity == WARNING)
    return errors, warnings


def find_base_kit(project_root):
    """The AK base kit checked out next to the project, if there is one."""
    p = pathlib.Path(project_root).resolve().parent / "ak-base-kit-stm32l151"
    return p if (p / "application" / "sources" / "ak").is_dir() else None


class StaticResult:
    def __init__(self, findings, handled):
        self.findings = findings
        self.handled = handled  # task id -> set of signal values, for R06


def run_static(project, tools=None, gcc=None, base=None, include_base=False, log=print):
    """Static rules on a project folder. Returns StaticResult."""
    from akscope.build import Builder
    from akscope.verify import static

    b = Builder(project, tools=tools, gcc=gcc, log=log)
    b.prepare()
    log("akscope: preprocessing %s" % b.app)
    texts = b.preprocess(static.SCAN_PREFIXES, static.SCAN_EXTRA)
    model = static.Project(texts)
    # sizeof() lengths of common messages, evaluated by the project's compiler for S09
    model.common_sizes = {}
    for src, probes in static.size_probes(model).items():
        for (key, _), n in zip(probes, b.probe_sizes(src, [e for _, e in probes])):
            if n is not None:
                model.common_sizes[key] = n
    root = b.app.parent
    if base is None:
        base = find_base_kit(root)
        if base:
            log("akscope: comparing with the base kit at %s" % base)
    findings = static.run(model, root, base, include_base)
    handled = {}
    for handler, task_id in model.handlers.items():
        values = model.handled_values(handler)
        if values is not None:
            handled[task_id] = values
    return StaticResult(findings, handled)


def run_runtime(timeline_, sym, rules, handled=None, stream_stats=None):
    from akscope.verify import runtime

    return runtime.run(timeline_, sym, rules, handled, stream_stats)
