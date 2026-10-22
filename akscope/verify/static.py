"""Static rules S01 to S12 on an AK project, from the project's own preprocessor output.

The model: every function defined in the project sources, a call graph, the task and polling
tables, the interrupt vector table, enumerators and file scope variables. A handler reaches
the functions it calls and the message functions (f(ak_msg_t*)) it names, such as the screen
handlers that the display task hands to SCREEN_TRAN(); those run in the same task.
"""

import difflib
import pathlib
import re
from collections import deque

from akscope.core import symbols
from akscope.core.model import AK_USER_DEFINE_SIG
from akscope.core.cparse import IDENT, Unit
from akscope.verify import ERROR, WARNING, Finding

# source folders read for the model; platform is only read for system.c (vector table, ISRs)
SCAN_PREFIXES = ("sources/app/", "sources/driver/", "sources/networks/", "sources/common/",
                 "sources/sys/")
SCAN_EXTRA = ("sources/platform/stm32l/system.c",)

# folders that belong to the framework (AK guardrail "do not modify")
FRAMEWORK_DIRS = ("sources/ak", "sources/sys", "sources/common", "sources/networks",
                  "sources/platform", "sources/libraries")
# allowed edits inside framework folders: the board IO layer and the kernel configuration
FRAMEWORK_ALLOWED = ("sources/platform/stm32l/io_cfg.c", "sources/platform/stm32l/io_cfg.h",
                     "sources/ak/ak.cfg.mk")

DELAY_CALLS = {"sys_ctrl_delay_ms", "sys_ctrl_delay_us", "sys_ctrl_delay", "delay",
               "delayMicroseconds", "delay_ms", "delay_us", "HAL_Delay", "_delay_ms", "_delay_us"}
# storage writes that wait for the hardware before they return; seen on the kit: a game saving
# its best score with eeprom_write() inside a handler held every task for 61 ms
STORAGE_CALLS = {"eeprom_write", "eeprom_erase", "io_eeprom_write", "io_eeprom_erase", "flash_write",
                 "flash_erase_sector", "flash_erase_block_32k", "flash_erase_block_64k",
                 "flash_erase_full", "flash_erase_synchronous", "flash_wait_to_idle"}
POST_CALLS = ("task_post_pure_msg", "task_post_common_msg", "task_post_dynamic_msg", "task_post",
              "timer_set", "timer_remove_attr")
KERNEL_CALLS_IN_ISR = set(POST_CALLS) | {"get_pure_msg", "get_common_msg", "get_dynamic_msg"}
DYNAMIC_CALLS = {"get_dynamic_msg", "task_post_dynamic_msg"}

_PERIPH = re.compile(r"\b(?:GPIO|RCC|USART|SPI|I2C|ADC|DAC|TIM|EXTI|DMA|IWDG|WWDG|RTC|PWR|SYSCFG|COMP)"
                     r"_[A-Za-z]\w*\s*\(")
_ARDUINO = re.compile(r"\b(?:pinMode|digitalWrite|digitalRead|analogRead|analogWrite)\s*\(")
# GPIOA->ODR after -E is ((GPIO_TypeDef *) (...))->ODR
_REGISTER = re.compile(r"_TypeDef\s*\*\s*\)[^;]{0,80}?\)\s*->\s*[A-Z][A-Z0-9_]*\b")
_WHILE = re.compile(r"\bwhile\s*\(")
_POST_ARGS = re.compile(r"\b(task_post_pure_msg|task_post_common_msg|task_post_dynamic_msg|timer_set|"
                        r"timer_remove_attr)\s*\(\s*(" + IDENT + r")\s*,\s*(" + IDENT + r")\b")
_WRITE_OPS = r"(?:=(?!=)|\+=|-=|\*=|/=|%=|\|=|&=|\^=|<<=|>>=|\+\+|--)"


def busy_waits(fn):
    """(file, line) of `while (cond);` and `while (cond) {}` in a function body. The condition
    is matched by counting parentheses: register waits nest deep after -E."""
    out = []
    body = fn.body
    for m in _WHILE.finditer(body):
        d = 0
        end = -1
        for i in range(m.end() - 1, len(body)):
            if body[i] == "(":
                d += 1
            elif body[i] == ")":
                d -= 1
                if d == 0:
                    end = i
                    break
            elif body[i] in ";{}":
                break
        if end < 0:
            continue
        if re.match(r"\s*(?:;|\{\s*\})", body[end + 1:]):
            # do { } while (x); is a normal loop, not a busy wait
            if re.search(r"\}\s*$", body[:m.start()]):
                continue
            f, ln = fn.unit.where(fn.brace + 1 + m.start())
            out.append((_norm(f), ln))
    return out


def _norm(path):
    return path[2:] if path.startswith("./") else path


class Project:
    def __init__(self, texts):
        """texts: {source path: -E output} from Builder.preprocess(SCAN_PREFIXES, SCAN_EXTRA)."""
        self.units = {f: Unit(t, f) for f, t in texts.items()}
        self.funcs = {}  # short name -> [Function], project files only, defined once
        seen = set()
        headers_done = set()
        for u in self.units.values():
            regions = u.regions("sources/", headers_done)
            headers_done |= {f for _, _, f in regions if not f.endswith((".c", ".cpp"))}
            for fn in u.functions(regions):
                fn.file = _norm(fn.file)
                if not fn.file.startswith("sources/"):
                    continue
                key = (fn.file, fn.line, fn.name)
                if key in seen:
                    continue
                seen.add(key)
                self.funcs.setdefault(fn.short, []).append(fn)

        self.values = {}
        for u in self.units.values():
            self.values = symbols.parse_enums(u.code, self.values)

        self.enum_loc = {}
        for u in self.units.values():
            for name, off in u.enumerators():
                f, ln = u.where(off)
                f = _norm(f)
                if f.startswith("sources/") and name not in self.enum_loc:
                    self.enum_loc[name] = (f, ln)

        tl = next((u for f, u in self.units.items() if f.endswith("/task_list.cpp")), None)
        self.task_list = tl
        self.rows = symbols.parse_task_table(tl.code, self.values) if tl else []
        self.handlers = {}  # handler name -> task id
        self.task_names = {}
        for task_id, id_name, handler, pri in self.rows:
            if id_name == "AK_TASK_EOT_ID":
                continue
            self.task_names[task_id] = id_name
            if handler:
                self.handlers[handler] = task_id
        self.polling = self._polling_functions()
        self.isrs = self._isr_functions()

        self.msg_funcs = {n for n, defs in self.funcs.items() if any(d.msg_param() for d in defs)}
        self._reach = {}
        self._edges = {}

    # model helpers

    def _polling_functions(self):
        if not self.task_list:
            return set()
        m = re.search(r"\bapp_task_polling_table\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;", self.task_list.code, re.S)
        if not m:
            return set()
        # AK_ENABLE is (0x01) after -E
        return set(re.findall(r"\{\s*\w+\s*,\s*[^,{}]+,\s*(" + IDENT + r")\s*\}", m.group(1)))

    def _isr_functions(self):
        for u in self.units.values():
            m = re.search(r"\bisr_vector\s*\[\s*\]\s*\)?\s*\(\s*\)\s*=\s*\{", u.code)
            if not m:
                continue
            body = u.code[m.end():u.block_end(m.end() - 1)]
            names = re.findall(IDENT, body)
            return {n for n in names if n in self.funcs and n != "reset_handler"}
        return set()

    def defs(self, name):
        return self.funcs.get(name, [])

    def edges(self, fn, mentions=True):
        key = (id(fn), mentions)
        if key not in self._edges:
            out = {c for c in fn.calls() if c in self.funcs}
            if mentions:
                # message functions handed over by name run in the same task (screens, FSM states)
                out |= {m for m in fn.mentions() if m in self.msg_funcs}
            out.discard(fn.short)
            self._edges[key] = out
        return self._edges[key]

    def reach(self, roots, mentions=True, skip=("sources/ak/",)):
        """{function name: path from a root} for everything reachable from roots."""
        key = (tuple(sorted(roots)), mentions, skip)
        if key not in self._reach:
            self._reach[key] = self._walk(roots, mentions, skip)
        return self._reach[key]

    def _walk(self, roots, mentions, skip):
        path = {r: [r] for r in roots if r in self.funcs}
        q = deque(path)
        while q:
            n = q.popleft()
            for fn in self.defs(n):
                if fn.file.startswith(skip):
                    continue
                for m in self.edges(fn, mentions):
                    if m not in path:
                        path[m] = path[n] + [m]
                        q.append(m)
        return path

    def all_functions(self):
        for defs in self.funcs.values():
            yield from defs

    def handled_values(self, handler):
        """Signal values handled by a task: sig cases in its handler and the message functions
        it reaches. None if none of them switches on msg->sig (FSM or TSM style), or if the
        handler runs code for every message (statements after its switch, a busy default).

        A handler that passes msg on through a function pointer (scr_mng_dispatch() calls the
        current screen) or fsm_dispatch/tsm_dispatch hands it to whatever function is current
        at run time; the cases of every message function that no other task reaches count.
        """
        reach = self.reach([handler])
        # code that runs for every message: no signal is "unhandled"
        if any(fn.catch_all() for fn in self.defs(handler)):
            return None
        cases = []
        delegates = False
        for name in reach:
            for fn in self.defs(name):
                cases += fn.sig_cases()
                p = fn.msg_param()
                # msg passed on through a function pointer or an FSM/TSM dispatcher
                if p and re.search(r"(?:->|\.)\s*\w+\s*\(\s*" + p + r"\s*\)|\(\s*\*\s*\w+\s*\)\s*\(\s*" + p +
                                   r"\s*\)|\b(?:fsm_dispatch|tsm_dispatch)\s*\([^;]*\b" + p + r"\b", fn.body):
                    delegates = True
        if delegates:
            others = set()
            for h in self.handlers:
                if h != handler:
                    others |= set(self.reach([h]))
            for name in self.msg_funcs - others - set(self.handlers):
                for fn in self.defs(name):
                    cases += fn.sig_cases()
        if not cases:
            return None
        return {self.values[c] for c in cases if c in self.values}

    def posts(self):
        """[(call, task name, sig name, file, line)] of post and timer calls in project code."""
        out = []
        for fn in self.all_functions():
            if fn.file.startswith("sources/ak/"):
                continue
            base = fn.brace + 1
            for m in _POST_ARGS.finditer(fn.body):
                f, ln = fn.unit.where(base + m.start())
                out.append((m.group(1), m.group(2), m.group(3), _norm(f), ln))
        return out


# rules

def s01_framework_unchanged(project_root, base_root):
    """S01: framework folders must match the AK base kit."""
    findings = []
    if base_root is None:
        return [Finding("S01", WARNING, "no AK base kit to compare with, pass --base "
                        "<ak-base-kit-stm32l151 folder>")]
    app = pathlib.Path(project_root) / "application"
    base_app = pathlib.Path(base_root) / "application"
    pairs = [(app / d, base_app / d, "application/" + d) for d in FRAMEWORK_DIRS]
    pairs.append((pathlib.Path(project_root) / "boot", pathlib.Path(base_root) / "boot", "boot"))
    allowed = {"application/" + a for a in FRAMEWORK_ALLOWED}

    for mine, theirs, rel in pairs:
        if not theirs.is_dir():
            continue
        mine_files = {p.relative_to(mine).as_posix() for p in mine.rglob("*") if p.is_file()} if mine.is_dir() else set()
        their_files = {p.relative_to(theirs).as_posix() for p in theirs.rglob("*") if p.is_file()}
        for f in sorted(mine_files | their_files):
            path = "%s/%s" % (rel, f)
            if path in allowed or "/build_" in path:
                continue
            if f not in their_files:
                findings.append(Finding("S01", ERROR, "file added to a framework folder", path))
            elif f not in mine_files:
                findings.append(Finding("S01", WARNING, "file removed from a framework folder", path))
            else:
                a = (mine / f).read_bytes().replace(b"\r\n", b"\n")
                b = (theirs / f).read_bytes().replace(b"\r\n", b"\n")
                if a != b:
                    line = _first_diff_line(a, b)
                    findings.append(Finding("S01", ERROR, "framework file changed from the AK base kit",
                                            path, line))
    return findings


def _first_diff_line(a, b):
    la, lb = a.split(b"\n"), b.split(b"\n")
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return i + 1
    return min(len(la), len(lb)) + 1


def s02_blocking(p):
    """S02: no delay, busy wait or blocking storage write in code that runs inside a task
    handler."""
    findings = []
    roots = [n for n in p.msg_funcs if any(not d.file.startswith("sources/ak/") for d in p.defs(n))]
    reach = p.reach(roots)
    reported = set()
    for name, chain in sorted(reach.items()):
        for fn in p.defs(name):
            if not fn.file.startswith(("sources/app/", "sources/driver/", "sources/networks/",
                                       "sources/common/")):
                continue
            via = " (via %s)" % " -> ".join(chain) if len(chain) > 1 else ""
            for call in sorted(fn.calls() & DELAY_CALLS):
                for f, ln, _ in fn.find(r"\b" + call + r"\s*\("):
                    key = (f, ln)
                    if key not in reported:
                        reported.add(key)
                        findings.append(Finding("S02", ERROR, "%s() blocks the handler%s" % (call, via),
                                                _norm(f), ln))
            for call in sorted(fn.calls() & STORAGE_CALLS):
                for f, ln, _ in fn.find(r"\b" + call + r"\s*\("):
                    key = (f, ln)
                    if key not in reported:
                        reported.add(key)
                        findings.append(Finding("S02", WARNING, "%s() waits for the memory to finish "
                                                "inside a handler%s; do it in steps driven by a timer, "
                                                "or out of the handler" % (call, via), _norm(f), ln))
            for f, ln in busy_waits(fn):
                key = (f, ln)
                if key not in reported:
                    reported.add(key)
                    findings.append(Finding("S02", WARNING, "busy wait loop in a handler%s" % via,
                                            _norm(f), ln))
    return findings


def s03_io_outside_io_cfg(p):
    """S03: GPIO, peripheral and register access only in platform/stm32l/io_cfg.c."""
    findings = []
    for fn in p.all_functions():
        if not fn.file.startswith(("sources/app/", "sources/driver/")):
            continue
        for pat, what in ((_PERIPH, "peripheral library call"), (_ARDUINO, "pin access"),
                          (_REGISTER, "register access")):
            for f, ln, text in fn.find(pat.pattern):
                f = _norm(f)
                if f.startswith(("sources/app/", "sources/driver/")):
                    call = text.split("(")[0].strip() if what != "register access" else text.split("->")[-1].strip()
                    findings.append(Finding("S03", ERROR, "%s %s outside io_cfg.c, move it to "
                                            "platform/stm32l/io_cfg.c and inject it" % (what, call), f, ln))
    return findings


def s04_task_prefix(p):
    """S04: the task_ prefix is only for tasks in task_list.h."""
    allowed = set(p.handlers) | p.polling
    findings = []
    for fn in p.all_functions():
        if fn.short.startswith("task_") and fn.short not in allowed and \
                fn.file.startswith(("sources/app/", "sources/driver/")):
            findings.append(Finding("S04", WARNING, "%s() uses the task_ prefix but is not a task in "
                                    "task_list.h" % fn.short, fn.file, fn.line))
    return findings


def s05_user_signals(p):
    """S05: user signals start at AK_USER_DEFINE_SIG (10)."""
    findings = []
    used = {s for _, _, s, _, _ in p.posts()}
    for fn in p.all_functions():
        used.update(fn.sig_cases())
    for name in sorted(used):
        v = p.values.get(name)
        loc = p.enum_loc.get(name)
        if v is not None and v < AK_USER_DEFINE_SIG and loc and loc[0].startswith("sources/app/"):
            findings.append(Finding("S05", WARNING, "signal %s = %d is below AK_USER_DEFINE_SIG (10), "
                                    "in the kernel's range" % (name, v), loc[0], loc[1]))
    return findings


def s06_priorities(p):
    """S06: task priority between TASK_PRI_LEVEL_1 and TASK_PRI_LEVEL_7."""
    findings = []
    for task_id, id_name, handler, pri in p.rows:
        if id_name == "AK_TASK_EOT_ID":
            continue
        if pri is None or not 1 <= pri <= 7:
            f, ln = _table_loc(p, id_name)
            findings.append(Finding("S06", ERROR, "%s has priority %s, use TASK_PRI_LEVEL_1 to 7 "
                                    "(0 is reserved)" % (id_name, pri), f, ln))
    return findings


def _table_loc(p, id_name):
    u = p.task_list
    m = re.search(r"\{\s*" + re.escape(id_name) + r"\s*,", u.code)
    if not m:
        return "", 0
    f, ln = u.where(m.start())
    return _norm(f), ln


def s07_unhandled_posts(p):
    """S07: a signal posted to a task must be handled by that task."""
    findings = []
    by_task = {}
    for handler, task_id in p.handlers.items():
        by_task[p.task_names[task_id]] = (handler, p.handled_values(handler))
    for call, task, sig, f, ln in p.posts():
        if task not in by_task or sig not in p.values:
            continue
        handler, handled = by_task[task]
        if handled is None:
            continue  # FSM / TSM style handler, cases not visible
        if p.values[sig] not in handled:
            findings.append(Finding("S07", ERROR, "%s posts %s to %s, but %s has no case for it"
                                    % (call, sig, task, handler), f, ln))
    return findings


def s08_dead_signals(p):
    """S08: a signal declared next to used signals but never posted, armed or handled."""
    used = set()
    for _, _, s, _, _ in p.posts():
        used.add(s)
    mentioned = set()
    for fn in p.all_functions():
        used.update(fn.sig_cases())
        mentioned |= fn.mentions()
    findings = []
    for u in p.units.values():
        for m in re.finditer(r"\benum\b\s*(?:" + IDENT + r")?\s*\{", u.code):
            end = u.block_end(m.end() - 1)
            names = re.findall(r"(?:^|,)\s*(" + IDENT + r")", u.code[m.end():end])
            if not any(n in used for n in names):
                continue
            for n in names:
                if n in mentioned or n in used or re.search(r"_(MAX|COUNT|NUM|END|LAST|SIZE)$", n):
                    continue
                loc = p.enum_loc.get(n)
                if loc and loc[0].startswith("sources/app/"):
                    findings.append(Finding("S08", WARNING, "signal %s is never posted, armed or "
                                            "handled" % n, loc[0], loc[1]))
    # the same header is in many units
    seen, out = set(), []
    for f in findings:
        if (f.file, f.line) not in seen:
            seen.add((f.file, f.line))
            out.append(f)
    return out


def _split_args(text):
    """Top level comma split of a call's argument text."""
    out, d, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
        if ch == "," and d == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def _decl_type(fn, name, before):
    """Type of a local variable or parameter `name` declared before offset `before` in fn, as a
    type-id usable in sizeof(), or None."""
    text = fn.params + ";" + fn.body[:before]
    pat = re.compile(r"(?:^|[;{}(,])\s*((?:(?:const|volatile|struct|union|enum|unsigned|signed|static)\s+)*"
                     r"[A-Za-z_][\w:]*(?:\s*<[^;{}()]*?>)?(?:\s*\*+)?)\s*\b" + re.escape(name) +
                     r"\s*(\[[^\]]*\])?\s*(?:=|;|,|\)|$)")
    found = None
    for m in pat.finditer(text):
        t = re.sub(r"\b(static|const|volatile)\s+", "", m.group(1)).strip()
        if t not in ("return", "case", "else", "sizeof") and not t.endswith(("return", "sizeof")):
            found = t + (m.group(2) or "")
    return found


def common_posts(p):
    """[(unit name, file, line, length text, size expression or None)] for task_post_common_msg
    calls in app and driver code. The size expression is what to put in sizeof() at file scope:
    sizeof(local) becomes sizeof(type of local)."""
    out = []
    for fn in p.all_functions():
        if not fn.file.startswith(("sources/app/", "sources/driver/")):
            continue
        base = fn.brace + 1
        for m in re.finditer(r"\btask_post_common_msg\s*\(", fn.body):
            close = _matching_paren(fn.body, m.end() - 1)
            if close < 0:
                continue
            args = _split_args(fn.body[m.end():close])
            if len(args) != 4:
                continue
            length = args[3]
            f, ln = fn.unit.where(base + m.start())
            size = None
            sm = re.fullmatch(r"sizeof\s*\(\s*(.+?)\s*\)", length, re.S)
            if sm:
                inner = sm.group(1)
                if re.fullmatch(IDENT, inner):
                    size = _decl_type(fn, inner, m.start()) or inner
                else:
                    size = inner
            out.append((fn.unit.name, _norm(f), ln, length, size))
    return out


def _matching_paren(text, open_index):
    d = 0
    for i in range(open_index, len(text)):
        if text[i] == "(":
            d += 1
        elif text[i] == ")":
            d -= 1
            if d == 0:
                return i
    return -1


def size_probes(p):
    """{unit name: [((file, line), size expression)]} for the compiler to evaluate."""
    out = {}
    for unit, f, ln, length, size in common_posts(p):
        if size is not None:
            out.setdefault(unit, []).append(((f, ln), size))
    return out


def s09_common_payload(p):
    """S09: common message payload at most 64 bytes. Literal lengths are read directly,
    sizeof() lengths come from the compiler (p.common_sizes, filled by run_static)."""
    sizes = getattr(p, "common_sizes", {})
    findings = []
    for unit, f, ln, length, size in common_posts(p):
        n = None
        if re.fullmatch(r"\(?\s*\d+\s*\)?", length):
            n = int(length.strip("() "))
        elif (f, ln) in sizes:
            n = sizes[(f, ln)]
        if n is not None and n > 64:
            findings.append(Finding("S09", ERROR, "common message of %d bytes (%s), the limit is "
                                    "AK_COMMON_MSG_DATA_SIZE (64); use a dynamic message" % (n, length), f, ln))
    return findings


def s10_shared_globals(p):
    """S10: a file scope variable written by more than one task."""
    globals_ = {}  # key -> (name, file, line, static)
    for u in p.units.values():
        for name, off, static in u.globals():
            f, ln = u.where(off)
            f = _norm(f)
            if not f.startswith(("sources/app/", "sources/driver/")) or f.endswith(".h"):
                continue
            key = (name, f) if static else (name, None)
            globals_.setdefault(key, (name, f, ln, static))
    if not globals_:
        return []
    names = {k[0] for k in globals_}
    writers = {}  # key -> {task id: (file, line, function)}
    for handler, task_id in p.handlers.items():
        for fname in p.reach([handler]):
            for fn in p.defs(fname):
                if not fn.file.startswith(("sources/app/", "sources/driver/")):
                    continue
                for n in names & fn.mentions():
                    pat = (r"(?:\b" + n + r"\b(?:\s*\[[^\]]*\]|\s*(?:\.|->)\s*\w+)*\s*" + _WRITE_OPS +
                           r"|(?:\+\+|--)\s*\b" + n + r"\b)")
                    hits = fn.find(pat)
                    if not hits:
                        continue
                    key = (n, fn.file) if (n, fn.file) in globals_ else (n, None)
                    if key not in globals_:
                        continue
                    writers.setdefault(key, {}).setdefault(task_id, (hits[0][0], hits[0][1], fn.short))
    findings = []
    for key, tasks in sorted(writers.items(), key=lambda kv: (globals_[kv[0]][1], globals_[kv[0]][2])):
        if len(tasks) < 2:
            continue
        name, f, ln, _ = globals_[key]
        detail = ", ".join("%s in %s() %s:%d" % (p.task_names.get(t, t), w[2], _norm(w[0]), w[1])
                           for t, w in sorted(tasks.items()))
        findings.append(Finding("S10", WARNING, "%s is written by %d tasks: %s; pass it in a message "
                                "instead" % (name, len(tasks), detail), f, ln))
    return findings


def s11_isr_bracket(p):
    """S11: an ISR that reaches the kernel calls task_entry_interrupt() and task_exit_interrupt()."""
    findings = []
    for isr in sorted(p.isrs):
        reach = p.reach([isr], mentions=False, skip=("sources/ak/", "sources/platform/stm32l/Libraries"))
        touches = None
        for name, chain in reach.items():
            for fn in p.defs(name):
                hit = fn.calls() & KERNEL_CALLS_IN_ISR
                if hit:
                    touches = (sorted(hit)[0], chain)
                    break
            if touches:
                break
        if not touches:
            continue
        for fn in p.defs(isr):
            calls = fn.calls()
            missing = [c for c in ("task_entry_interrupt", "task_exit_interrupt") if c not in calls]
            if missing:
                findings.append(Finding("S11", ERROR, "%s() reaches %s() (via %s) without %s()"
                                        % (isr, touches[0], " -> ".join(touches[1]), " and ".join(missing)),
                                        fn.file, fn.line))
    return findings


def s12_dynamic_in_isr(p):
    """S12: no dynamic message from an ISR (it allocates from the heap)."""
    findings = []
    for isr in sorted(p.isrs):
        reach = p.reach([isr], mentions=False)
        for name, chain in sorted(reach.items()):
            for fn in p.defs(name):
                for call in sorted(fn.calls() & DYNAMIC_CALLS):
                    for f, ln, _ in fn.find(r"\b" + call + r"\s*\("):
                        findings.append(Finding("S12", ERROR, "%s() in interrupt context (via %s)"
                                                % (call, " -> ".join(chain)), _norm(f), ln))
    return findings


RULES = (s02_blocking, s03_io_outside_io_cfg, s04_task_prefix, s05_user_signals, s06_priorities,
         s07_unhandled_posts, s08_dead_signals, s09_common_payload, s10_shared_globals,
         s11_isr_bracket, s12_dynamic_in_isr)


class BaseKitDiff:
    """Which lines of a project file are new compared to the AK base kit, computed per file
    on first use (only files that have findings get diffed)."""

    def __init__(self, project_root, base_root):
        self.app = pathlib.Path(project_root) / "application"
        self.base_app = pathlib.Path(base_root) / "application" if base_root else None
        self._cache = {}

    def new_lines(self, rel):
        """Set of new line numbers, empty if the file is the base kit's, None if the base kit
        has no such file (all lines are the project's)."""
        if self.base_app is None:
            return None
        if rel not in self._cache:
            self._cache[rel] = self._diff(rel)
        return self._cache[rel]

    def _diff(self, rel):
        mine_p, theirs_p = self.app / rel, self.base_app / rel
        if not mine_p.is_file() or not theirs_p.is_file():
            return None
        a = mine_p.read_bytes().replace(b"\r\n", b"\n")
        b = theirs_p.read_bytes().replace(b"\r\n", b"\n")
        if a == b:
            return set()
        mine = a.decode(errors="replace").split("\n")
        theirs = b.decode(errors="replace").split("\n")
        new = set()
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, theirs, mine, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                new.update(range(j1 + 1, j2 + 1))
        return new


def _order(f):
    return (f.file, f.line, f.rule)


def run(project, project_root, base_root=None, include_base=False):
    """All static findings. Unless include_base, findings on lines that come unchanged from the
    AK base kit are left out: that is the kit's code, not the project's."""
    findings = s01_framework_unchanged(project_root, base_root)
    diff = BaseKitDiff(project_root, None if include_base else base_root)
    for rule in RULES:
        for f in rule(project):
            new = diff.new_lines(f.file) if f.file else None
            if new is not None and f.line and f.line not in new:
                continue
            findings.append(f)
    return sorted(findings, key=_order)
