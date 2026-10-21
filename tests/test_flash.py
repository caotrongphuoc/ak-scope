import subprocess
import unittest
from unittest import mock

from akscope import flash


def result(text, code=0):
    return subprocess.CompletedProcess([], code, stdout=text, stderr="")


class TestFlash(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch("shutil.which", return_value="/usr/local/bin/ak-flash"),
                   mock.patch("time.sleep")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_retry_then_ok(self):
        replies = [result("\x1b[31m[ERROR] \x1b[0mchecksum\n", 1), result("[INFO] firmware update completed\n")]
        with mock.patch("subprocess.run", side_effect=replies) as run:
            self.assertEqual(flash.flash("/dev/ttyUSB0", "app.bin", log=lambda *a: None), 2)
        self.assertEqual(run.call_args.args[0], ["/usr/local/bin/ak-flash", "/dev/ttyUSB0", "app.bin", "0x08003000"])

    def test_handshake_fault_says_boot_mode(self):
        with mock.patch("subprocess.run", return_value=result("[ERROR] handshake faulted !\n", 1)):
            with self.assertRaises(flash.FlashError) as cm:
                flash.flash("/dev/ttyUSB0", "app.bin", attempts=2, log=lambda *a: None)
        self.assertIn("handshake faulted", str(cm.exception))
        self.assertIn("boot mode", str(cm.exception))

    def test_other_error_has_no_hint(self):
        with mock.patch("subprocess.run", return_value=result("[ERROR] checksum mismatch\n", 1)):
            with self.assertRaises(flash.FlashError) as cm:
                flash.flash("/dev/ttyUSB0", "app.bin", attempts=1, log=lambda *a: None)
        self.assertNotIn("boot mode", str(cm.exception))

    def test_wakes_firmware_at_console_rate(self):
        replies = [result("[ERROR] handshake faulted !\n", 1), result("[INFO] firmware update completed\n")]
        with mock.patch("subprocess.run", side_effect=replies), \
                mock.patch.object(flash, "wake_bootloader", return_value=True) as wake:
            self.assertEqual(flash.flash("/dev/ttyUSB0", "app.bin", log=lambda *a: None, console_baud=460800), 2)
        # the console rate first, then the other common rates
        self.assertEqual([c.args for c in wake.call_args_list], [("/dev/ttyUSB0", 460800), ("/dev/ttyUSB0", 230400)])

    def test_no_wake_at_115200(self):
        with mock.patch("subprocess.run", return_value=result("[INFO] firmware update completed\n")), \
                mock.patch.object(flash, "wake_bootloader") as wake:
            flash.flash("/dev/ttyUSB0", "app.bin", log=lambda *a: None, console_baud=115200)
            flash.flash("/dev/ttyUSB0", "app.bin", log=lambda *a: None)
        wake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
