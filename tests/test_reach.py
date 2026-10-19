import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import reach  # noqa: E402
from jarvis_crown.settings import Settings  # noqa: E402

MAC = "02:00:00:00:00:01"


def fake_ssh(blocked=(), year="2026", nc=True, code=0, stderr=""):
    """A Show that answers the probe script, with the targets in blocked failing."""
    seen = []

    def run(argv, script):
        seen.append((argv, script))
        out = [] if nc else ["NO-NC"]
        for line in script.splitlines():
            if line.startswith("if nc"):
                key = line.split("'REACH ")[1].split()[0]
                out.append(f"REACH {key} {'no' if key in blocked else 'ok'}")
        out += [f"MAC {MAC.upper()}", f"YEAR {year}"]
        return subprocess.CompletedProcess(argv, code, "\n".join(out) + "\n", stderr)
    run.seen = seen
    return run


class TargetsTests(unittest.TestCase):
    def test_what_a_show_dials(self):
        got = reach.targets("https://ha.example.org", "10.0.0.5:9555", "10.0.0.6")
        self.assertEqual([(t.key, t.host, t.port) for t in got], [
            ("haos", "ha.example.org", 443), ("dashcast", "10.0.0.5", 9555),
            ("mass", "10.0.0.6", 8095), ("github", "github.com", 443)])

    def test_ports_come_from_the_settings(self):
        got = reach.targets("http://10.0.0.4:8123/", "[fd00::5]:9600", None)
        self.assertEqual([(t.key, t.host, t.port) for t in got],
                         [("haos", "10.0.0.4", 8123), ("dashcast", "fd00::5", 9600), ("github", "github.com", 443)])

    def test_what_isnt_set_up_isnt_probed(self):
        self.assertEqual([t.key for t in reach.targets(None, "", None)], ["github"])

    def test_nothing_odd_reaches_the_shell(self):
        got = reach.targets("https://ha.example.org", "x;reboot:9555", "$(id)")
        self.assertEqual([t.key for t in got], ["haos", "github"])


class ProbeTests(unittest.TestCase):
    want = reach.targets("https://10.0.0.4", "10.0.0.5:9555", "10.0.0.6")

    def test_all_reached(self):
        run = fake_ssh()
        found = reach.probe("10.0.0.20", self.want, identity=pathlib.Path("/k"), name="Kitchen", run=run,
                            find_by_name=lambda: "10.0.0.20")
        self.assertTrue(found.ok)
        self.assertEqual(found.mac, MAC)
        argv = run.seen[0][0]
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("HostKeyAlias=genbu-kitchen", argv)
        self.assertEqual(argv[-3:], ["root@10.0.0.20", "sh", "-s"])

    def test_blocked_ones_and_a_clock_never_set(self):
        found = reach.probe("10.0.0.20", self.want, identity=None, name="Kitchen",
                            run=fake_ssh(blocked={"dashcast", "github"}, year="1970"))
        self.assertEqual([t.key for t in found.blocked], ["dashcast", "github"])
        self.assertIs(found.clock_set, False)
        self.assertIsNone(found.found_by_name)
        self.assertFalse(found.ok)

    def test_not_found_by_name(self):
        found = reach.probe("10.0.0.20", self.want, identity=None, name="K", run=fake_ssh(), find_by_name=lambda: None)
        self.assertIs(found.found_by_name, False)
        self.assertFalse(found.ok)

    def test_ssh_failing_says_so_and_finds_nothing_blocked(self):
        found = reach.probe("10.0.0.20", self.want, identity=None, name="K",
                            run=fake_ssh(code=255, stderr="Permission denied (publickey)."))
        self.assertIn("Permission denied", found.trouble)
        self.assertEqual(found.blocked, [])

    def test_no_nc_on_the_show(self):
        found = reach.probe("10.0.0.20", self.want, identity=None, name="K", run=fake_ssh(nc=False))
        self.assertIn("no nc", found.trouble)


class RulesTests(unittest.TestCase):
    def found(self, **kw):
        want = {t.key: t for t in reach.targets("https://ha.example.org", "10.0.0.5:9555", "10.0.0.6")}
        f = reach.Reach("10.0.0.20", mac=MAC, clock_set=True, found_by_name=True)
        for k, v in kw.items():
            setattr(f, k, v)
        f.blocked = [want[k] for k in kw.get("blocked", [])]
        return f

    def test_each_line_is_filled_in(self):
        lines = reach.missing_rules(self.found(blocked=["haos", "github"], clock_set=False, found_by_name=False),
                                    resolve=lambda h: f"{h} (10.0.0.4)")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "Home Assistant: allow TCP from the Show 10.0.0.20 (MAC 02:00:00:00:00:01) to "
                         "ha.example.org (10.0.0.4) port 443. Without it the Home Assistant step waits for the "
                         "Show to hand over its assistant and wake words and gives up, and the dashboard can't log in.")
        self.assertIn("to the internet port 443", lines[1])
        self.assertIn("UDP from the Show 10.0.0.20", lines[2])
        self.assertIn("port 123", lines[2])
        self.assertIn("UDP port 5353", lines[3])
        for line in lines:
            self.assertNotIn("<", line)
            self.assertNotIn("None", line)

    def test_nothing_missing(self):
        self.assertEqual(reach.missing_rules(self.found()), [])

    def test_the_openwrt_line(self):
        self.assertEqual(reach.openwrt_line(self.found(), name="Kitchen Show 5", music_assistant="10.0.0.6"),
                         "genbu_rules KitchenShow5 10.0.0.20 10.0.0.6")
        self.assertEqual(reach.rule_name("5 Küche"), "show5Kche")

    def test_no_openwrt_line_with_a_value_missing(self):
        self.assertIsNone(reach.openwrt_line(self.found(), name="Kitchen", music_assistant=None))
        self.assertIsNone(reach.openwrt_line(self.found(), name="Kitchen", music_assistant="ma.example.org"))
        self.assertIsNone(reach.openwrt_line(self.found(), name="---", music_assistant="10.0.0.6"))
        self.assertIsNone(reach.openwrt_line(self.found(address="fd00::20"), name="K", music_assistant="10.0.0.6"))


class Answers:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []
        self.interactive = bool(answers)

    def yes(self, label, *, help=""):
        self.asked.append(label)
        return self.answers.pop(0) if self.answers else None


class CheckReachTests(unittest.TestCase):
    """The installer's side: what it says, and that it offers to look again."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("jarvis_show_cli_reach", ROOT / "tools" / "jarvis-show.py")
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        self.settings = Settings(ha_url="https://10.0.0.4", dashcast="10.0.0.5:9555", music_assistant="10.0.0.6")
        self.args = mock.Mock(no_ssh=False, ssh_identity=None)

    def check(self, rounds, answers, host="10.0.0.20"):
        probes = [reach.Reach("10.0.0.20", mac=MAC, clock_set=True, blocked=[t for t in self.want() if t.key in r])
                  for r in rounds]
        self.answers = Answers(*answers)
        with mock.patch.object(self.cli.reach, "probe", side_effect=probes) as probe, \
                mock.patch.object(self.cli, "_asker", return_value=self.answers), \
                mock.patch.object(self.cli.shutil, "which", return_value=None), \
                mock.patch.object(self.cli, "_find_host", return_value=lambda: None), \
                mock.patch.object(self.cli.ui, "out", self.cli.ui.Out(io.StringIO(), io.StringIO(), color=False, fancy=False, width=1000)):
            self.cli.check_reach(self.settings, self.args, name="Kitchen", host=host)
            self.out = self.cli.ui.out.stream.getvalue() + self.cli.ui.out.err.getvalue()
            self.warnings = list(self.cli.ui.out.warnings)
        return probe

    def want(self):
        return reach.targets(self.settings.ha_url, self.settings.dashcast, self.settings.music_assistant)

    def test_all_reached_asks_nothing(self):
        self.check([set()], [True])
        self.assertEqual(self.answers.asked, [])
        self.assertIn("it reaches Home Assistant, DashCast, Music Assistant, updates", self.out)
        self.assertIn("avahi-resolve-host-name isn't on this computer", self.out)

    def test_blocked_names_the_rule_and_checks_again(self):
        probe = self.check([{"dashcast"}, set()], [True])
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(self.answers.asked, ["Check again?"])
        self.assertIn("DashCast: allow TCP from the Show 10.0.0.20", self.out)
        self.assertIn("genbu_rules Kitchen 10.0.0.20 10.0.0.6", self.out)
        self.assertIn("Nothing on the router was changed", self.out)
        self.assertEqual(self.warnings, [])

    def test_going_on_without_the_rules_leaves_one_warning(self):
        self.check([{"dashcast", "github"}, {"github"}], [True, False])
        self.assertEqual(self.warnings, ["1 router rule still missing, see above"])

    def test_nobody_to_ask_says_it_once_and_goes_on(self):
        probe = self.check([{"haos"}], [])
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(self.warnings, ["1 router rule still missing, see above"])

    def test_no_address_no_check(self):
        probe = self.check([], [], host=None)
        probe.assert_not_called()
        self.assertIn("its address isn't known", self.out)

    def test_no_ssh_no_check(self):
        self.args.no_ssh = True
        probe = self.check([], [])
        probe.assert_not_called()

    def test_runs_before_the_home_assistant_step(self):
        with tempfile.TemporaryDirectory() as td:
            key = pathlib.Path(td) / "home-assistant.key"
            key.write_text("k\n")
            (pathlib.Path(td) / "address").write_text("10.0.0.20\n")
            with mock.patch.object(self.cli, "check_reach") as check, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.cli.deploy_to_home_assistant(self.settings, self.args, name="Kitchen", key_file=key, host=None,
                                                  wait_seconds=1, backups=pathlib.Path(td))
        check.assert_called_once_with(self.settings, self.args, name="Kitchen", host="10.0.0.20")


if __name__ == "__main__":
    unittest.main()
