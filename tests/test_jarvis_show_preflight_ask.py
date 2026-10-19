import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.device_gate import DeviceGateError, in_plain_words  # noqa: E402
from jarvis_crown.preflight import Check  # noqa: E402


class Answers:
    def __init__(self, answer):
        self.answer, self.asked = answer, []
        self.interactive = answer is not None

    def yes(self, label, *, help=""):
        self.asked.append(label)
        return self.answer


class PreflightAsksTests(unittest.TestCase):
    """GENBU-216: a passing preflight asks whether to go on, a failing one says what to do."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("jarvis_show_cli_preflight", ROOT / "tools" / "jarvis-show.py")
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        argv = ["jarvis-show.py", "preflight", "--board", "crown",
                "--work-dir", f"{td.name}/work", "--backup-dir", f"{td.name}/backups"]
        with mock.patch.object(sys, "argv", argv):
            self.args = self.cli.parse_args()

    def run_preflight(self, checks, answer):
        self.answers = Answers(answer)
        self.went_on = []
        main = self.cli._main
        with mock.patch.object(self.cli, "run_preflight", return_value=checks), \
                mock.patch.object(self.cli, "_asker", return_value=self.answers), \
                mock.patch.object(self.cli, "_main", side_effect=lambda a: self.went_on.append(a.command) or 7), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(self.args)
        self.out = out.getvalue() + err.getvalue()
        return code

    def test_all_passed_and_yes_goes_on_to_the_install(self):
        self.assertEqual(self.run_preflight([Check("PASS", "repo", "here")], True), 7)
        self.assertEqual(self.answers.asked, ["Go on with the install?"])
        self.assertEqual(self.went_on, ["install"])

    def test_no_ends_there_and_says_how_to_start_later(self):
        self.assertEqual(self.run_preflight([Check("PASS", "repo", "here")], False), 0)
        self.assertEqual(self.went_on, [])
        self.assertIn("python3 tools/jarvis-show.py install", self.out)

    def test_asked_nothing_it_ends_as_before(self):
        self.assertEqual(self.run_preflight([Check("PASS", "repo", "here")], None), 0)
        self.assertEqual(self.went_on, [])
        self.assertNotIn("Start it later", self.out)

    def test_a_failed_check_says_what_to_do_and_asks_nothing(self):
        checks = [Check("PASS", "repo", "here"),
                  Check("FAIL", "modemmanager", "active", "Stop it with: sudo systemctl stop ModemManager")]
        self.assertEqual(self.run_preflight(checks, True), 1)
        self.assertEqual(self.answers.asked, [])
        self.assertIn("sudo systemctl stop ModemManager", self.out)
        self.assertIn("1 check failed", self.out)
        self.assertIn("nothing on the Show was touched", self.out)

    def test_no_show_found_says_what_to_do(self):
        self.args.board = None
        with mock.patch.object(self.cli, "_detect_profile", side_effect=DeviceGateError("no fastboot device detected")), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.cli._main(self.args), 2)
        said = out.getvalue() + err.getvalue()
        self.assertIn("no fastboot device detected", said)
        self.assertIn("Plug it in over USB", said)


class InPlainWordsTests(unittest.TestCase):
    def test_each_known_gate_failure(self):
        for said, want in {
            "no fastboot device detected": "No Show was found",
            "exactly one fastboot device is required; detected: a, b": "More than one device",
            "cannot execute fastboot query fastboot devices: gone": "install Android platform-tools",
            "fastboot query timed out after 8s: fastboot devices": "stopped answering",
            "connected product 'CROWN' is crown, expected checkers": "another model than --board",
            "'KNIGHT' is not a supported Jarvis Show target. ...": "isn't one the installer supports",
        }.items():
            self.assertIn(want, in_plain_words(DeviceGateError(said)), said)

    def test_an_unknown_one_is_left_as_it_is(self):
        self.assertEqual(in_plain_words(DeviceGateError("something new")), "something new")


class FixesTests(unittest.TestCase):
    def test_every_failed_check_says_what_to_do(self):
        from jarvis_crown import preflight
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            (td / "lineage.zip").write_bytes(b"not a zip")
            with mock.patch.object(preflight.sys, "platform", "darwin"), \
                    mock.patch.object(preflight.sys, "version_info", (3, 9, 0)), \
                    mock.patch.object(preflight.os, "geteuid", return_value=1000), \
                    mock.patch.object(preflight, "_group_names", return_value=set()), \
                    mock.patch.object(preflight.shutil, "which", return_value=None), \
                    mock.patch.object(preflight, "_modemmanager_active", return_value=True), \
                    mock.patch.object(preflight.shutil, "disk_usage", return_value=mock.Mock(free=1)):
                checks = preflight.run_preflight(repo_root=td / "gone", amonet_dir=td / "amonet",
                                                 work_dir=td / "work", backup_dir=td / "backups",
                                                 lineage_zip=td / "lineage.zip")
        failed = [c for c in checks if c.failed]
        self.assertGreater(len(failed), 10)
        for check in failed:
            self.assertTrue(check.fix, check.name)


if __name__ == "__main__":
    unittest.main()
