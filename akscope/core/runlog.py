"""Console lines of each handler run, from the base kit's debug prints.

With APP_DBG_SIG_EN (on in the base kit Makefile) a handler starts its case with
APP_DBG_SIG("NAME\\n"), which prints "-SIG-> NAME" before anything else the run prints
(APP_DBG "[DBG] ...", APP_PRINT "[PRINT] ...", plain xprintf). The akt EXEC record of that run
is queued when the handler returns and sent later by the polling task, so on the UART the
run's text always comes before its EXEC frame. A "-SIG-> NAME" line therefore belongs to the
next EXEC whose signal is named NAME.

The lines after it are only taken when the run was long enough to print them. With the link
UART on (IF_LINK_UART_EN, as in the base kit) the console blocks: every character holds the
handler 10 bits at the console rate. At boot all the text comes before the first trace frames,
and a run of 0.6 ms cannot have printed seven lines that take 7 ms; those were printed by a
later run without its own "-SIG->" line. Such lines stay in the console only. Without a known
rate every line up to the next "-SIG->" is taken.
"""

from collections import deque

SIG_PREFIX = "-SIG-> "
MAX_LINES = 50  # per run; a print in a loop must not make the page JSON huge


def print_us(lines, baud):
    """Time the console needs to send lines when it blocks, 8N1 plus the line feed."""
    return sum(len(x) + 1 for x in lines) * 10 * 1e6 / baud


class RunLog:
    def __init__(self, name_of, baud=None, names=()):
        """name_of(task, sig) gives the signal name the project uses, as APP_DBG_SIG prints it;
        baud is the console rate of the capture, None when unknown; names are the project's
        signal names. Other "-SIG->" lines (the screen manager prints "-SIG-> SCREEN_ENTRY"
        inside the run that changes screen) are lines of the run, not the start of one."""
        self.name_of = name_of
        self.baud = baud
        self.names = set(names)
        self.logs = {}  # exec index -> [line]
        self._partial = ""
        self._open = None  # [name, lines] of the run whose "-SIG->" came last
        self._waiting = deque()  # [name, lines] whose EXEC has not arrived, oldest first

    def text(self, s):
        """Console text, in the order it came on the UART."""
        lines = (self._partial + s).split("\n")
        self._partial = lines.pop()
        for line in lines:
            self._line(line.strip("\r"))

    def _line(self, line):
        name = line[len(SIG_PREFIX):].split() if line.startswith(SIG_PREFIX) else None
        if name and (not self.names or name[0] in self.names):
            entry = [name[0], [line]]
            self._waiting.append(entry)
            self._open = entry
            # a run whose EXEC was lost (OVERFLOW) must not hold lines forever
            while len(self._waiting) > 64:
                self._waiting.popleft()
        elif self._open is not None and line and len(self._open[1]) < MAX_LINES:
            self._open[1].append(line)

    def exec(self, index, task, sig, dur_us):
        """An EXEC record arrived; index is its position in Timeline.execs, dur_us its run time."""
        # a "?" marks a name matched by value only; the printed name has none
        name = self.name_of(task, sig).rstrip("?")
        for entry in self._waiting:
            if entry[0] == name:
                self._waiting.remove(entry)
                if entry is self._open:
                    self._open = None
                lines = entry[1]
                if self.baud:
                    n = 1
                    while n < len(lines) and print_us(lines[:n + 1], self.baud) <= dur_us:
                        n += 1
                    lines = lines[:n]
                self.logs[index] = lines
                return
