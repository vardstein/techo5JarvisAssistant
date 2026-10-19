"""GENBU-163 stage e: board names outside the catalogue may only get fewer (tools/board_names.py)."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("board_names", ROOT / "tools" / "board_names.py")
names = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = names
SPEC.loader.exec_module(names)


class BaselineTest(unittest.TestCase):
    def test_no_more_board_names_than_the_baseline(self):
        try:
            now = names.counts()
        except (OSError, subprocess.CalledProcessError) as e:
            self.skipTest(f"not a git checkout: {e}")
        up, down = names.compare(now, json.loads(names.BASELINE.read_text()))
        self.assertEqual(up, [], "a board is named outside the catalogue; ask the catalogue instead")
        self.assertEqual(down, [], "fewer board names: run python3 tools/board_names.py --update")


class CountTest(unittest.TestCase):
    def test_what_counts(self):
        self.assertEqual(names.count("tools/x.py", b"if board == 'crown' or b == \"checkers\":"), 2)
        self.assertEqual(names.count("echod/paths_cronos.go", b"package x"), 1)
        self.assertEqual(names.count("x.sh", b"crown_ CHECKERS mt8163-crown cronos.json lcm_cronos_st"), 4)

    def test_what_does_not(self):
        # Inside another word, or a Go name like boardCrown, is no board name.
        for text in (b"crowned", b"checkerboard", b"boardCrown", b"Crown", b"chronos", b"tools/jarvis_crown/x"):
            self.assertEqual(names.count("x.go", text), 0, text)
        self.assertEqual(names.count("x.bin", b"crown\0checkers"), 0)
        self.assertEqual(names.count("tools/jarvis_crown/unlock.py", b""), 0)

    def test_skipped(self):
        for rel in ("catalogue/boards/crown.json", "docs/x.txt", "tests/test_x.py", "echod/a_test.go",
                    "ha/tests/test_x.py", "audit/j1.txt", "README.md", "echod/internal/board/boards/crown.json", "echod/internal/lib/mdi/names.txt"):
            self.assertTrue(names.SKIP.search(rel), rel)
        for rel in ("tools/install-show.py", "echod/internal/layout/device_cronos.go", "NOTICE"):
            self.assertFalse(names.SKIP.search(rel), rel)

    def test_compare(self):
        base = {"a": 2, "b": 1}
        self.assertEqual(names.compare(base, base), ([], []))
        self.assertEqual(names.compare({"a": 3, "b": 1}, base), (["a: 2 -> 3"], []))
        self.assertEqual(names.compare({"a": 2, "b": 1, "c": 1}, base), (["c: 0 -> 1"], []))
        self.assertEqual(names.compare({"a": 2}, base), ([], ["b: 1 -> 0"]))

    def test_counts_tracked_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "tools").mkdir()
            (root / "tools" / "a.py").write_text("crown\n")
            (root / "docs").mkdir()
            (root / "docs" / "b.txt").write_text("crown\n")
            (root / "loose.py").write_text("crown\n")
            subprocess.run(["git", "add", "tools/a.py", "docs/b.txt"], cwd=root, check=True)
            self.assertEqual(names.counts(root), {"tools/a.py": 1})


if __name__ == "__main__":
    unittest.main()
