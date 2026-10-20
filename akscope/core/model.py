from dataclasses import dataclass, field

PURE_MSG_TYPE = 0x80
COMMON_MSG_TYPE = 0xC0
DYNAMIC_MSG_TYPE = 0x40

MSG_TYPE_NAMES = {
    PURE_MSG_TYPE: "pure",
    COMMON_MSG_TYPE: "common",
    DYNAMIC_MSG_TYPE: "dynamic",
}

# first signal value free for applications (AK_USER_DEFINE_SIG in ak.h)
AK_USER_DEFINE_SIG = 10


def msg_type_name(msg_type):
    return MSG_TYPE_NAMES.get(msg_type, "0x%02x" % msg_type)


@dataclass
class Dispatch:
    """One message handled by a task, as reported by the kernel."""

    origin: str  # "console" for the live kernel log, "fatal" for a `fatal m` dump
    line: int  # line number in the capture, for error reports
    task_id: int
    msg_type: int
    ref_count: int
    sig: int
    wait_ms: int
    exe_ms: int
    index: int = -1  # slot in the fatal ring, -1 for console records


@dataclass
class Trace:
    """Records from one capture, in the order the board reported them."""

    source: str = ""
    dispatches: list = field(default_factory=list)

    def task_ids(self):
        return sorted({d.task_id for d in self.dispatches})

    def by_task(self):
        groups = {}
        for d in self.dispatches:
            groups.setdefault(d.task_id, []).append(d)
        return dict(sorted(groups.items()))

    def by_signal(self):
        """Group by (task_id, sig): signal values are only unique inside one task."""
        groups = {}
        for d in self.dispatches:
            groups.setdefault((d.task_id, d.sig), []).append(d)
        return dict(sorted(groups.items()))
