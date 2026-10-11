import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from jarvis_crown import timing, unlock, recovery  # noqa: E402
from jarvis_crown.device_gate import DeviceGateError, DeviceIdentity  # noqa: E402
from techo5lib import Fastboot  # noqa: E402

SPEC = importlib.util.spec_from_file_location("jarvis_install_show_reconnect", TOOLS / "install-show.py")
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class DummyAdb:
    def __init__(self):
        self.commands = []

    def state(self):
        return "recovery"

    def sh(self, command):
        self.commands.append(command)
        if command == "twrp format data":
            return "Done."
        if command == "twrp reboot recovery":
            self.rebooted = True
            return ""
        if command == "cat /proc/sys/kernel/random/boot_id":
            return "boot-after" if getattr(self, "rebooted", False) else "boot-before"
        if command.startswith("getprop sys.usb.config;"):
            return getattr(self, "usb", "mtp,adb\nmtp,ffs")
        if command == "mount":
            return "/dev/block/foo on /data type ext4 (rw)"
        if command.startswith("touch /data/.jarvis-crown-write-test"):
            return "OK"
        if command.startswith("sha256sum /data/lineage.zip"):
            import hashlib
            return hashlib.sha256(self.payload).hexdigest() + "  /data/lineage.zip"
        if command == "twrp install /data/lineage.zip":
            return "Install succeeded"
        if command == "rm -f /data/lineage.zip":
            return ""
        raise AssertionError(command)

    def push(self, local, remote):
        self.payload = pathlib.Path(local).read_bytes()


class ReconnectTimingTests(unittest.TestCase):
    def test_stage_budgets_are_generous_but_bounded(self):
        self.assertGreaterEqual(timing.POST_AMONET_FASTBOOT_TIMEOUT_SECONDS, 300)
        self.assertGreaterEqual(timing.TWRP_REENUM_TIMEOUT_SECONDS, 300)
        self.assertGreaterEqual(timing.TWRP_DATA_REBOOT_TIMEOUT_SECONDS, 300)
        self.assertGreaterEqual(timing.FASTBOOT_REENUM_TIMEOUT_SECONDS, 180)
        self.assertGreaterEqual(timing.RESCUE_CONSOLE_TIMEOUT_SECONDS, 420)
        self.assertGreaterEqual(timing.FIRST_BOOT_TIMEOUT_SECONDS, 600)
        self.assertLessEqual(timing.FIRST_BOOT_TIMEOUT_SECONDS, 900)

    def test_unlock_wait_tolerates_device_disappearing_then_returning(self):
        original = DeviceIdentity("SERIAL", "CROWN", False, "lk")
        calls = {"n": 0}

        def identify():
            calls["n"] += 1
            if calls["n"] < 4:
                raise DeviceGateError("no fastboot device")
            return DeviceIdentity("SERIAL", "CROWN", True, "lk")

        result = unlock._wait_for_unlocked(original, identify=identify, sleep=lambda _s: None, attempts=5)
        self.assertTrue(result.unlocked)
        self.assertEqual(calls["n"], 4)

    def test_twrp_default_wait_budget_matches_timing_contract(self):
        budget = recovery.TWRP_WAIT_ATTEMPTS * recovery.TWRP_WAIT_INTERVAL_SECONDS
        self.assertGreaterEqual(budget, timing.TWRP_REENUM_TIMEOUT_SECONDS)

    def test_post_format_recovery_wait_uses_product_timeout(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            archive = pathlib.Path(td) / "lineage.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("META-INF/com/android/metadata", "pre-device=crown\n")
            adb = DummyAdb()
            seen = {}
            old_wait = install.wait_for
            install.wait_for = lambda label, seconds, predicate, every: (
                seen.update(label=label, seconds=seconds, every=every), self.assertTrue(predicate())
            )
            try:
                install.install_lineage(adb, str(archive))
            finally:
                install.wait_for = old_wait
            self.assertEqual(seen["seconds"], timing.TWRP_DATA_REBOOT_TIMEOUT_SECONDS)

    def test_stuck_in_fastboot_after_the_format_is_sent_on_to_twrp(self):
        class StuckFastboot:
            def __init__(self):
                self.ran, self.there = [], True

            def present(self):
                return self.there

            def run(self, *args):
                self.ran.append(args)
                self.there = False
                return 0, "OKAY"

        fastboot = StuckFastboot()
        test = install.back_to_twrp(fastboot)
        self.assertFalse(test(), "TWRP being ready is what ends the wait, not the reboot")
        self.assertFalse(test())
        self.assertEqual(fastboot.ran, [("reboot", "recovery")], "sent on once, not again once it left")
        self.assertFalse(install.back_to_twrp(None)())

    def test_fastboot_presence_probe_times_out_instead_of_hanging(self):
        fb = Fastboot("SERIAL")
        old_run = subprocess.run

        def fake_run(*args, **kwargs):
            self.assertEqual(kwargs.get("timeout"), 10)
            raise subprocess.TimeoutExpired(args[0], 10)

        subprocess.run = fake_run
        try:
            self.assertFalse(fb.present())
        finally:
            subprocess.run = old_run

    def test_install_show_uses_reconnect_contract_for_all_reboots(self):
        source = (TOOLS / "install-show.py").read_text(encoding="utf-8")
        self.assertIn("TWRP_DATA_REBOOT_TIMEOUT_SECONDS", source)
        self.assertIn("FASTBOOT_REENUM_TIMEOUT_SECONDS", source)
        self.assertIn("RESCUE_CONSOLE_TIMEOUT_SECONDS", source)
        self.assertIn("FIRST_BOOT_TIMEOUT_SECONDS", source)


if __name__ == "__main__":
    unittest.main()
