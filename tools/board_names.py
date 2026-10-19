#!/usr/bin/env python3
"""Count where board names are spelled out outside the board catalogue (GENBU-163, stage e).

The catalogue is meant to be the one place that knows the boards. Every other place that names
crown, checkers or cronos is either one the catalogue hasn't reached yet or one that should ask it.
This counts them per tracked file, in its path and its text, and compares the count with a baseline
(tests/fixtures/board-names-baseline.json) that may only go down: a new file that names a board, or
one that names them more often, fails the test. So does one that names them less often, until the
baseline is lowered with --update, so the gain stays.

Not counted: the catalogue itself, the boards echod carries (made from it), docs, audit records and
Markdown, tests (they are about the boards), this file, and the icon font's list, where "crown" is an
icon. The installer's package name jarvis_crown is not a board either. Binary files are skipped.

  python3 tools/board_names.py            show what differs from the baseline, exit 1 if anything does
  python3 tools/board_names.py --update   write the counts as the new baseline
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "board-names-baseline.json"
# jarvis_crown is the installer's package, not a board.
NAME = re.compile(r"(?<![A-Za-z])(?<!jarvis_)(?:crown|checkers|cronos)(?![a-z])")
SKIP = re.compile(r"^(?:catalogue/|docs/|audit/|echod/internal/board/boards/|echod/internal/lib/mdi/"
                  r"|tools/board_names\.py$)"
                  r"|(?:^|/)tests/|_test\.go$|\.md$")


def tracked(root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def count(rel: str, data: bytes) -> int:
    """How often rel names a board: in its path, and in its text unless it is binary."""
    n = len(NAME.findall(rel))
    if b"\0" not in data:
        n += len(NAME.findall(data.decode("utf-8", "replace")))
    return n


def counts(root: Path = ROOT) -> dict[str, int]:
    out = {}
    for rel in tracked(root):
        if SKIP.search(rel):
            continue
        p = root / rel
        if not p.is_file():  # deleted but not yet committed
            continue
        n = count(rel, p.read_bytes())
        if n:
            out[rel] = n
    return dict(sorted(out.items()))


def compare(now: dict[str, int], base: dict[str, int]) -> tuple[list[str], list[str]]:
    """What went up (new files included), and what went down (files gone included)."""
    up = [f"{f}: {base.get(f, 0)} -> {n}" for f, n in now.items() if n > base.get(f, 0)]
    down = [f"{f}: {n} -> {now.get(f, 0)}" for f, n in base.items() if now.get(f, 0) < n]
    return up, down


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", action="store_true", help="write the counts as the new baseline")
    args = ap.parse_args()
    now = counts()
    if args.update:
        BASELINE.write_text(json.dumps(now, indent=1) + "\n")
        print(f"{len(now)} files, {sum(now.values())} names")
        return 0
    up, down = compare(now, json.loads(BASELINE.read_text()))
    for line in up:
        print("more board names:", line)
    for line in down:
        print("fewer board names (lower the baseline with --update):", line)
    return 1 if up or down else 0


if __name__ == "__main__":
    sys.exit(main())
