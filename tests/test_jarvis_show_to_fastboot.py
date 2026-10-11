import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.shows import record_show  # noqa: E402
from jarvis_crown.to_fastboot import ToFastbootError, bring_to_fastboot  # noqa: E402

BOARDS = {"crown": "G000AB1234508TU", "checkers": "G000CD5678905BJ"}


class FakeShow:
    """fastboot, adb and ssh as one Show in one state answers them; a reboot command moves it to fastboot."""

    def __init__(self, state, serial="G000AB1234508TU", *, adb_rows=None, comes_back=True):
        self.state, self.serial, self.comes_back = state, serial, comes_back
        self.adb_rows = adb_rows
        self.commands = []

    def __call__(self, argv, **kwargs):
        self.commands.append(argv)
        out = ""
        if argv[:2] == ["fastboot", "devices"]:
            out = f"{self.serial}\tfastboot\n" if self.state == "fastboot" else ""
        elif argv[:2] == ["adb", "devices"]:
            rows = self.adb_rows if self.adb_rows is not None else (
                [(self.serial, self.state)] if self.state in ("device", "recovery", "unauthorized") else [])
            out = "List of devices attached\n" + "".join(f"{s}\t{st}\n" for s, st in rows)
        elif argv[0] == "adb" and argv[-2:] == ["reboot", "bootloader"]:
            self.reboot()
        elif argv[0] == "ssh":
            command = argv[-1]
            if self.state != "jarvis":
                return subprocess.CompletedProcess(argv, 255, "", "ssh: connect to host: No route to host")
            if command.endswith("rebootto bootloader"):
                self.reboot()
                return subprocess.CompletedProcess(argv, 255, "", "Connection closed by remote host")
            out = f"rebootto\nandroidboot.serialno={self.serial}\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    def reboot(self):
        self.state = "fastboot" if self.comes_back else "gone"

    def ran(self, *words):
        return any(all(w in " ".join(argv) for w in words) for argv in self.commands)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def bring(show, backups, **kw):
    clock = Clock()
    return bring_to_fastboot(backups=backups, run=show, sleep=clock.sleep, clock=clock, say=lambda _: None,
                             **{"name": None, **kw})


def installed(backups, name, board, serial, address="10.0.0.40"):
    folder = backups / serial
    record_show(folder, name=name, board=board)
    (folder / "address").write_text(address + "\n", encoding="utf-8")


class BringToFastbootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backups = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_show_already_on_fastboot_is_left_alone(self):
        show = FakeShow("fastboot")
        self.assertEqual(bring(show, self.backups), "")
        self.assertEqual(show.commands, [["fastboot", "devices"]])

    def test_android_or_twrp_goes_there_with_adb(self):
        for board, serial in BOARDS.items():
            for state in ("device", "recovery"):
                with self.subTest(board=board, state=state):
                    show = FakeShow(state, serial)
                    self.assertEqual(bring(show, self.backups), "adb")
                    self.assertTrue(show.ran("adb", "-s", serial, "reboot", "bootloader"))
                    self.assertEqual(show.state, "fastboot")

    def test_twrp_stays_when_the_install_carries_on_from_there(self):
        show = FakeShow("recovery")
        self.assertEqual(bring(show, self.backups, keep_recovery=True), "")
        self.assertFalse(show.ran("reboot"))

    def test_several_adb_devices_or_an_unanswered_prompt_reboot_nothing(self):
        cases = [(FakeShow("device", adb_rows=[("A1", "device"), ("B2", "device")]), "2 devices on adb"),
                 (FakeShow("unauthorized"), "accept the prompt")]
        for show, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ToFastbootError, message):
                    bring(show, self.backups)
                self.assertFalse(show.ran("reboot"))

    def test_a_show_running_jarvis_goes_there_over_ssh_after_its_serial_checks_out(self):
        for board, serial in BOARDS.items():
            with self.subTest(board=board):
                installed(self.backups, f"{board}Show", board, serial)
                show = FakeShow("jarvis", serial)
                self.assertEqual(bring(show, self.backups, name=f"{board}show"), "ssh")
                self.assertTrue(show.ran("root@10.0.0.40", "rebootto bootloader"))
                self.assertEqual(show.state, "fastboot")

    def test_the_shows_own_key_is_the_one_ssh_offers(self):
        installed(self.backups, "crownShow", "crown", BOARDS["crown"])
        show = FakeShow("jarvis", BOARDS["crown"])
        key = pathlib.Path("/keys/genbu_crownShow")
        self.assertEqual(bring(show, self.backups, name="crownShow", identity=key), "ssh")
        for argv in (a for a in show.commands if a[0] == "ssh"):
            self.assertIn("-i", argv)
            self.assertEqual(argv[argv.index("-i") + 1], str(key))
            self.assertIn("IdentitiesOnly=yes", argv)

    def test_a_different_show_at_the_recorded_address_is_not_rebooted(self):
        installed(self.backups, "livingRoom", "crown", BOARDS["crown"])
        show = FakeShow("jarvis", BOARDS["checkers"])
        with self.assertRaisesRegex(ToFastbootError, "serial ending 05BJ, not 08TU.*nothing was rebooted"):
            bring(show, self.backups, name="livingRoom")
        self.assertFalse(show.ran("rebootto bootloader"))

    def test_nothing_found_and_no_known_show_says_what_to_do(self):
        with self.assertRaisesRegex(ToFastbootError, "no Show on fastboot or adb.*--name"):
            bring(FakeShow("off"), self.backups, name="kitchen")

    def test_an_unreachable_show_says_to_use_fastboot_by_hand(self):
        installed(self.backups, "kitchen", "checkers", BOARDS["checkers"])
        with self.assertRaisesRegex(ToFastbootError, "can't be sent there over SSH.*No route"):
            bring(FakeShow("off", BOARDS["checkers"]), self.backups, name="kitchen")

    def test_a_reboot_that_never_reaches_this_computer_ends_with_the_way_back(self):
        installed(self.backups, "kitchen", "crown", BOARDS["crown"])
        show = FakeShow("jarvis", BOARDS["crown"], comes_back=False)
        with self.assertRaisesRegex(ToFastbootError, "USB cable.*fastboot continue"):
            bring(show, self.backups, name="kitchen")


if __name__ == "__main__":
    unittest.main()
