"""The reset over SSH before a Show leaves the house (jarvis_crown/show_reset.py), with ssh stood in for."""

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import home_assistant as ha  # noqa: E402
from jarvis_crown import show_reset  # noqa: E402

HOST = "10.0.0.5"
KEY = pathlib.Path("/home/someone/.ssh/genbu_genbuShow5")
SERIAL = "G0000000000005"


class Ssh:
    """ssh as the Show answers it: the probe's lines, the mark it leaves, the restart."""

    def __init__(self, *, genbu=True, keeps_wifi=True, serial=SERIAL, refused=False, mark_ok=True):
        self.genbu, self.keeps_wifi, self.serial = genbu, keeps_wifi, serial
        self.refused, self.mark_ok = refused, mark_ok
        self.commands = []

    def __call__(self, argv, **kw):
        self.argv = argv
        command = argv[-1]
        self.commands.append(command)
        if self.refused:
            return subprocess.CompletedProcess(argv, 255, "", f"root@{HOST}: Permission denied (publickey).\n")
        if command == show_reset.PROBE:
            out = ("genbu\n" if self.genbu else "") + ("keeps-wifi\n" if self.keeps_wifi else "") \
                + f"androidboot.serialno={self.serial}\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "factory_reset" in command:
            if not self.mark_ok:
                return subprocess.CompletedProcess(argv, 1, "", "sh: can't create: Read-only file system\n")
            lines = command.split("printf '%s\\n' ", 1)[1].split(" >", 1)[0]
            return subprocess.CompletedProcess(argv, 0, "\n".join(lines.split()) + "\n", "")
        if command == "reboot":
            raise subprocess.TimeoutExpired(argv, 15)
        raise AssertionError(command)


def probe(ssh, *, expected=SERIAL, identity=KEY):
    return show_reset.probe(HOST, name="genbuShow5", identity=identity, expected=expected, run=ssh)


class ProbeTests(unittest.TestCase):
    def test_a_genbu_show_with_its_serial(self):
        ssh = Ssh()
        reach = probe(ssh)
        self.assertEqual((reach.serial, reach.keeps_wifi), (SERIAL, True))
        self.assertIn("BatchMode=yes", ssh.argv)
        self.assertEqual(ssh.argv[ssh.argv.index("-i") + 1], str(KEY))
        self.assertIn("HostKeyAlias=genbu-genbushow5", ssh.argv)

    def test_an_older_image_cannot_keep_the_wifi(self):
        self.assertFalse(probe(Ssh(keeps_wifi=False)).keeps_wifi)

    def test_another_serial_is_refused(self):
        with self.assertRaisesRegex(show_reset.ResetError, "ending 0006, not 0005"):
            probe(Ssh(serial="G0000000000006"))

    def test_without_a_record_any_serial_goes(self):
        self.assertEqual(probe(Ssh(serial="G0000000000006"), expected=None).serial, "G0000000000006")

    def test_without_its_own_key_nothing_is_tried(self):
        ssh = Ssh()
        with self.assertRaisesRegex(show_reset.ResetError, "no key for it"):
            probe(ssh, identity=None)
        self.assertEqual(ssh.commands, [])

    def test_ssh_failing_never_says_the_address(self):
        with self.assertRaises(show_reset.ResetError) as caught:
            probe(Ssh(refused=True))
        self.assertIn("Permission denied", str(caught.exception))
        self.assertNotIn(HOST, str(caught.exception))

    def test_something_else_there_is_not_a_show(self):
        with self.assertRaisesRegex(show_reset.ResetError, "not look like a Genbu Show"):
            probe(Ssh(genbu=False))


class ResetTests(unittest.TestCase):
    def reach(self, keeps_wifi=True):
        return show_reset.Reach(host=HOST, serial=SERIAL, keeps_wifi=keeps_wifi)

    def test_leaves_the_mark_then_restarts(self):
        ssh = Ssh()
        show_reset.reset(self.reach(), keep_wifi=False, name="genbuShow5", identity=KEY, run=ssh)
        self.assertIn("printf '%s\\n' ssh > /data/misc/techo5/factory_reset.new", ssh.commands[0])
        self.assertIn("umask 077", ssh.commands[0])
        self.assertEqual(ssh.commands[1], "reboot")

    def test_keep_wifi_is_a_line_of_the_mark(self):
        ssh = Ssh()
        show_reset.reset(self.reach(), keep_wifi=True, name="genbuShow5", identity=KEY, run=ssh)
        self.assertIn("printf '%s\\n' ssh keep-wifi >", ssh.commands[0])

    def test_keep_wifi_on_an_older_image_touches_nothing(self):
        ssh = Ssh()
        with self.assertRaisesRegex(show_reset.ResetError, "can't keep the Wi-Fi"):
            show_reset.reset(self.reach(keeps_wifi=False), keep_wifi=True, name="genbuShow5", identity=KEY, run=ssh)
        self.assertEqual(ssh.commands, [])

    def test_no_mark_no_restart(self):
        ssh = Ssh(mark_ok=False)
        with self.assertRaisesRegex(show_reset.ResetError, "the mark was not left"):
            show_reset.reset(self.reach(), keep_wifi=False, name="genbuShow5", identity=KEY, run=ssh)
        self.assertNotIn("reboot", ssh.commands)

    def test_the_mark_is_what_the_boot_script_reads(self):
        lib = (ROOT / "tools/linux/techo5-lib.sh").read_text()
        self.assertIn(f"T5_RESET={show_reset.MARK}\n", lib)
        self.assertIn("grep -qx keep-wifi", lib)


class AddressTests(unittest.TestCase):
    class House:
        def __init__(self, states):
            self.states = states

        def request(self, method, path, body=None):
            entity = path.rsplit("/", 1)[1]
            if entity not in self.states:
                raise ha.NotFound(path)
            return {"state": self.states[entity]}

    ENTITIES = ["light.genbushow5_ring", "sensor.genbushow5_ip_address"]

    def test_the_given_host_first(self):
        self.assertEqual(show_reset.address(self.House({}), self.ENTITIES, host="10.0.0.9"), "10.0.0.9")

    def test_the_ip_sensor_in_home_assistant(self):
        house = self.House({"sensor.genbushow5_ip_address": HOST})
        self.assertEqual(show_reset.address(house, self.ENTITIES), HOST)

    def test_an_unknown_sensor_falls_back_to_the_record_then_mdns(self):
        house = self.House({"sensor.genbushow5_ip_address": "unavailable"})
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            self.assertEqual(show_reset.address(house, self.ENTITIES, folder=folder, find_host=lambda: "10.0.0.7"),
                             "10.0.0.7")
            (folder / "address").write_text("10.0.0.8\n")
            self.assertEqual(show_reset.address(house, self.ENTITIES, folder=folder), "10.0.0.8")
        self.assertIsNone(show_reset.address(house, []))


if __name__ == "__main__":
    unittest.main()
