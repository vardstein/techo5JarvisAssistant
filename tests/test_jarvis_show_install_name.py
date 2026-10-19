"""An install without --name asks for the Show's name instead of stopping."""

import argparse
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from jarvis_crown.settings import Asker  # noqa: E402

SPEC = importlib.util.spec_from_file_location("jarvis_show_cli_install_name", TOOLS / "jarvis-show.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def asking(*answers):
    said = []
    left = list(answers)
    return Asker(True, ask=lambda _: left.pop(0), say=said.append), said


class InstallNameTests(unittest.TestCase):
    def test_a_given_name_is_not_asked(self):
        asker, said = asking()
        self.assertEqual(cli._install_name(argparse.Namespace(name="Kitchen"), asker), "Kitchen")
        self.assertEqual(said, [])

    def test_no_name_is_asked_for(self):
        asker, said = asking("  Bathroom ")
        self.assertEqual(cli._install_name(argparse.Namespace(name=None), asker), "Bathroom")
        self.assertTrue(any("the Show's name" in line for line in said))

    def test_a_name_the_install_would_refuse_is_asked_again(self):
        asker, said = asking("x" * 65, "Bath\x07room", "Bathroom")
        self.assertEqual(cli._install_name(argparse.Namespace(name=None), asker), "Bathroom")

    def test_nobody_to_ask_gives_none(self):
        self.assertIsNone(cli._install_name(argparse.Namespace(name=None), Asker(False)))

    def test_enter_alone_gives_none(self):
        asker, _ = asking("")
        self.assertIsNone(cli._install_name(argparse.Namespace(name=None), asker))


if __name__ == "__main__":
    unittest.main()
