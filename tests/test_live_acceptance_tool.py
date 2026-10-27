from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/live-acceptance.py"
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.catalogue import default  # noqa: E402
from techo5lib import JARVIS_SHOW_PRODUCT  # noqa: E402

BOARDS = default().product(JARVIS_SHOW_PRODUCT)["boards"]
J38_FLAGS = (
    "--no-black-bar", "--no-header-sidebar", "--edge-touch-ok", "--native-geometry",
    "--card-geometry-unchanged", "--cold-reload-clean",
)


class LiveAcceptanceToolTests(unittest.TestCase):
    def run_tool(self, *args, expected=0):
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
        self.assertEqual(proc.returncode, expected, proc.stdout)
        return proc.stdout

    def test_verify_fails_closed_without_live_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            out = self.run_tool("verify", "--state", str(state), expected=1)
            for board in BOARDS:
                self.assertIn(f"FAIL: J38 {board}", out)
            self.assertIn("FAIL: J39", out)

    def test_j38_requires_every_explicit_operator_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            shot = Path(td) / "crown.png"
            shot.write_bytes(b"physical screenshot")
            out = self.run_tool(
                "j38", "--state", str(state), "--board", "crown", "--evidence", str(shot),
                "--no-black-bar",
                expected=1,
            )
            self.assertIn("refusing PASS without explicit confirmations", out)
            self.assertFalse(state.exists())

    def test_j38_and_j39_record_hashes_not_evidence_contents(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            shots = {}
            for board in BOARDS:
                shots[board] = Path(td) / f"{board}.png"
                shots[board].write_bytes(f"{board}-screen-secret-marker".encode())
            general = Path(td) / "general.txt"
            recipe = Path(td) / "recipe.txt"
            general.write_text("general-response-secret-marker", encoding="utf-8")
            recipe.write_text("recipe-response-secret-marker", encoding="utf-8")

            for board in BOARDS:
                self.run_tool("j38", "--state", str(state), "--board", board, "--evidence", str(shots[board]),
                              *J38_FLAGS)
            self.run_tool(
                "j39", "--state", str(state),
                "--general-evidence", str(general), "--recipe-evidence", str(recipe),
                "--ha-owned", "--general-answer-ok", "--recipe-web-grounded", "--no-device-misroute",
            )

            raw = state.read_text(encoding="utf-8")
            self.assertNotIn("secret-marker", raw)
            data = json.loads(raw)
            self.assertEqual(len(data["j38"]["crown"]["evidence"]["sha256"]), 64)
            self.assertEqual(len(data["j39"]["general_evidence"]["sha256"]), 64)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            out = self.run_tool("verify", "--state", str(state))
            for board in BOARDS:
                self.assertIn(f"PASS: J38 {board}", out)
            self.assertIn("PASS: J39", out)

    def test_every_board_the_product_lists_needs_display_evidence(self):
        """GENBU-176: a later board such as cronos is no less a release board than the first ones."""
        self.assertIn("cronos", BOARDS)
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            for board in BOARDS[:-1]:
                shot = Path(td) / f"{board}.png"
                shot.write_bytes(b"screen")
                self.run_tool("j38", "--state", str(state), "--board", board, "--evidence", str(shot), *J38_FLAGS)
            self.run_tool(
                "j39", "--state", str(state),
                "--general-evidence", str(shot), "--recipe-evidence", str(shot),
                "--ha-owned", "--general-answer-ok", "--recipe-web-grounded", "--no-device-misroute",
            )
            out = self.run_tool("verify", "--state", str(state), expected=1)
            self.assertIn(f"FAIL: J38 {BOARDS[-1]}", out)

    def test_missing_evidence_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            out = self.run_tool(
                "j38", "--state", str(state), "--board", "crown", "--evidence", str(Path(td) / "missing.png"),
                "--no-black-bar", "--no-header-sidebar", "--edge-touch-ok", "--native-geometry",
                "--card-geometry-unchanged", "--cold-reload-clean",
                expected=1,
            )
            self.assertIn("evidence file not found", out)


if __name__ == "__main__":
    unittest.main()
