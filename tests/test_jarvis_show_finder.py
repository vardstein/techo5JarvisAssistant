import contextlib
import datetime
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import finder  # noqa: E402
from jarvis_crown.shows import known_shows, record_show, update_show  # noqa: E402

KEYED = '"api_encryption=Noise_NNpsk0_25519_ChaChaPoly_SHA256"'
SETUP = '"api_encryption_supported=Noise_NNpsk0_25519_ChaChaPoly_SHA256" "api_provisioning=zero-psk"'


def line(node, txt, *, proto="IPv4", address="10.0.0.5", platform="echolocal", mac="0cee00000005", friendly=None):
    return (f"=;wlan0;{proto};{node};_esphomelib._tcp;local;{node}.local;{address};6053;{txt} "
            f'"board=crown" "platform={platform}" "mac={mac}" "version=2026.5.1" '
            f'"friendly_name={friendly or node}"')


BROWSE = "\n".join([
    "+;wlan0;IPv4;kitchenshow5;_esphomelib._tcp;local",
    line("kitchenshow5", KEYED, friendly="kitchenShow5"),
    line("kitchenshow5", KEYED, proto="IPv6", address="fe80::1", friendly="kitchenShow5"),
    line("hallshow5", SETUP, mac="0cee00000006", friendly="hallShow5"),
    line("plug", KEYED, platform="ESP32", mac="aabbccddeeff"),
])


class ParseTests(unittest.TestCase):
    def test_shows_once_each_with_their_state(self):
        seen = finder.parse_browse(BROWSE)
        self.assertEqual([s.node for s in seen], ["hallshow5", "kitchenshow5"])
        hall, kitchen = seen
        self.assertTrue(hall.in_setup)
        self.assertFalse(kitchen.in_setup)
        self.assertEqual(kitchen.address, "10.0.0.5")
        self.assertEqual((kitchen.friendly, kitchen.board, kitchen.mac), ("kitchenShow5", "crown", "0cee00000005"))

    def test_a_name_with_a_space_and_a_quote(self):
        seen = finder.parse_browse(line("kitchen-show", KEYED, friendly='Kitchen \\"Show\\"'))
        self.assertEqual(seen[0].friendly, 'Kitchen "Show"')

    def test_no_avahi_is_none_and_a_slow_one_keeps_what_it_had(self):
        self.assertIsNone(finder.browse(which=lambda _: None))

        def slow(*_, **__):
            raise subprocess.TimeoutExpired("avahi-browse", 10, output=BROWSE)
        self.assertEqual(len(finder.browse(run=slow, which=lambda t: "/usr/bin/" + t)), 2)


def ran(outputs):
    def run(argv, **_):
        return types.SimpleNamespace(stdout=outputs.get(argv[0], ""), returncode=0)
    return run


class UsbTests(unittest.TestCase):
    def test_every_way_a_show_sits_on_usb(self):
        def ask(port):
            if port == "/dev/ttyACM1":
                raise RuntimeError("the console asks for root's password")
            return "G0000000000007 setup"
        units = finder.usb(run=ran({"fastboot": "G0000000000001\tfastboot\n",
                                    "adb": "List of devices attached\nG0000000000002\trecovery\n"
                                           "G0000000000003\tunauthorized\n"}),
                           which=lambda t: t, consoles=lambda: [("/dev/ttyACM0", ""), ("/dev/ttyACM1", "G9")],
                           ask=ask)
        self.assertEqual([(u.serial, u.state) for u in units],
                         [("G0000000000001", "fastboot"), ("G0000000000002", "recovery"),
                          ("G0000000000007", "in setup"), ("G9", "locked")])

    def test_no_tools_is_nothing_on_usb(self):
        self.assertEqual(finder.usb(which=lambda _: None), [])


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backups = pathlib.Path(self.tmp.name)
        self.keys = self.backups / "ssh"
        self.keys.mkdir()
        for serial, name in (("G0000000000005", "kitchenShow5"), ("G0000000000006", "hallShow5"),
                             ("G0000000000008", "atticShow5")):
            record_show(self.backups / serial, name=name, board="crown")
        update_show(self.backups / "G0000000000006", mac="0c:ee:00:00:00:06")

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, in_ha=lambda name: name == "kitchenShow5", usb=()):
        return {r.name: r for r in finder.merge(known_shows(self.backups), finder.parse_browse(BROWSE),
                                                list(usb), in_ha)}

    def test_records_meet_the_network_and_usb(self):
        rows = self.rows(usb=[finder.OnUsb("G0000000000008", "fastboot"), finder.OnUsb("G0000000000099", "android")])
        self.assertEqual(set(rows), {"kitchenShow5", "hallShow5", "atticShow5", "serial ending 0099"})
        self.assertEqual(rows["kitchenShow5"].state, "in this Home Assistant")
        self.assertIn("Discovered, Add", rows["hallShow5"].state)
        self.assertEqual(rows["atticShow5"].state, "on USB, in fastboot")
        self.assertFalse(rows["kitchenShow5"].leftover)
        self.assertTrue(rows["hallShow5"].leftover)   # reset by its Delete in Home Assistant, waiting again
        self.assertFalse(rows["atticShow5"].leftover)  # mid-install

    def test_somebody_elses_show_is_listed_not_claimed(self):
        rows = finder.merge([], finder.parse_browse(line("neighbourshow5", KEYED, friendly="neighbourShow5")), [],
                            lambda name: False)
        self.assertIn("not in this Home Assistant", rows[0].state)
        self.assertFalse(rows[0].leftover)

    def test_nothing_is_a_leftover_without_asking_home_assistant(self):
        rows = self.rows(in_ha=lambda name: None)
        self.assertFalse(any(r.leftover for r in rows.values()))

    def test_put_away_moves_the_keys_and_the_record_and_keeps_the_backups(self):
        row = self.rows()["atticShow5"]
        for f in ("genbu_atticShow5", "genbu_atticShow5.pub", "genbu_atticShow5.known_hosts"):
            (self.keys / f).write_text("x")
        unlock = self.backups / "G0000000000008" / "boot.img"
        unlock.write_text("x")
        self.assertTrue(row.leftover)
        self.assertFalse(row.reset_seen)  # only not around: maybe switched off
        self.assertEqual(len(finder.leftovers(row, self.keys)), 4)
        to = finder.retire(row, self.keys, today=datetime.date(2026, 10, 19))
        self.assertEqual(to.name, "retired-2026-10-19")
        self.assertEqual(sorted(p.name for p in to.iterdir()),
                         ["genbu_atticShow5", "genbu_atticShow5.known_hosts", "genbu_atticShow5.pub", "show.json"])
        self.assertEqual(list(self.keys.iterdir()), [])
        self.assertTrue(unlock.is_file())
        self.assertNotIn("atticShow5", [s.name for s in known_shows(self.backups)])

    def test_router_note_names_the_rules(self):
        self.assertIn("genbu_unrules atticShow5", finder.router_note(self.rows()["atticShow5"]))


def _cli():
    spec = importlib.util.spec_from_file_location("jarvis_show_cli_shows", ROOT / "tools" / "jarvis-show.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class ShowsCommandTests(unittest.TestCase):
    def run_it(self, *, no_questions=False, yes_remove=False, answers=(), in_ha=lambda name: name == "kitchenShow5"):
        cli = _cli()
        args = types.SimpleNamespace(no_questions=no_questions, yes_remove=yes_remove)
        asked = iter(answers)
        asker = types.SimpleNamespace(yes=lambda label, **_: next(asked))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "_asker", return_value=asker), \
                mock.patch.object(cli, "_in_this_house", return_value=in_ha), \
                mock.patch.object(cli, "_on_usb", return_value=[]), \
                mock.patch.object(cli.finder, "browse", return_value=finder.parse_browse(BROWSE)), \
                mock.patch.object(cli.finder, "key_path", lambda name, key_dir=None: pathlib.Path(tmp) / f"genbu_{name}"), \
                mock.patch.object(cli.finder, "known_hosts_path",
                                  lambda name, key_dir=None: pathlib.Path(tmp) / f"genbu_{name}.known_hosts"), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            backups = pathlib.Path(tmp) / "backups"
            record_show(backups / "G0000000000005", name="kitchenShow5", board="crown")
            record_show(backups / "G0000000000006", name="hallShow5", board="crown")
            (pathlib.Path(tmp) / "genbu_hallShow5").write_text("x")
            code = cli.shows_command(args, backups)
            self.left = sorted(p.name for p in pathlib.Path(tmp).glob("genbu_*"))
            self.records = [s.name for s in known_shows(backups)]
        self.out = out.getvalue() + err.getvalue()
        self.assertNotIn("10.0.0.5", self.out)
        return code

    def test_lists_and_puts_away_when_asked(self):
        self.assertEqual(self.run_it(answers=[True]), 0)
        self.assertIn("kitchenShow5 (crown): in this Home Assistant", self.out)
        self.assertIn("hallShow5 (crown): waiting to be added", self.out)
        self.assertIn("it was reset, so the key opens nothing now", self.out)
        self.assertIn("put away in", self.out)
        self.assertIn("genbu_unrules hallShow5", self.out)
        self.assertEqual(self.left, [])
        self.assertEqual(self.records, ["kitchenShow5"])

    def test_no_keeps_everything(self):
        self.assertEqual(self.run_it(answers=[False]), 0)
        self.assertEqual(self.left, ["genbu_hallShow5"])
        self.assertEqual(sorted(self.records), ["hallShow5", "kitchenShow5"])

    def test_no_questions_only_lists(self):
        self.assertEqual(self.run_it(no_questions=True), 0)
        self.assertIn("--no-questions only lists", self.out)
        self.assertEqual(self.left, ["genbu_hallShow5"])

    def test_without_home_assistant_nothing_counts_as_gone(self):
        self.assertEqual(self.run_it(in_ha=lambda name: None, yes_remove=True), 0)
        self.assertNotIn("Left here", self.out)
        self.assertEqual(self.left, ["genbu_hallShow5"])


if __name__ == "__main__":
    unittest.main()
