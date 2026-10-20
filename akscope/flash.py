"""Flash an AK kit over its console UART with ak-flash.

ak-flash talks to the AK bootloader on the same UART the firmware uses for its console.
When the running firmware sends trace frames, they can land inside the handshake reply and
the first attempt fails with a checksum error. After a failed attempt the board waits in the
bootloader, so a later run goes through. flash() retries for that reason.
"""

import re
import shutil
import subprocess
import time

APP_START_ADDR = 0x08003000

# right after a failed attempt the bootloader answers the next handshake with a fault;
# measured on the AK kit: an immediate retry fails, 5 to 10 s later it goes through
RETRY_DELAY_S = 5.0
DEFAULT_ATTEMPTS = 4

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class FlashError(Exception):
    pass


def flash(port, binary, address=APP_START_ADDR, attempts=DEFAULT_ATTEMPTS, log=print):
    tool = shutil.which("ak-flash")
    if tool is None:
        raise FlashError("ak-flash not found, see https://github.com/ak-embedded-software/ak-flash")

    last = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(RETRY_DELAY_S)
        out = subprocess.run([tool, port, str(binary), "0x%08X" % address],
                             capture_output=True, text=True, errors="replace")
        text = _ANSI.sub("", (out.stdout + out.stderr).replace("\r", "\n"))
        if out.returncode == 0 and "firmware update completed" in text:
            log("akscope: flashed %s to %s" % (binary, port))
            return attempt
        errors = [l.strip() for l in text.splitlines() if "[ERROR]" in l or "[FAILED]" in l]
        last = errors[-1] if errors else "exit code %d" % out.returncode
        log("akscope: flash attempt %d failed: %s" % (attempt, last))

    raise FlashError("flash failed after %d attempts: %s" % (attempts, last))
