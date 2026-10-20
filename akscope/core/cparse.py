"""Light C/C++ scanning over preprocessor output, for verify.

Not a C parser. It finds what the AK rules need, in code written like the AK base kit:
function definitions at file scope with their bodies, the names each body calls or mentions,
file scope variables, and enumerators. Every position maps back to the original file and
line through the `# <line> "<file>"` markers that `-E` writes.

Comments are already gone in -E output. String and char literals and the marker lines are
blanked with spaces of the same length, so offsets stay valid.
"""

import bisect
import re

IDENT = r"[A-Za-z_]\w*"
KEYWORDS = {
    "if", "else", "while", "for", "do", "switch", "case", "default", "return", "break",
    "continue", "goto", "sizeof", "typedef", "struct", "union", "enum", "static", "extern",
    "const", "volatile", "inline", "unsigned", "signed", "void", "char", "short", "int", "long",
    "float", "double", "bool", "true", "false", "new", "delete", "this", "operator", "template",
    "typename", "class", "public", "private", "protected", "namespace", "using", "register",
    "auto", "__attribute__", "__asm", "asm", "__asm__", "nullptr", "virtual", "explicit",
    "friend", "mutable", "static_cast", "reinterpret_cast", "const_cast", "dynamic_cast",
    "alignof", "decltype", "noexcept", "throw", "try", "catch", "__volatile__", "__inline",
}

_MARKER = re.compile(r'^# (\d+) "([^"]*)"', re.M)
_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
_NAME_PAREN = re.compile(r"(" + IDENT + r"(?:\s*::\s*~?" + IDENT + r")*)\s*\(")
_AFTER_PARAMS = re.compile(r"\s*(?:const\b\s*)?(?:noexcept\b\s*)?(?:__attribute__\s*\(\([^()]*\)\)\s*)?\{")
_BRACES = re.compile(r"[{}]")
_WORD = re.compile(IDENT)
_CALL = re.compile(r"(" + IDENT + r")\s*\(")


def _blank(m):
    s = m.group(0)
    return s[0] + " " * (len(s) - 2) + s[-1]


class Unit:
    """One preprocessed translation unit."""

    def __init__(self, text, name=""):
        self.name = name
        self.raw = text
        # line starts of the -E output, and the original (file, line) of each output line
        self.starts = [0]
        for m in re.finditer(r"\n", text):
            self.starts.append(m.end())
        self.origin = [None] * len(self.starts)
        cur_file, cur_line = name, 1
        markers = {}
        for m in _MARKER.finditer(text):
            markers[m.start()] = (int(m.group(1)), m.group(2))
        for i, start in enumerate(self.starts):
            if start in markers:
                cur_line, cur_file = markers[start]
                self.origin[i] = None
                continue
            self.origin[i] = (cur_file, cur_line)
            cur_line += 1
        code = _MARKER.sub(lambda m: " " * len(m.group(0)), text)
        code = re.sub(r"^#.*$", lambda m: " " * len(m.group(0)), code, flags=re.M)
        self.code = _LITERAL.sub(_blank, code)
        self._depth = None

    def where(self, offset):
        i = bisect.bisect_right(self.starts, offset) - 1
        o = self.origin[i]
        return o if o else (self.name, 0)

    def depth(self, offset):
        """Brace depth at offset."""
        if self._depth is None:
            pos, after, top_open = [], [], []
            d = 0
            for m in _BRACES.finditer(self.code):
                if m.group(0) == "{":
                    d += 1
                    if d == 1:
                        top_open.append(m.start())
                else:
                    d = max(d - 1, 0)
                pos.append(m.start())
                after.append(d)
            self._depth = (pos, after)
            self._top_open = top_open
            self._linkage = {}
        pos, after = self._depth
        i = bisect.bisect_left(pos, offset) - 1
        return after[i] if i >= 0 else 0

    def block_end(self, open_index):
        """Index of the brace that closes the one at open_index."""
        d = 0
        code = self.code
        for i in range(open_index, len(code)):
            if code[i] == "{":
                d += 1
            elif code[i] == "}":
                d -= 1
                if d == 0:
                    return i
        return len(code)

    def _close_paren(self, open_index):
        d = 0
        code = self.code
        for i in range(open_index, len(code)):
            ch = code[i]
            if ch == "(":
                d += 1
            elif ch == ")":
                d -= 1
                if d == 0:
                    return i
            elif ch in ";{}":
                return -1
        return -1

    def regions(self, prefix="sources/", skip_files=()):
        """[(start, end, file)] runs of output that come from files under prefix, without the
        files in skip_files (headers already read in another unit)."""
        out = []
        cur = None
        for i, o in enumerate(self.origin):
            f = o[0] if o else None
            if f and f.startswith("./"):
                f = f[2:]
            keep = f is not None and f.startswith(prefix) and f not in skip_files
            if keep and cur and cur[2] == f:
                continue
            if cur:
                out.append((cur[0], self.starts[i], cur[2]))
                cur = None
            if keep:
                cur = (self.starts[i], None, f)
        if cur:
            out.append((cur[0], len(self.code), cur[2]))
        return out

    def functions(self, regions=None):
        """Function definitions at file scope (or in an extern "C" / namespace block), only
        starting inside regions when given."""
        out = []
        code = self.code
        if regions is None:
            regions = [(0, len(code), None)]
        for r_start, r_end, _ in regions:
            out += self._functions_in(r_start, r_end)
        return out

    def _functions_in(self, r_start, r_end):
        out = []
        code = self.code
        pos = r_start
        while True:
            m = _NAME_PAREN.search(code, pos, r_end)
            if not m:
                break
            name = re.sub(r"\s+", "", m.group(1))
            pos = m.end()
            if name.split("::")[-1] in KEYWORDS:
                continue
            d = self.depth(m.start())
            if d > 1 or (d == 1 and not self._in_linkage_block(m.start())):
                continue
            close = self._close_paren(m.end() - 1)
            if close < 0:
                continue
            after = _AFTER_PARAMS.match(code, close + 1)
            if not after:
                continue
            brace = after.end() - 1
            end = self.block_end(brace)
            out.append(Function(self, name, code[m.end():close], m.start(), brace, end))
            pos = end + 1
        return out

    def _in_linkage_block(self, offset):
        """True when offset (at depth 1) is inside extern "C" { } or namespace { }."""
        self.depth(offset)
        i = bisect.bisect_left(self._top_open, offset) - 1
        if i < 0:
            return False
        brace = self._top_open[i]
        if brace not in self._linkage:
            head = self.code[max(0, brace - 40):brace]
            self._linkage[brace] = bool(re.search(r'(extern\s+"\s*"|namespace\s*(?:' + IDENT + r')?)\s*$', head))
        return self._linkage[brace]

    def globals(self):
        """[(name, offset, is_static)] of variables defined at file scope (not extern, not
        prototypes)."""
        out = []
        code = self.code
        start = 0
        d = 0
        init = False  # inside the braces of `x = { ... }`, part of the same statement
        for m in re.finditer(r"[{};]", code):
            ch = m.group(0)
            if ch == "{":
                if d == 0 and "=" in code[start:m.start()]:
                    init = True
                d += 1
                if not init:
                    start = m.end()
            elif ch == "}":
                d = max(d - 1, 0)
                if not init:
                    start = m.end()
            elif d == 0:
                init = False
                stmt = code[start:m.start()]
                name = _global_name(stmt)
                if name:
                    # the last occurrence of the name in the declarator part
                    head = stmt.split("=", 1)[0]
                    out.append((name, start + head.rfind(name), bool(re.match(r"\s*static\b", stmt))))
                start = m.end()
        return out

    def enumerators(self):
        """[(name, offset)] of every enumerator."""
        out = []
        for m in re.finditer(r"\benum\b\s*(?:class\s+)?(?:" + IDENT + r")?\s*(?::\s*\w+\s*)?\{", self.code):
            end = self.block_end(m.end() - 1)
            body_start = m.end()
            for item in re.finditer(r"(?:^|,)\s*(" + IDENT + r")", self.code[body_start:end]):
                out.append((item.group(1), body_start + item.start(1)))
        return out


def _global_name(stmt):
    s = stmt.strip()
    if not s or re.match(r"(extern|typedef|using|template|namespace|friend|static_assert)\b", s):
        return None
    if re.match(r"(struct|union|enum|class)\s+" + IDENT + r"\s*$", s):
        return None
    head = s.split("=", 1)[0]
    fp = re.search(r"\(\s*\*\s*(" + IDENT + r")\s*\)\s*\(", head)
    if fp:
        return fp.group(1)
    # a prototype has its parameter list before any initializer
    if "(" in head:
        return None
    words = _WORD.findall(re.sub(r"\[[^\]]*\]", "", head))
    # a type and a name at least; the name is the last word that is not a keyword
    names = [w for w in words if w not in KEYWORDS]
    if len(words) < 2 or not names or names[-1] != words[-1]:
        return None
    return names[-1]


class Function:
    def __init__(self, unit, name, params, start, brace, end):
        self.unit = unit
        self.name = name
        self.params = params
        self.start = start
        self.brace = brace
        self.end = end
        self.file, self.line = unit.where(start)
        self._calls = None
        self._mentions = None

    @property
    def body(self):
        return self.unit.code[self.brace + 1:self.end]

    @property
    def short(self):
        return self.name.split("::")[-1]

    def calls(self):
        if self._calls is None:
            self._calls = {m.group(1) for m in _CALL.finditer(self.body) if m.group(1) not in KEYWORDS}
        return self._calls

    def mentions(self):
        if self._mentions is None:
            self._mentions = {w for w in _WORD.findall(self.body) if w not in KEYWORDS}
        return self._mentions

    def find(self, pattern):
        """(file, line, text) of each match of pattern in the body."""
        out = []
        base = self.brace + 1
        for m in re.finditer(pattern, self.body):
            f, ln = self.unit.where(base + m.start())
            out.append((f, ln, m.group(0)))
        return out

    def msg_param(self):
        m = re.fullmatch(r"\s*(?:struct\s+)?ak_msg_t\s*\*\s*(" + IDENT + r")\s*", self.params)
        return m.group(1) if m else None

    def catch_all(self):
        return _catch_all(self)

    def sig_cases(self):
        """case labels of every `switch (<msg>->sig)` in the body, nested other switches left out."""
        p = self.msg_param()
        if not p:
            return []
        body = self.body
        cases = []
        for s in re.finditer(r"\bswitch\s*\(\s*" + re.escape(p) + r"\s*->\s*sig\s*\)\s*\{", body):
            inner = _block_text(body, s.end() - 1)
            inner = _drop_other_switches(inner, p)
            cases += re.findall(r"\bcase\s+(" + IDENT + r")\s*:", inner)
        return cases


def _catch_all(fn):
    """True when the handler does something for every signal: statements after its
    switch (msg->sig), or a default branch that is more than break."""
    p = fn.msg_param()
    if not p:
        return False
    body = fn.body
    for s in re.finditer(r"\bswitch\s*\(\s*" + re.escape(p) + r"\s*->\s*sig\s*\)\s*\{", body):
        inner = _block_text(body, s.end() - 1)
        tail = body[s.end() + len(inner) + 1:]
        if re.sub(r"[\s;}]|\breturn\b", "", tail):
            return True
        inner = _drop_other_switches(inner, p)
        d = re.search(r"\bdefault\s*:(.*)$", inner, re.S)
        if d and re.sub(r"[\s;{}]|\bbreak\b", "", d.group(1)):
            return True
    return False


def _block_text(text, open_index):
    d = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            d += 1
        elif text[i] == "}":
            d -= 1
            if d == 0:
                return text[open_index + 1:i]
    return text[open_index + 1:]


def _drop_other_switches(body, param):
    keep = re.compile(r"\bswitch\s*\(\s*" + re.escape(param) + r"\s*->\s*sig\s*\)")
    out, i = [], 0
    for m in re.finditer(r"\bswitch\s*\([^)]*\)\s*\{", body):
        if m.start() < i or keep.match(body, m.start()):
            continue
        out.append(body[i:m.start()])
        inner = _block_text(body, m.end() - 1)
        i = m.end() + len(inner) + 1
    out.append(body[i:])
    return "".join(out)
