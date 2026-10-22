"""Task and signal names for an AK project, from preprocessed sources.

Input is the output of the project's own compiler run with -E and the project's flags, so
#if / #ifdef are already resolved and macros already expanded.

A signal value is only unique inside one task (each task numbers its enum from
AK_USER_DEFINE_SIG), so a name is found per (task, sig) with, in this order:

1. `case NAME:` inside `switch (msg->sig)` of that task's handler
2. `task_post_*_msg(TASK, NAME, ...)`, `timer_set(TASK, NAME, ...)`, `timer_remove_attr(TASK, NAME)`
3. the only signal name with that value among all names used in 1 and 2, anywhere;
   shown with a trailing "?" because it is a guess
"""

import ast
import operator
import re

_IDENT = r"[A-Za-z_]\w*"

_ENUM_RE = re.compile(r"\benum\b\s*(?:" + _IDENT + r")?\s*\{([^{}]*)\}")
_HANDLER_RE = re.compile(r"\b(" + _IDENT + r")\s*\(\s*ak_msg_t\s*\*\s*(" + _IDENT + r")\s*\)\s*\{")
_CASE_RE = re.compile(r"\bcase\s+(" + _IDENT + r")\s*:")
_POST_RE = re.compile(r"\b(?:task_post_pure_msg|task_post_common_msg|task_post_dynamic_msg|"
                      r"timer_set|timer_remove_attr)\s*\(\s*(" + _IDENT + r")\s*,\s*(" + _IDENT + r")\b")
_TABLE_RE = re.compile(r"\bapp_task_table\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;", re.S)
_ROW_RE = re.compile(r"\{\s*(" + _IDENT + r")\s*,\s*([^,{}]+?)\s*,\s*([^{}]+?)\s*\}")

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.LShift: operator.lshift,
    ast.RShift: operator.rshift, ast.BitOr: operator.or_, ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}


def strip_literals(text):
    """Blank out string and char literals so their braces and words are not parsed."""
    return re.sub(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', '""', text)


def strip_line_markers(text):
    return re.sub(r"^#.*$", "", text, flags=re.M)


def eval_int(expr, names):
    """Evaluate a C integer constant expression. Returns None if it cannot."""
    expr = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\b", r"\1", expr.strip())
    expr = expr.replace("/", "//")
    # drop simple casts like (uint8_t)
    expr = re.sub(r"\(\s*(?:unsigned\s+|signed\s+)?" + _IDENT + r"\s*\)\s*(?=[\w(])", "", expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise ValueError(node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        raise ValueError(ast.dump(node))

    try:
        return ev(tree)
    except (ValueError, ZeroDivisionError):
        return None


def parse_enums(text, values=None):
    """All enumerators in text, as {name: value}. values seeds known names."""
    values = dict(values or {})
    for m in _ENUM_RE.finditer(text):
        nxt = 0
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            name, _, expr = item.partition("=")
            name = name.strip()
            if not re.fullmatch(_IDENT, name):
                nxt = None
                continue
            if expr:
                nxt = eval_int(expr, values)
            if nxt is None:
                continue
            values[name] = nxt
            nxt += 1
    return values


def _block(text, open_index):
    """Text inside the brace that opens at open_index."""
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
    return text[open_index + 1:]


def parse_handlers(text):
    """{function name: [case names in switch (msg->sig)]} for every f(ak_msg_t* p) {...}."""
    out = {}
    for m in _HANDLER_RE.finditer(text):
        name, param = m.group(1), m.group(2)
        if name in ("if", "while", "switch", "for"):
            continue
        body = _block(text, m.end() - 1)
        cases = []
        sw = re.compile(r"\bswitch\s*\(\s*" + re.escape(param) + r"\s*->\s*sig\s*\)\s*\{")
        for s in sw.finditer(body):
            sw_body = _block(body, s.end() - 1)
            # a nested switch on another value has its own case labels, drop it first
            cases += _CASE_RE.findall(_drop_nested_switches(sw_body, param))
        out.setdefault(name, [])
        out[name] += [c for c in cases if c not in out[name]]
    return out


def _drop_nested_switches(body, param):
    """Remove switch blocks that are not on param->sig, keep the rest."""
    keep = re.compile(r"\bswitch\s*\(\s*" + re.escape(param) + r"\s*->\s*sig\s*\)")
    out = []
    i = 0
    for m in re.finditer(r"\bswitch\s*\([^)]*\)\s*\{", body):
        if m.start() < i or keep.match(body, m.start()):
            continue
        out.append(body[i:m.start()])
        inner = _block(body, m.end() - 1)
        i = m.end() + len(inner) + 1
    out.append(body[i:])
    return "".join(out)


def parse_posts(text):
    """[(task id name, sig name)] from post and timer calls."""
    return _POST_RE.findall(text)


def parse_task_table(text, values):
    """[(task id, id name, handler name or None, priority)] from app_task_table."""
    m = _TABLE_RE.search(text)
    if not m:
        return []
    rows = []
    for id_name, pri, fn in _ROW_RE.findall(m.group(1)):
        if id_name not in values:
            continue
        fn = fn.strip()
        handler = fn if re.fullmatch(_IDENT, fn) else None
        rows.append((values[id_name], id_name, handler, eval_int(pri, values)))
    return rows


def build_symbols(task_list_text, app_texts, app_ver=None):
    """Combine preprocessed task_list.cpp and the other app sources into a symbol table."""
    task_list_text = strip_literals(strip_line_markers(task_list_text))
    app_texts = [strip_literals(strip_line_markers(t)) for t in app_texts]
    every = [task_list_text] + app_texts

    values = {}
    for t in every:
        values = parse_enums(t, values)

    rows = parse_task_table(task_list_text, values)
    tasks = {}
    handler_task = {}
    for task_id, id_name, handler, pri in rows:
        if id_name == "AK_TASK_EOT_ID":
            continue
        tasks[task_id] = {"name": id_name, "handler": handler, "pri": pri}
        if handler:
            handler_task[handler] = task_id

    handlers, posts = {}, []
    for t in app_texts + [task_list_text]:
        for fn, cases in parse_handlers(t).items():
            handlers.setdefault(fn, [])
            handlers[fn] += [c for c in cases if c not in handlers[fn]]
        posts += parse_posts(t)

    per_task = {}

    def add(task_id, name, rank):
        if name not in values:
            return
        slot = per_task.setdefault(task_id, {}).setdefault(values[name], {})
        slot[name] = min(rank, slot.get(name, rank))

    for fn, cases in handlers.items():
        if fn in handler_task:
            for c in cases:
                add(handler_task[fn], c, 1)
    for task_name, sig_name in posts:
        if task_name in values:
            add(values[task_name], sig_name, 2)

    # names seen as signals anywhere, for the last-resort lookup by value
    used = {c for cases in handlers.values() for c in cases} | {s for _, s in posts}
    by_value = {}
    for n in used:
        if n in values:
            by_value.setdefault(values[n], set()).add(n)

    signals = {}
    for task_id, sigs in per_task.items():
        signals[task_id] = {}
        for sig, names in sigs.items():
            best = min(names.values())
            signals[task_id][sig] = sorted(n for n, r in names.items() if r == best)[0]

    fallback = {v: sorted(ns)[0] for v, ns in by_value.items() if len(ns) == 1}
    return {
        "tasks": tasks,
        "signals": signals,
        "fallback_signals": fallback,
        "app_ver": app_ver,
    }


def parse_app_ver(macros_text):
    """APP_VER from `-dM -E` output, e.g. #define APP_VER {0, 0, 0, 3}."""
    m = re.search(r"^#define\s+APP_VER\s+\{([^}]*)\}", macros_text, re.M)
    if not m:
        return None
    parts = [eval_int(p, {}) for p in m.group(1).split(",")]
    if len(parts) != 4 or None in parts:
        return None
    return parts


class SymbolTable:
    """Lookup over the dict from build_symbols(), also after a JSON round trip."""

    SPECIAL = {0xEE: "isr", 0xEF: "idle"}

    def __init__(self, data=None):
        data = data or {}
        self.tasks = {int(k): v for k, v in data.get("tasks", {}).items()}
        self.signals = {int(t): {int(s): n for s, n in sigs.items()}
                        for t, sigs in data.get("signals", {}).items()}
        self.fallback = {int(k): v for k, v in data.get("fallback_signals", {}).items()}
        self.cpu_hz = 32000000

    def task(self, task_id):
        if task_id in self.SPECIAL:
            return self.SPECIAL[task_id]
        t = self.tasks.get(task_id)
        return t["name"] if t else str(task_id)

    def signal_names(self):
        """Every signal name of the project."""
        names = set(self.fallback.values())
        for sigs in self.signals.values():
            names.update(sigs.values())
        return names

    def signal(self, task_id, sig):
        name = self.signals.get(task_id, {}).get(sig)
        if name:
            return name
        # only value-unique across the project, not known for this task: mark it as a guess
        if sig in self.fallback:
            return self.fallback[sig] + "?"
        return str(sig)
