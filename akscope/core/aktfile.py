"""The .akt capture file: a small JSON header, then the raw console bytes.

    magic   8 bytes  b"AKSCOPE\\x01"
    length  u32 LE   size of the header
    header  JSON     {"created": ..., "port": ..., "baud": ...}
    data    raw bytes as read from the UART, until end of file

Raw bytes are kept instead of decoded records so a newer decoder can read old files.
"""

import json
import struct

MAGIC = b"AKSCOPE\x01"


class AktWriter:
    def __init__(self, path, header):
        self._f = open(path, "wb")
        blob = json.dumps(header, sort_keys=True).encode()
        self._f.write(MAGIC + struct.pack("<I", len(blob)) + blob)
        self._f.flush()

    def write(self, data):
        self._f.write(data)

    def flush(self):
        self._f.flush()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_capture(path):
    """Return (header, data). A file without the magic is taken as raw bytes."""
    with open(path, "rb") as f:
        blob = f.read()
    if not blob.startswith(MAGIC):
        return {}, blob
    (n,) = struct.unpack_from("<I", blob, len(MAGIC))
    start = len(MAGIC) + 4
    header = json.loads(blob[start:start + n].decode())
    return header, blob[start + n:]
