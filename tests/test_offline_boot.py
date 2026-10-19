"""The boot path away from a known network (GENBU-151): the saved clock and the one-at-a-time
clock start, run for real in a shell with the commands that touch the system stubbed."""
from pathlib import Path
import shutil
import subprocess
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "tools" / "linux" / "techo5-lib.sh"
SH = [shutil.which("busybox"), "sh"] if shutil.which("busybox") else ["sh"]


def run(tmp: Path, body: str, now: int = 1_000, uptime: int = 100, ntpd_running: bool = False) -> str:
    script = f"""
log() {{ echo "log: $*"; }}
. "{LIB}"
T5_CLOCK="{tmp}/misc/clock"
T5_NTP_LOCK="{tmp}/run/ntp-start"
# The stubs note their calls in a file: the lib sends their output to /dev/null.
calls="{tmp}/calls"
date() {{
	case "$1" in
	+%s) echo {now} ;;
	-s) echo "date-set $2" >> "$calls" ;;
	*) echo "a date" ;;
	esac
}}
cut() {{ echo {uptime}; }}
pidof() {{ [ "$1" = ntpd ] && {'true' if ntpd_running else 'false'}; }}
t5_ntp() {{ echo "ntp-query" >> "$calls"; }}
t5_ntp_peers() {{ echo pool.example.org; }}
ntpd() {{ echo "ntpd-started" >> "$calls"; }}
{body}
"""
    calls = tmp / "calls"
    calls.unlink(missing_ok=True)
    out = subprocess.run(SH + ["-c", script], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return (calls.read_text() if calls.exists() else "")


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "misc").mkdir()
        self.clock = self.tmp / "misc" / "clock"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_save_writes_only_a_later_time(self):
        run(self.tmp, "t5_clock_save", now=2_000)
        self.assertEqual(self.clock.read_text().strip(), "2000")
        run(self.tmp, "t5_clock_save", now=1_500)
        self.assertEqual(self.clock.read_text().strip(), "2000")
        self.assertFalse((self.tmp / "misc" / "clock.tmp").exists())

    def test_save_replaces_a_damaged_file(self):
        self.clock.write_text("garbage\n")
        run(self.tmp, "t5_clock_save", now=2_000)
        self.assertEqual(self.clock.read_text().strip(), "2000")

    def test_save_needs_userdata(self):
        shutil.rmtree(self.tmp / "misc")
        run(self.tmp, "t5_clock_save", now=2_000)
        self.assertFalse(self.clock.exists())

    def test_restore_only_moves_the_clock_forward(self):
        self.clock.write_text("5000\n")
        self.assertIn("date-set @5000", run(self.tmp, "t5_clock_restore", now=1_000))
        self.assertNotIn("date-set", run(self.tmp, "t5_clock_restore", now=9_000))

    def test_restore_ignores_missing_or_damaged_files(self):
        self.assertNotIn("date-set", run(self.tmp, "t5_clock_restore"))
        for bad in ("", "12ab", "-5", "@1", "1 2"):
            self.clock.write_text(bad)
            self.assertNotIn("date-set", run(self.tmp, "t5_clock_restore"), bad)


class ClockStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lock = self.tmp / "run" / "ntp-start"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_starts_once_and_cleans_up(self):
        out = run(self.tmp, "t5_clock_start")
        self.assertIn("ntp-query", out)
        self.assertIn("ntpd-started", out)
        self.assertFalse(self.lock.exists())

    def test_nothing_while_ntpd_runs(self):
        self.assertEqual(run(self.tmp, "t5_clock_start", ntpd_running=True), "")

    def test_leaves_it_to_a_caller_already_at_it(self):
        self.lock.mkdir(parents=True)
        (self.lock / "at").write_text("90\n")
        self.assertEqual(run(self.tmp, "t5_clock_start", uptime=100), "")
        self.assertTrue(self.lock.exists())
        # Between the other caller's mkdir and its note: still theirs.
        (self.lock / "at").unlink()
        self.assertEqual(run(self.tmp, "t5_clock_start", uptime=100), "")

    def test_takes_over_from_a_caller_that_died(self):
        self.lock.mkdir(parents=True)
        (self.lock / "at").write_text("10\n")
        out = run(self.tmp, "t5_clock_start", uptime=100)
        self.assertIn("ntpd-started", out)
        self.assertFalse(self.lock.exists())


class HostnameTests(unittest.TestCase):
    """GENBU-260: the name sent with DHCP is echod's node name (layout.Slug)."""

    def name(self, written: str | None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "name"
            if written is not None:
                path.write_text(written)
            out = subprocess.run(SH + ["-c", f'T5_NAME="{path}"; . "{LIB}"; t5_hostname'],
                                 capture_output=True, text=True, timeout=30)
            return out.stdout.strip()

    def test_the_slug_of_the_name(self):
        self.assertEqual(self.name("bathroomShow5\n"), "bathroomshow5")
        self.assertEqual(self.name("  Living Room \n"), "living-room")
        self.assertEqual(self.name("Küche / Wohnzimmer 2"), "k-che-wohnzimmer-2")

    def test_techo5_without_a_name(self):
        self.assertEqual(self.name(None), "techo5")
        self.assertEqual(self.name("\n"), "techo5")
        self.assertEqual(self.name("!!!"), "techo5")

    def test_every_lease_client_sends_it(self):
        for rel in ("tools/linux/techo5-lib.sh", "tools/linux/rootfs/etc/techo5/boot.sh"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            starts = re.findall(r"udhcpc -i wlan0[^\n]*", text)
            self.assertTrue(starts, rel)
            for line in starts:
                self.assertIn('-x "hostname:$(t5_hostname)"', line, rel)


class BootPathContractTests(unittest.TestCase):
    def read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_lease_client_keeps_asking_when_association_fails(self):
        lib = self.read("tools/linux/techo5-lib.sh")
        start = lib.index('log "wifi: not associated')
        branch = lib[start:lib.index("return 1", start)]
        self.assertIn("udhcpc -i wlan0 -b", branch)
        self.assertTrue(branch.rstrip().endswith("&"), "the lease client must not hold the boot")

    def test_keeper_leaves_a_running_supplicant_alone(self):
        boot = self.read("tools/linux/rootfs/etc/techo5/boot.sh")
        self.assertIn("pidof wpa_supplicant >/dev/null && pidof udhcpc >/dev/null && [ $((down % 5)) -ne 0 ]", boot)

    def test_clock_is_restored_early_and_saved(self):
        boot = self.read("tools/linux/rootfs/etc/techo5/boot.sh")
        self.assertLess(boot.index("t5_clock_restore"), boot.index("t5_wifi_up"))
        self.assertLess(boot.index("mount -t ext4 -o noatime $DATA_DEV /data"), boot.index("t5_clock_restore"))
        self.assertIn("t5_clock_save", boot)
        self.assertIn("t5_clock_save", self.read("tools/linux/rootfs/etc/techo5/shutdown.sh"))
        shutdown = self.read("tools/linux/rootfs/etc/techo5/shutdown.sh")
        self.assertLess(shutdown.index("t5_clock_save"), shutdown.index("umount -a -r"))

    def test_lease_starts_the_clock_in_the_background(self):
        hook = self.read("tools/linux/rootfs/etc/techo5/udhcpc.sh")
        self.assertIn("( . /lib/techo5-lib.sh; t5_clock_start ) > /dev/null 2>&1 &", hook)


if __name__ == "__main__":
    unittest.main()
