"""The Jarvis Show board lists that live outside the installer agree with it (GENBU-177).

dashcast and echod are their own Go modules and can't import the installer's board profiles, so each
keeps a short list. This reads those lists out of the Go source and checks that every one names the
same boards as the installer, and that dashcast gives each board the installer's screen size. A board
added in one place and forgotten in another fails here instead of on a Show: dashcast used to drop a
cronos connection because its size table only knew crown and checkers.
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.catalogue import profile_for_board, supported_boards  # noqa: E402
import techo5lib  # noqa: E402


def go_block(path: pathlib.Path, start: str) -> str:
    """The text of a Go composite literal that begins with `start`, up to its closing brace."""
    text = path.read_text(encoding="utf-8")
    at = text.index(start)
    depth = 0
    for i in range(at + len(start) - 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[at:i + 1]
    raise AssertionError(f"{start!r} in {path} has no closing brace")


def dashcast_boards() -> dict[str, tuple[int, int]]:
    block = go_block(ROOT / "dashcast" / "serve.go", "var showBoards = map[string]struct{ w, h int }{")
    return {name: (int(w), int(h)) for name, w, h in re.findall(r'"([a-z0-9-]+)":\s*\{(\d+),\s*(\d+)\}', block)}


def echod_update_boards() -> set[str]:
    block = go_block(ROOT / "echod" / "internal" / "update" / "manifest.go", "var supportedBoards = map[string]bool{")
    return set(re.findall(r'"([a-z0-9-]+)":\s*true', block))


class ShowBoardListsTest(unittest.TestCase):
    def setUp(self):
        self.boards = set(supported_boards())
        self.assertIn("cronos", self.boards)

    def test_dashcast_serves_every_installer_board_at_its_size(self):
        served = dashcast_boards()
        self.assertEqual(set(served), self.boards)
        for board, size in served.items():
            profile = profile_for_board(board)
            self.assertEqual(size, (profile.display_width, profile.display_height), board)

    def test_echod_updates_every_installer_board(self):
        self.assertEqual(echod_update_boards(), self.boards)

    def test_install_show_lists_the_same_boards(self):
        self.assertEqual(set(techo5lib.JARVIS_SHOW_BOARDS), self.boards)

    def test_dashcast_start_check_uses_the_size_table(self):
        main = (ROOT / "dashcast" / "main.go").read_text(encoding="utf-8")
        self.assertIn("showBoards[board]", main)
        self.assertNotRegex(main, r'board != "(crown|checkers|cronos)"')


if __name__ == "__main__":
    unittest.main()
