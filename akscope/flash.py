"""Flash an AK kit over its console UART with ak-flash.

ak-flash talks to the AK bootloader on the same UART the firmware uses for its console.
When the running firmware sends trace frames, they can land inside the handshake reply and
the first attempt fails with a checksum error. After a failed attempt the board waits in the
bootloader, so a later run goes through. flash() retries for that reason.

ak-flash asks the running firmware for the bootloader at 115200 baud. A firmware built with
another console rate (akscope build --console-baud) does not understand it, and every attempt
ends in "handshake faulted". The kit then has to be put in boot mode by hand; the error says how.
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


# what ak-flash itself prints for a board that does not answer the handshake
BOOT_MODE_HINT = ("Put the kit in boot mode: hold MODE, press and release RESET, keep MODE held until "
                  "the LIFE LED blinks fast, then flash again (the firmware may run its console at "
                  "another rate than the 115200 ak-flash uses)")


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

    msg = "flash failed after %d attempts: %s" % (attempts, last)
    if "handshake faulted" in last:
        msg = msg.rstrip(" !.") + ". " + BOOT_MODE_HINT
    raise FlashError(msg)
