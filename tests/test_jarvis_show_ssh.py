import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import boot_logo as bl  # noqa: E402
from jarvis_crown.show_ssh import identity_for, key_path, ssh_options  # noqa: E402


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
