import argparse
import contextlib
import importlib.util
import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import boot_logo as bl  # noqa: E402
from jarvis_crown import show_ssh  # noqa: E402
from jarvis_crown.show_ssh import (ShowKeyError, identity_for, key_path, make_key, owner_of,  # noqa: E402
                                   public_key, ssh_options)


class ShowKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_show_has_its_own_file(self):
        self.assertEqual(key_path("kitchenShow", self.keys), self.keys / "genbu_kitchenShow")
        self.assertNotEqual(key_path("kitchenShow", self.keys), key_path("bedroomShow", self.keys))

    def test_a_name_never_leaves_the_key_folder(self):
        for name in ("../id_ed25519", "a/b", "..", "Küche Show"):
            with self.subTest(name=name):
                path = key_path(name, self.keys)
                self.assertEqual(path.parent, self.keys)
                self.assertRegex(path.name, r"^genbu_[A-Za-z0-9._-]+$")

    def test_the_shows_key_is_used_when_it_is_there(self):
        self.assertIsNone(identity_for("kitchenShow", key_dir=self.keys))
        key_path("kitchenShow", self.keys).write_text("key", encoding="utf-8")
        self.assertEqual(identity_for("kitchenShow", key_dir=self.keys), self.keys / "genbu_kitchenShow")
        self.assertIsNone(identity_for("bedroomShow", key_dir=self.keys))

    def test_a_key_given_by_hand_wins(self):
        key_path("kitchenShow", self.keys).write_text("key", encoding="utf-8")
        given = self.keys / "other"
        self.assertEqual(identity_for("kitchenShow", given, key_dir=self.keys), given)

    def test_ssh_offers_that_key_and_no_other(self):
        self.assertEqual(ssh_options(None), [])
        self.assertEqual(ssh_options(pathlib.Path("/k/genbu_x")), ["-i", "/k/genbu_x", "-o", "IdentitiesOnly=yes"])

    def keygen(self, calls):
        def run(argv, **kw):
            calls.append(argv)
            path = pathlib.Path(argv[argv.index("-f") + 1])
            path.write_text("PRIVATE", encoding="utf-8")
            path.with_name(path.name + ".pub").write_text(
                f"ssh-ed25519 AAAA{path.name} {argv[argv.index('-C') + 1]}\n", encoding="utf-8")
            return bl.subprocess.CompletedProcess(argv, 0, "", "")
        return run

    def test_the_installer_makes_a_key_for_the_show(self):
        calls = []
        path, made = make_key("kitchenShow", self.keys, run=self.keygen(calls))
        self.assertTrue(made)
        self.assertEqual(path, self.keys / "genbu_kitchenShow")
        argv = calls[0]
        self.assertEqual(argv[:4], ["ssh-keygen", "-q", "-t", "ed25519"])
        self.assertEqual(argv[argv.index("-C") + 1], "genbu_kitchenShow")
        self.assertEqual(public_key(path.with_name(path.name + ".pub")), "ssh-ed25519 AAAAgenbu_kitchenShow")

    def test_a_pair_already_there_is_kept(self):
        calls = []
        make_key("kitchenShow", self.keys, run=self.keygen(calls))
        path, made = make_key("kitchenShow", self.keys, run=self.keygen(calls))
        self.assertFalse(made)
        self.assertEqual(len(calls), 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "PRIVATE")

    def test_half_a_pair_stops_it_untouched(self):
        key_path("kitchenShow", self.keys).write_text("mine", encoding="utf-8")
        with self.assertRaises(ShowKeyError):
            make_key("kitchenShow", self.keys, run=self.keygen([]))
        self.assertEqual(key_path("kitchenShow", self.keys).read_text(encoding="utf-8"), "mine")

    def test_ssh_keygen_failing_stops_it(self):
        def run(argv, **kw):
            return bl.subprocess.CompletedProcess(argv, 1, "", "no space")
        with self.assertRaisesRegex(ShowKeyError, "no space"):
            make_key("kitchenShow", self.keys, run=run)

    def test_one_key_never_opens_two_shows(self):
        make_key("kitchenShow", self.keys, run=self.keygen([]))
        make_key("bedroomShow", self.keys, run=self.keygen([]))
        kitchen = public_key(self.keys / "genbu_kitchenShow.pub")
        self.assertEqual(owner_of(kitchen, "bedroomShow", self.keys), "kitchenShow")
        self.assertIsNone(owner_of(kitchen, "kitchenShow", self.keys))
        self.assertIsNone(owner_of("ssh-ed25519 AAAAsomethingElse", "bedroomShow", self.keys))

    def cli_key(self, **given):
        spec = importlib.util.spec_from_file_location("jarvis_show_cli_ssh", ROOT / "tools" / "jarvis-show.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        args = argparse.Namespace(**{"name": "kitchenShow", "ssh_key": None, "no_ssh": False, **given})
        with mock.patch.object(show_ssh, "KEY_DIR", self.keys), contextlib.redirect_stdout(io.StringIO()) as out:
            return cli._show_ssh_key(args), out.getvalue()

    @unittest.skipUnless(shutil.which("ssh-keygen"), "needs ssh-keygen")
    def test_install_makes_the_key_and_says_so(self):
        pub, said = self.cli_key()
        self.assertEqual(pub, self.keys / "genbu_kitchenShow.pub")
        self.assertTrue(public_key(pub).startswith("ssh-ed25519 "))
        self.assertEqual((self.keys / "genbu_kitchenShow").stat().st_mode & 0o077, 0)
        self.assertIn("never used for another", said)
        self.assertIn("made now", said)
        again, said = self.cli_key()
        self.assertEqual(public_key(again), public_key(pub))
        self.assertIn("kept", said)

    def test_install_refuses_another_shows_key(self):
        make_key("bedroomShow", self.keys, run=self.keygen([]))
        with self.assertRaisesRegex(ShowKeyError, "bedroomShow"):
            self.cli_key(ssh_key=self.keys / "genbu_bedroomShow.pub")

    def test_no_ssh_means_no_key(self):
        pub, _ = self.cli_key(no_ssh=True)
        self.assertIsNone(pub)
        self.assertEqual(list(self.keys.iterdir()), [])

    def test_boot_logo_logs_in_with_it(self):
        seen = []

        def run(argv, **kw):
            seen.append(argv)
            return bl.subprocess.CompletedProcess(argv, 0, b"ok", b"")

        with mock.patch.object(bl.subprocess, "run", run):
            bl.SshClient("10.0.0.40", pathlib.Path("/k/genbu_x")).shell("true")
        self.assertEqual(seen[0][-2:], ["root@10.0.0.40", "true"])
        self.assertIn("IdentitiesOnly=yes", seen[0])
        self.assertEqual(seen[0][seen[0].index("-i") + 1], "/k/genbu_x")


if __name__ == "__main__":
    unittest.main()
