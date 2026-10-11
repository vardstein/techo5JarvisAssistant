import ipaddress
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import secret_scan as ss  # noqa: E402

# Built from pieces, so this file does not hold anything the scanner (or anyone else's) would flag.
GH = "gh" + "p_" + "A1b2C3d4" * 4 + "Zz9Y"
JWT = "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JpYXQiOjE3MDAwMDAwMDB9." + "Qm9ndXNTaWduYXR1cmVYWVo"
KEY = ["-----BEGIN OPENSSH " + "PRIVATE KEY-----", "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB"]


def kinds(path, *lines, local=None):
    return [k for _, k in ss.scan_lines(path, lines, local)]


class FakeLocal(ss.Local):
    def __init__(self):
        self.nets = [ipaddress.ip_network("10.20.30.0/24")]
        self.home = "/home/someone"
        self.keys = {"AAAAC3NzaC1lZDI1NTE5AAAAIOurOwnKeyBody"}


class SecretScanTest(unittest.TestCase):
    def test_tokens_are_found_everywhere_even_in_tests(self):
        self.assertEqual(kinds("tests/test_x.py", f'X = "{GH}"'), ["access token"])
        self.assertEqual(kinds("docs/a.md", f"Authorization: Bearer {JWT}"), ["Home Assistant token (JWT)"])

    def test_a_private_key_needs_its_body(self):
        self.assertEqual(kinds("x/key.txt", *KEY), ["private key"])
        # Only the header, as a test that refuses it uses it, is not a key.
        self.assertEqual(kinds("echod/x_test.go", '"' + KEY[0] + '",', '"ssh-ed25519",'), [])

    def test_a_password_in_code_is_found_but_not_in_tests_or_examples(self):
        line = 'hass_password = "k8Jd02nfLq93mZpQ"'
        self.assertEqual(kinds("tools/x.py", line), ["password or key in the code"])
        self.assertEqual(kinds("tests/test_x.py", line), [])
        self.assertEqual(kinds("tools/x.py", 'token: "${{ secrets.GITHUB_TOKEN }}"'), [])
        self.assertEqual(kinds("tools/x.py", 'password = "example-password-1"'), [])

    def test_allow_marker(self):
        self.assertEqual(kinds("docs/a.md", f"{GH}  <!-- secret-scan: allow -->"), [])

    def test_this_machines_things_only_with_here(self):
        lines = ("host = 10.20.30.40", "see /home/someone/notes", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOurOwnKeyBody me")
        self.assertEqual(kinds("docs/a.md", *lines), [])
        self.assertEqual(kinds("docs/a.md", *lines, local=FakeLocal()), [
            "address on this machine's network", "this machine's home directory", "one of our own public SSH keys"])
        self.assertEqual(kinds("docs/a.md", "host = 10.0.0.5", local=FakeLocal()), [])

    def test_the_repo_is_clean(self):
        found = sorted(set(ss.scan_tree(None)))
        self.assertEqual(found, [], "secrets in the tree; run tools/secret_scan.py")


if __name__ == "__main__":
    unittest.main()
