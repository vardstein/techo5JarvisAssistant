"""GENBU-163 stage c, step 1: board.json, what a Show keeps of its own board.

The installer writes it from the catalogue (catalogue.on_show), factory reset keeps it, and echod
embeds a copy of every board's (echod/internal/board/boards), which echod's own tests read. Nothing
uses it to decide anything yet: echod reads it beside its guess and logs whether the two agree."""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import catalogue  # noqa: E402
from jarvis_crown.catalogue import CatalogueError  # noqa: E402

ECHOD_BOARDS = ROOT / "echod" / "internal" / "board" / "boards"


def load_installer():
    spec = importlib.util.spec_from_file_location("install_show", ROOT / "tools" / "install-show.py")
    show = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(show)
    return show


class OnShowTest(unittest.TestCase):
    def setUp(self):
        self.cat = catalogue.default()

    def test_only_what_the_show_reads(self):
        for board in self.cat.boards():
            with self.subTest(board=board):
                out = self.cat.on_show(board)
                self.assertEqual(tuple(out), catalogue.ON_SHOW_KEYS)
                self.assertEqual(out["identity"], self.cat.resolve(board)["identity"])
                self.assertNotIn("assets", out)
                self.assertNotIn("policy", out)

    def test_one_line(self):
        for board in self.cat.boards():
            text = catalogue.on_show_text(self.cat, board)
            self.assertEqual(text.count("\n"), 1)
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(json.loads(text), self.cat.on_show(board))

    def test_echods_copies_are_the_catalogues(self):
        # Regenerate with: python3 tools/jarvis_crown/catalogue.py write-on-show
        self.assertEqual(sorted(p.name for p in ECHOD_BOARDS.iterdir()),
                         sorted(f"{b}.json" for b in self.cat.boards()))
        for board in self.cat.boards():
            with self.subTest(board=board):
                self.assertEqual((ECHOD_BOARDS / f"{board}.json").read_text(),
                                 catalogue.on_show_text(self.cat, board))

    def test_android_product_is_what_a_running_unit_reports(self):
        # androidboot.product off one running unit per board (ledger C5, K5, R7).
        dump = (ROOT / "docs" / "dumps" / "board-identity-running-2026-10-19.txt").read_text()
        seen = dict(re.findall(r"^== (\w+)\n(?:.*\n)*?androidboot\.product=(\S+)$", dump, re.M))
        self.assertEqual(sorted(seen), self.cat.boards())
        for board, product in seen.items():
            self.assertEqual(self.cat.board_for("android_product", product), board)

    def test_android_product_matches_exactly(self):
        for value in ("Crown", "crown ", "cro", "mt8163", ""):
            with self.subTest(value=value), self.assertRaises(CatalogueError):
                self.cat.board_for("android_product", value)

    def test_unknown_board(self):
        with self.assertRaises(CatalogueError):
            self.cat.on_show("generic")


class InstallerWritesItTest(unittest.TestCase):
    def setUp(self):
        self.show = load_installer()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "board.json"

    def run_cmd(self, board):
        return subprocess.run(["sh", "-c", self.show.board_json_cmd(board, str(self.path))],
                              capture_output=True, text=True, check=False)

    def test_writes_the_catalogues(self):
        for board in catalogue.supported_boards():
            with self.subTest(board=board):
                r = self.run_cmd(board)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("BOARD-OK", r.stdout)
                self.assertEqual(self.path.read_text(), catalogue.on_show_text(catalogue.default(), board))
                self.assertFalse(self.path.with_name("board.json.new").exists())

    def test_replaces_an_old_one(self):
        self.path.write_text('{"id":"crown"}\n')
        self.run_cmd("cronos")
        self.assertEqual(json.loads(self.path.read_text())["id"], "cronos")

    def test_its_own_command_next_to_the_profile(self):
        self.assertEqual(self.show.BOARD_PATH, "/data/misc/techo5/board.json")
        src = (ROOT / "tools" / "install-show.py").read_text()
        self.assertIn("console.run(board_json_cmd(dev), 15)", src)
        self.assertLess(len(self.show.board_json_cmd("checkers")), 400)

    def test_unknown_board_never_reaches_the_console(self):
        with self.assertRaises(CatalogueError):
            self.show.board_json_cmd("generic")


if __name__ == "__main__":
    unittest.main()
