from dataclasses import dataclass


@dataclass
class TimeStats:
    count: int
    exe_min: int
    exe_avg: float
    exe_max: int
    exe_total: int
    wait_max: int


def time_stats(dispatches):
    exe = [d.exe_ms for d in dispatches]
    return TimeStats(
        count=len(exe),
        exe_min=min(exe),
        exe_avg=sum(exe) / len(exe),
        exe_max=max(exe),
        exe_total=sum(exe),
        wait_max=max(d.wait_ms for d in dispatches),
    )


def per_task(trace):
    return {task_id: time_stats(ds) for task_id, ds in trace.by_task().items()}


def per_signal(trace):
    return {key: time_stats(ds) for key, ds in trace.by_signal().items()}
