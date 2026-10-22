"""Decode the akt trace stream (docs/trace-format.md, format version 1).

The stream from the console UART mixes plain text and frames:
    0x00 | COBS(record | crc8) | 0x00
"""

import struct
from dataclasses import dataclass, field

ID_INFO = 0x01
ID_POST = 0x02
ID_EXEC = 0x03
ID_TIMER_SET = 0x04
ID_TIMER_REMOVE = 0x05
ID_POOL = 0x06
ID_OVERFLOW = 0x07
ID_USER = 0x08

# record id -> (name, struct layout after id and t, field names)
_LAYOUT = {
    ID_INFO: ("INFO", "<BIBBBBB4sB",
              ("version", "cpu_hz", "task_count", "pure_pool", "common_pool", "dynamic_pool",
               "timer_pool", "app_ver", "flags")),
    ID_POST: ("POST", "<BBBBH", ("src", "des", "sig", "msg_type", "len")),
    ID_EXEC: ("EXEC", "<IBBBH", ("duration", "task", "sig", "msg_type", "wait_ms")),
    ID_TIMER_SET: ("TIMER_SET", "<BBBI", ("des", "sig", "periodic", "duty_ms")),
    ID_TIMER_REMOVE: ("TIMER_REMOVE", "<BB", ("des", "sig")),
    ID_POOL: ("POOL", "<BBBB", ("pure", "common", "dynamic", "timer")),
    ID_OVERFLOW: ("OVERFLOW", "<H", ("dropped",)),
    ID_USER: ("USER", "<BI", ("user_id", "value")),
}

RECORD_SIZE = {rid: 5 + struct.calcsize(fmt) for rid, (_, fmt, _) in _LAYOUT.items()}

# id -> (name, compiled struct for t and the fields, field names); decode_record runs per frame
_COMPILED = {rid: (name, struct.Struct("<I" + fmt[1:]), names)
             for rid, (name, fmt, names) in _LAYOUT.items()}


def _crc8_table():
    table = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


_CRC8_TABLE = _crc8_table()


def crc8(data):
    """CRC-8/SMBUS: poly 0x07, init 0, no reflection, no final xor."""
    crc = 0
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return crc


def cobs_decode(data):
    """Return the decoded bytes, or None if data is not valid COBS."""
    if 0 in data:
        return None
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        code = data[i]
        end = i + code
        if code == 0 or end > n:
            return None
        out += data[i + 1:end]
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


def cobs_encode(data):
    out = bytearray([0])
    code_index = 0
    code = 1
    for b in data:
        if b == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    return bytes(out)


def encode_frame(record):
    """Frame a record the way the firmware does. Used by tests and replay tools."""
    return b"\x00" + cobs_encode(record + bytes([crc8(record)])) + b"\x00"


@dataclass
class Record:
    kind: str
    rid: int
    t: int  # raw 32-bit cycle count from the board
    fields: dict = field(default_factory=dict)
    t_abs: int = 0  # cycle count with wraps removed, set by Unwrapper
    exec_index: int = -1  # for an EXEC: its position in Timeline.execs, set by build_timeline
    restart: bool = False  # first record after the board restarted, set by Unwrapper


def decode_record(body):
    """body is a frame after COBS decoding, crc included.

    Returns a Record, "unknown" for a valid frame with a new record id, or None if corrupt.
    """
    if len(body) < 2 or crc8(body[:-1]) != body[-1]:
        return None
    record = body[:-1]
    rid = record[0]
    if rid not in _LAYOUT:
        return "unknown"
    if len(record) != RECORD_SIZE[rid]:
        return None
    name, st, names = _COMPILED[rid]
    values = st.unpack_from(record, 1)
    t = values[0]
    fields = dict(zip(names, values[1:]))
    if rid == ID_INFO:
        fields["app_ver"] = list(fields["app_ver"])
    return Record(kind=name, rid=rid, t=t, fields=fields)


@dataclass
class StreamStats:
    frames: int = 0
    corrupt: int = 0
    unknown: int = 0
    text_bytes: int = 0


class StreamDecoder:
    """Incremental splitter for serial data. feed() returns a list of Record and str items."""

    def __init__(self):
        self.stats = StreamStats()
        self._segment = bytearray()
        self._in_frame = False
        # until the first good frame, nothing counts as a corrupt frame: the bootloader prints
        # its banner at its own baud rate, which reads as noise at a faster console rate
        self._synced = False

    def feed(self, data):
        items = []
        parts = bytes(data).split(b"\x00")
        # every part but the last ended with a delimiter; the last waits for more data
        for part in parts[:-1]:
            self._segment += part
            self._flush(items)
        self._segment += parts[-1]
        return items

    def _flush(self, items):
        seg = bytes(self._segment)
        self._segment.clear()
        if not seg:
            # 00 00 between two frames, or the leading delimiter after text
            self._in_frame = True
            return
        if self._in_frame:
            body = cobs_decode(seg)
            rec = decode_record(body) if body is not None else None
            if isinstance(rec, Record):
                self.stats.frames += 1
                self._synced = True
                items.append(rec)
                self._in_frame = False
                return
            if rec == "unknown":
                self.stats.unknown += 1
                self._in_frame = False
                return
            if self._synced:
                self.stats.corrupt += 1
        # text, or a frame broken by text: the next 00 may start a frame
        self.stats.text_bytes += len(seg)
        items.append(seg.decode("latin-1"))
        self._in_frame = True

    def finish(self):
        """Return trailing text that was not followed by a delimiter."""
        items = []
        if self._segment:
            self.stats.text_bytes += len(self._segment)
            items.append(bytes(self._segment).decode("latin-1"))
            self._segment.clear()
        return items


# a restart: the cycle counter starts over at boot. INFO is sent first thing, so an INFO this
# close to 0 is a boot (the periodic INFO comes after 64 s); without INFO (lost in the noise of
# the restart) a step back of more than a second to a time this close to 0 is one too. At
# 32 MHz; a faster chip only makes the windows shorter
BOOT_INFO_CYCLES = 3200000  # 0.1 s
BOOT_STEP_BACK_CYCLES = 32000000  # 1 s
BOOT_WINDOW_CYCLES = 320000000  # 10 s


class Unwrapper:
    """Turn the 32-bit cycle counter into a monotonic count.

    Records are not sorted by t: EXEC is sent when the handler returns but carries the time
    it started, so it comes after the POSTs made inside the handler. Each step is taken as a
    signed 32-bit difference from the previous record, which is right as long as two records
    are less than half a wrap apart (67 s at 32 MHz). The board sends POOL every second.

    When the board restarts the counter starts over. The records after it are placed right
    after the last one before it (the real gap, reset plus boot, is not in the trace), marked
    with restart, and their times go on from there.
    """

    def __init__(self):
        self._last_t = None
        self._last_abs = 0
        self.restarts = []  # t_abs of the last record before each restart

    def _is_restart(self, rec, delta):
        if rec.kind == "INFO" and rec.t < BOOT_INFO_CYCLES:
            return True
        return delta < -BOOT_STEP_BACK_CYCLES and rec.t < BOOT_WINDOW_CYCLES

    def __call__(self, rec):
        if self._last_t is None:
            rec.t_abs = rec.t
        else:
            delta = (rec.t - self._last_t) & 0xFFFFFFFF
            if delta & 0x80000000:
                delta -= 1 << 32
            if self._is_restart(rec, delta):
                rec.restart = True
                self.restarts.append(self._last_abs)
                delta = rec.t
            rec.t_abs = self._last_abs + delta
        self._last_t = rec.t
        self._last_abs = rec.t_abs
        return rec


def decode_stream(data):
    """Decode a whole capture. Returns (items, stats): Record and str (console text) in the
    order they came on the UART."""
    dec = StreamDecoder()
    unwrap = Unwrapper()
    items = [unwrap(x) if isinstance(x, Record) else x for x in dec.feed(data) + dec.finish()]
    return items, dec.stats


def decode_bytes(data):
    """Decode a whole capture. Returns (records, text, stats)."""
    items, stats = decode_stream(data)
    records = [x for x in items if isinstance(x, Record)]
    return records, "".join(x for x in items if not isinstance(x, Record)), stats
