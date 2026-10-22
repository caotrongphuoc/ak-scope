import json
import pathlib
import unittest

from akscope.core import akt, timeline
from akscope.core.runlog import RunLog, print_us
from akscope.core.symbols import SymbolTable

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
NAMES = {(9, 10): "AC_DISPLAY_INITIAL", (9, 14): "AC_DISPLAY_SHOW_LOGO", (12, 11): "AC_LINK_MAC_LAYER_STARTED"}


def runlog(baud=None):
    return RunLog(lambda task, sig: NAMES.get((task, sig), str(sig)), baud, set(NAMES.values()))


class TestRunLog(unittest.TestCase):
    def test_sig_line_starts_a_run(self):
        log = runlog()
        log.text("-SIG-> AC_DISPLAY_INITIAL\r\n[DBG] lcd on\r\n")
        log.exec(0, 9, 10, 1000)
        self.assertEqual(log.logs, {0: ["-SIG-> AC_DISPLAY_INITIAL", "[DBG] lcd on"]})

    def test_lines_split_across_chunks(self):
        log = runlog()
        log.text("-SIG-> AC_DISPLAY_INI")
        log.text("TIAL\n[DBG] a")
        log.text("b\n")
        log.exec(3, 9, 10, 1000)
        self.assertEqual(log.logs[3], ["-SIG-> AC_DISPLAY_INITIAL", "[DBG] ab"])

    def test_runs_matched_by_name(self):
        # the EXEC of the logo run comes after the text of the next run started
        log = runlog()
        log.text("-SIG-> AC_DISPLAY_SHOW_LOGO\n[DBG] logo\n-SIG-> AC_DISPLAY_INITIAL\n")
        log.exec(5, 9, 10, 1000)
        log.exec(4, 9, 14, 1000)
        self.assertEqual(log.logs, {4: ["-SIG-> AC_DISPLAY_SHOW_LOGO", "[DBG] logo"],
                                    5: ["-SIG-> AC_DISPLAY_INITIAL"]})

    def test_other_sig_lines_belong_to_the_run(self):
        # the screen manager prints "-SIG-> SCREEN_ENTRY" inside the run that changes screen
        log = runlog()
        log.text("-SIG-> AC_DISPLAY_SHOW_LOGO\n-SIG-> SCREEN_ENTRY\n")
        log.exec(1, 9, 14, 1000)
        self.assertEqual(log.logs[1], ["-SIG-> AC_DISPLAY_SHOW_LOGO", "-SIG-> SCREEN_ENTRY"])

    def test_text_outside_a_run(self):
        log = runlog()
        log.text("[PRINT] boot\n")
        log.exec(0, 9, 10, 1000)
        self.assertEqual(log.logs, {})

    def test_lines_the_run_had_no_time_to_print(self):
        # at 460800 a 30 character line takes about 0.67 ms on a blocking console
        self.assertAlmostEqual(print_us(["x" * 30], 460800), 672.0, delta=1)
        log = runlog(460800)
        log.text("-SIG-> AC_DISPLAY_INITIAL\n" + "-LINK_PRINT- MAX timeout send phy-frame: 250 (ms)\n" * 3)
        log.exec(0, 9, 10, 700)
        self.assertEqual(log.logs[0], ["-SIG-> AC_DISPLAY_INITIAL"])
        log.text("-SIG-> AC_DISPLAY_INITIAL\n" + "-LINK_PRINT- MAX timeout send phy-frame: 250 (ms)\n" * 3)
        log.exec(1, 9, 10, 5000)
        self.assertEqual(len(log.logs[1]), 4)


class TestRealCapture(unittest.TestCase):
    def test_base_kit_boot(self):
        items, _ = akt.decode_stream((SAMPLES / "base-kit-boot-akt.bin").read_bytes())
        tl = timeline.build_timeline([x for x in items if isinstance(x, akt.Record)])
        sym = SymbolTable(json.loads((SAMPLES / "base-kit-symbols.json").read_text()))
        timeline.attach_logs(tl, items, sym, 460800)
        logs = {sym.signal(tl.execs[i].task, tl.execs[i].sig): lines for i, lines in tl.logs.items()}
        # the link layer prints at boot (7 lines, 7 ms) come before any frame; the 0.64 ms run
        # of AC_UART_IF_INIT cannot have printed them
        self.assertEqual(logs["AC_UART_IF_INIT"], ["-SIG-> AC_UART_IF_INIT"])
        self.assertEqual(logs["AC_DISPLAY_SHOW_LOGO"], ["-SIG-> AC_DISPLAY_SHOW_LOGO", "-SIG-> SCREEN_ENTRY"])
        self.assertEqual(len(tl.logs), 20)


if __name__ == "__main__":
    unittest.main()
