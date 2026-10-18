"""The install needs dialout (or uucp) for the Show's rescue console. A login that joined it after it started
has not got it yet: the installer runs itself again under sg instead of telling someone to."""

import argparse
import contextlib
import importlib.util
import io
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

SPEC = importlib.util.spec_from_file_location("jarvis_show_cli_serial_group", TOOLS / "jarvis-show.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

ARGV = ["tools/jarvis-show.py", "install", "--channel", "dev"]


class Run:
    """_serial_group with the groups, the system and the answer as given; what it printed and ran."""

    def __init__(self, *, session=(), listed=(), nixos=False, answer=None, sudo=0, again=""):
        self.execs, self.calls = [], []
        out = io.StringIO()
        env = {cli.SERIAL_AGAIN: again} if again else {}
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(contextlib.redirect_stdout(out))
            enter(contextlib.redirect_stderr(out))
            enter(mock.patch.dict(cli.os.environ, env, clear=False))
            if not again:
                cli.os.environ.pop(cli.SERIAL_AGAIN, None)
            enter(mock.patch.object(cli.os, "geteuid", return_value=1000))
            enter(mock.patch.object(cli, "_group_names", return_value=set(session)))
            enter(mock.patch.object(cli, "_in_group", side_effect=lambda g, user: g in listed))
            enter(mock.patch.object(cli.Path, "exists", lambda p: nixos and str(p) == "/etc/NIXOS"))
            enter(mock.patch.object(cli.shutil, "which", return_value="/run/wrappers/bin/sg"))
            enter(mock.patch.object(cli.sys, "argv", ARGV))
            enter(mock.patch.object(cli.sys, "executable", "/usr/bin/python3"))
            enter(mock.patch.object(cli.os, "execv", side_effect=lambda path, argv: self.execs.append(argv)))
            enter(mock.patch.object(cli.subprocess, "call",
                                    side_effect=lambda argv: self.calls.append(argv) or sudo))
            asker = mock.Mock()
            asker.yes.return_value = answer
            enter(mock.patch.object(cli, "_asker", return_value=asker))
            self.asked = asker.yes
            self.code = cli._serial_group(argparse.Namespace(no_questions=False))
        self.out = out.getvalue()


AGAIN = "/usr/bin/python3 tools/jarvis-show.py install --channel dev"


class SerialGroupTests(unittest.TestCase):
    def test_a_login_that_has_it_goes_on(self):
        run = Run(session={"dialout"})
        self.assertIsNone(run.code)
        self.assertEqual(run.execs, [])

    def test_a_member_whose_login_predates_it_runs_again_under_sg(self):
        run = Run(listed={"dialout"})
        self.assertEqual(run.execs, [["/run/wrappers/bin/sg", "dialout", "-c", AGAIN]])
        run.asked.assert_not_called()
        self.assertEqual(run.calls, [])

    def test_uucp_counts_too(self):
        run = Run(listed={"uucp"})
        self.assertEqual(run.execs[0][1], "uucp")

    def test_it_runs_again_only_once(self):
        run = Run(listed={"dialout"}, again="1")
        self.assertEqual(run.code, 1)
        self.assertEqual(run.execs, [])
        self.assertIn(f"sg dialout -c '{AGAIN}'", run.out)  # quoted, so it is one command

    def test_someone_not_in_it_is_offered_the_usermod(self):
        run = Run(answer=True)
        self.assertEqual(run.calls, [["sudo", "usermod", "-aG", "dialout", mock.ANY]])
        self.assertEqual(run.execs, [["/run/wrappers/bin/sg", "dialout", "-c", AGAIN]])

    def test_a_no_touches_nothing_and_says_what_to_run(self):
        run = Run(answer=False)
        self.assertEqual((run.code, run.calls, run.execs), (1, [], []))
        self.assertIn("sudo usermod -aG dialout", run.out)
        self.assertIn(f"sg dialout -c '{AGAIN}'", run.out)

    def test_a_failed_usermod_does_not_run_again(self):
        run = Run(answer=True, sudo=1)
        self.assertEqual((run.code, run.execs), (1, []))

    def test_nixos_is_told_its_configuration_line_not_a_usermod(self):
        run = Run(nixos=True)
        run.asked.assert_not_called()
        self.assertEqual((run.code, run.calls, run.execs), (1, [], []))
        self.assertIn("extraGroups", run.out)
        self.assertNotIn("usermod", run.out)


if __name__ == "__main__":
    unittest.main()
