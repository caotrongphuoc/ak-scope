"""Parse the text that an AK board prints on its console.

Two line formats carry per-message records:

kernel task log (ak/src/task.c, AK_TASK_LOG_CONSOLE_ENABLE), one line per dispatch:
    taskID: 4	msgType:0x80	refCnt:1	sig:10		waitTime:0	exeTime:0

`fatal m` shell dump (app/shell.cpp), the RAM ring saved at the last FATAL:
    index: 0	task_id: 4	msg_type:0x80	ref_count:1	sig: 10		wait_time: 0	exe_time: 0

Anything else on the console (boot banner, shell echo, app prints) is skipped.
"""

import re

from akscope.core.model import Dispatch, Trace

_KERNEL_RE = re.compile(
    r"taskID:\s*(\d+)\s+msgType:\s*0x([0-9a-fA-F]+)\s+refCnt:\s*(\d+)\s+"
    r"sig:\s*(\d+)\s+waitTime:\s*(\d+)\s+exeTime:\s*(\d+)"
)

_FATAL_RE = re.compile(
    r"index:\s*(\d+)\s+task_id:\s*(\d+)\s+msg_type:\s*0x([0-9a-fA-F]+)\s+ref_count:\s*(\d+)\s+"
    r"sig:\s*(\d+)\s+wait_time:\s*(\d+)\s+exe_time:\s*(\d+)"
)


def parse_line(line, line_no=0):
    """Return a Dispatch for a record line, or None for any other text."""
    m = _KERNEL_RE.search(line)
    if m:
        task_id, msg_type, ref_count, sig, wait, exe = m.groups()
        return Dispatch(
            origin="console",
            line=line_no,
            task_id=int(task_id),
            msg_type=int(msg_type, 16),
            ref_count=int(ref_count),
            sig=int(sig),
            wait_ms=int(wait),
            exe_ms=int(exe),
        )

    m = _FATAL_RE.search(line)
    if m:
        index, task_id, msg_type, ref_count, sig, wait, exe = m.groups()
        return Dispatch(
            origin="fatal",
            line=line_no,
            index=int(index),
            task_id=int(task_id),
            msg_type=int(msg_type, 16),
            ref_count=int(ref_count),
            sig=int(sig),
            wait_ms=int(wait),
            exe_ms=int(exe),
        )

    return None


def parse_text(text):
    """Parse a whole capture. Accepts LF, CRLF or bare CR line endings."""
    records = []
    for line_no, line in enumerate(re.split(r"\r\n|\r|\n", text), start=1):
        rec = parse_line(line, line_no)
        if rec is not None:
            records.append(rec)
    return records


def parse_file(path):
    # the console is 8-bit and may carry noise from reset or a wrong baud rate
    with open(path, "r", encoding="latin-1", newline="") as f:
        return parse_text(f.read())


def load_trace(path):
    return Trace(source=str(path), dispatches=parse_file(path))
