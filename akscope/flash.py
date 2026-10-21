"""Flash an AK kit over its console UART with ak-flash.

ak-flash talks to the AK bootloader on the same UART the firmware uses for its console.
When the running firmware sends trace frames, they can land inside the handshake reply and
the first attempt fails with a checksum error. After a failed attempt the board waits in the
bootloader, so a later run goes through. flash() retries for that reason.

ak-flash asks the running firmware for the bootloader at 115200 baud. A firmware built with
another console rate (akscope build --console-baud) does not understand it, and every attempt
ends in "handshake faulted". For that case flash() first types the base kit shell command
"fwu" at the console rate: it stores a UART update request for the bootloader and resets, so
the bootloader waits for ak-flash. A firmware without that command needs the boot mode
buttons; the error says how.
"""

import re
import shutil
import subprocess
import time

AK_FLASH_BAUD = 115200
# rates tried for "fwu" after the console rate itself, in case the kit runs another build
WAKE_RATES = (460800, 230400, 921600)
# from the reset to the bootloader listening, measured on the AK kit: well under 1 s
WAKE_DELAY_S = 1.0

APP_START_ADDR = 0x08003000

# right after a failed attempt the bootloader answers the next handshake with a fault;
# measured on the AK kit: an immediate retry fails, 5 to 10 s later it goes through
RETRY_DELAY_S = 5.0
DEFAULT_ATTEMPTS = 4

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


# what ak-flash itself prints for a board that does not answer the handshake
BOOT_MODE_HINT = ("Put the kit in boot mode: hold MODE, press and release RESET, keep MODE held until "
                  "the LIFE LED blinks fast, then flash again (the firmware may run its console at "
                  "another rate than the 115200 ak-flash uses, and have no fwu command)")


class FlashError(Exception):
    pass


def wake_bootloader(port, baud):
    """type "fwu" on the console at baud; False when the port cannot be opened"""
    try:
        import serial
    except ImportError:
        return False
    try:
        with serial.Serial(port, baud, timeout=0.2, exclusive=True) as s:
            s.reset_input_buffer()
            s.write(b"\r\nfwu\r\n")
            s.flush()
            time.sleep(0.05)
    except (OSError, serial.SerialException):
        return False
    time.sleep(WAKE_DELAY_S)
    return True


def port_busy(port):
    """why port cannot be opened for itself alone, or "" when it can (or cannot be checked)"""
    try:
        import serial
    except ImportError:
        return ""
    try:
        serial.Serial(port, exclusive=True).close()
    except serial.SerialException as e:
        if "busy" in str(e).lower() or "lock" in str(e).lower():
            return str(e)
    except OSError:
        pass
    return ""


def _wake_rate(console_baud, attempt):
    """the rate to send "fwu" at before an attempt, None when ak-flash can talk to the firmware"""
    if console_baud is None or console_baud == AK_FLASH_BAUD:
        return None
    rates = [console_baud] + [r for r in WAKE_RATES if r != console_baud]
    return rates[(attempt - 1) % len(rates)]


def flash(port, binary, address=APP_START_ADDR, attempts=DEFAULT_ATTEMPTS, log=print, console_baud=None):
    """console_baud: the console rate of the firmware running on the kit, when it is not 115200"""
    tool = shutil.which("ak-flash")
    if tool is None:
        raise FlashError("ak-flash not found, see https://github.com/ak-embedded-software/ak-flash")

    busy = port_busy(port)
    if busy:
        raise FlashError("%s is in use by another program (%s); stop it first" % (port, busy))

    last = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(RETRY_DELAY_S)
        rate = _wake_rate(console_baud, attempt)
        if rate is not None and wake_bootloader(port, rate):
            log("akscope: asked the firmware for the bootloader (fwu at %d baud)" % rate)
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
