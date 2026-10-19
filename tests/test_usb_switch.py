"""techo5-usb: switching the port off succeeds whether or not a serial console is running.

The script is run for real, with the library and the run directory swapped for stand-ins."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/linux/rootfs/usr/local/sbin/techo5-usb"


class UsbSwitchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.calls = self.dir / "calls"
        (self.dir / "lib.sh").write_text(
            f't5_usb_off() {{ echo off >> {self.calls}; }}\n'
            f't5_usb_acm() {{ echo acm >> {self.calls}; }}\n'
            f't5_usb_host() {{ echo host $1 >> {self.calls}; }}\n')
        self.run_dir = self.dir / "run"
        self.run_dir.mkdir()
        script = (TOOL.read_text()
                  .replace("/lib/techo5-lib.sh", str(self.dir / "lib.sh"))
                  .replace("/etc/techo5/device.conf", str(self.dir / "none"))
                  .replace("/run/techo5", str(self.run_dir))
                  .replace("/dev/kmsg", "/dev/null"))
        self.tool = self.dir / "techo5-usb"
        self.tool.write_text(script)

    def run_tool(self, arg):
        return subprocess.run(["sh", str(self.tool), arg], capture_output=True, text=True, timeout=10)

    def test_off_without_a_console_succeeds(self):
        r = self.run_tool("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls.read_text(), "off\n")

    def test_off_hangs_up_the_console(self):
        console = subprocess.Popen(["sleep", "30"])
        self.addCleanup(console.kill)
        (self.run_dir / "console.pid").write_text(f"{console.pid}\n")
        r = self.run_tool("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(console.wait(timeout=5), -1)  # SIGHUP

    def test_off_with_a_stale_pid_succeeds(self):
        (self.run_dir / "console.pid").write_text("999999999\n")
        self.assertEqual(self.run_tool("off").returncode, 0)

    def test_on_and_usage(self):
        self.assertEqual(self.run_tool("on").returncode, 0)
        self.assertEqual(self.calls.read_text(), "acm\nhost 1\n")
        self.assertEqual(self.run_tool("bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
