#!/usr/bin/env python3
"""Record and verify Jarvis Show live acceptance evidence for J38/J39.

This helper deliberately does not inspect screenshots or decide whether a spoken answer is good.
Those are live, operator-observed release gates. It records explicit confirmations plus SHA-256
fingerprints of the evidence files so a later release review can prove which artifacts were accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_crown.catalogue import load_or_exit  # noqa: E402
from techo5lib import JARVIS_SHOW_PRODUCT  # noqa: E402

# Every board the product lists needs its own display evidence, a later one like cronos as much as the
# first release's (GENBU-176).
BOARDS = tuple(load_or_exit().product(JARVIS_SHOW_PRODUCT)["boards"])

SCHEMA = 1
DEFAULT_STATE = Path(
    os.environ.get(
        "JARVIS_SHOW_LIVE_ACCEPTANCE",
        "~/.local/state/jarvis-show/live-acceptance.json",
    )
).expanduser()

J38_CHECKS = (
    "no_black_bar",
    "no_header_sidebar",
    "edge_touch_ok",
    "native_geometry",
    "card_geometry_unchanged",
    "cold_reload_clean",
)
J39_CHECKS = (
    "ha_owned",
    "general_answer_ok",
    "recipe_web_grounded",
    "no_device_misroute",
)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def evidence_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"FAIL: evidence file not found: {resolved}")
    return {
        "path": str(resolved),
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": SCHEMA, "j38": {}, "j39": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: cannot read acceptance state {path}: {exc}") from exc
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"FAIL: unsupported acceptance state schema in {path}")
    data.setdefault("j38", {})
    data.setdefault("j39", {})
    return data


def save_state(path: Path, data: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def require_checks(args: argparse.Namespace, names: tuple[str, ...]) -> dict[str, bool]:
    checks = {name: bool(getattr(args, name)) for name in names}
    missing = [name.replace("_", "-") for name, value in checks.items() if not value]
    if missing:
        raise SystemExit("FAIL: refusing PASS without explicit confirmations: " + ", ".join(missing))
    return checks


def cmd_j38(args: argparse.Namespace) -> int:
    state_path = Path(args.state).expanduser()
    data = load_state(state_path)
    checks = require_checks(args, J38_CHECKS)
    data["j38"][args.board] = {
        "accepted": True,
        "accepted_at": now_utc(),
        "board": args.board,
        "evidence": evidence_record(Path(args.evidence)),
        "checks": checks,
    }
    save_state(state_path, data)
    print(f"PASS: J38 {args.board} live evidence recorded")
    return 0


def cmd_j39(args: argparse.Namespace) -> int:
    state_path = Path(args.state).expanduser()
    data = load_state(state_path)
    checks = require_checks(args, J39_CHECKS)
    data["j39"] = {
        "accepted": True,
        "accepted_at": now_utc(),
        "general_evidence": evidence_record(Path(args.general_evidence)),
        "recipe_evidence": evidence_record(Path(args.recipe_evidence)),
        "checks": checks,
    }
    save_state(state_path, data)
    print("PASS: J39 deployed HA/Klar live evidence recorded")
    return 0


def gate_status(data: dict[str, Any]) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for board in BOARDS:
        accepted = bool(data.get("j38", {}).get(board, {}).get("accepted"))
        lines.append(f"{'PASS' if accepted else 'FAIL'}: J38 {board} physical display acceptance")
        ok = ok and accepted
    accepted = bool(data.get("j39", {}).get("accepted"))
    lines.append(f"{'PASS' if accepted else 'FAIL'}: J39 deployed HA/Klar acceptance")
    ok = ok and accepted
    return ok, lines


def cmd_status(args: argparse.Namespace) -> int:
    data = load_state(Path(args.state).expanduser())
    ok, lines = gate_status(data)
    for line in lines:
        print(line)
    print(f"STATE: {Path(args.state).expanduser()}")
    return 0 if ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    data = load_state(Path(args.state).expanduser())
    ok, lines = gate_status(data)
    for line in lines:
        print(line)
    if not ok:
        print("FAIL: J38/J39 live release gates are incomplete")
        return 1
    print("PASS: J38/J39 live release gates have explicit evidence")
    return 0


def add_state_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="acceptance state file")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    j38 = sub.add_parser("j38", help="record one board's physical display acceptance")
    add_state_arg(j38)
    j38.add_argument("--board", required=True, choices=BOARDS)
    j38.add_argument("--evidence", required=True, help="screenshot/photo evidence file")
    for name in J38_CHECKS:
        j38.add_argument("--" + name.replace("_", "-"), action="store_true")
    j38.set_defaults(func=cmd_j38)

    j39 = sub.add_parser("j39", help="record deployed HA/Klar voice/web acceptance")
    add_state_arg(j39)
    j39.add_argument("--general-evidence", required=True, help="transcript/log for 'Was ist ein Taco?'")
    j39.add_argument("--recipe-evidence", required=True, help="transcript/log for explicit Taco web/recipe request")
    for name in J39_CHECKS:
        j39.add_argument("--" + name.replace("_", "-"), action="store_true")
    j39.set_defaults(func=cmd_j39)

    status = sub.add_parser("status", help="show current J38/J39 evidence state")
    add_state_arg(status)
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify", help="fail unless every J38 board and J39 are accepted")
    add_state_arg(verify)
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
