#!/usr/bin/env python3
"""Jarvis Crown v1 one-installer entry point.

Only the non-destructive host preflight is enabled at this checkpoint. Later
jobs add Crown detection, unlock, recovery and install phases behind this gate.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_crown.device_gate import DeviceGateError, identify_crown  # noqa: E402
from jarvis_crown.preflight import preflight_ok, print_checks, run_preflight  # noqa: E402
from jarvis_crown.boards import profile_for_board  # noqa: E402
from jarvis_crown.unlock import CONFIRM_PHRASE, UnlockError, unlock_crown, unlock_warnings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-crown")
    parser.add_argument("command", choices=["preflight", "identify", "unlock"], nargs="?", default="preflight")
    parser.add_argument("--amonet-dir", type=Path, help="local amonet-crown-v2.0.1 package directory")
    parser.add_argument("--lineage-zip", type=Path, help="optional Crown LineageOS ZIP to validate host-side")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    project = root.parent
    checks = run_preflight(
        repo_root=root,
        amonet_dir=(args.amonet_dir or (project / "third_party" / "amonet-crown-v2.0.1")).resolve(),
        lineage_zip=args.lineage_zip.resolve() if args.lineage_zip else None,
        work_dir=(args.work_dir or (project / "work")).resolve(),
        backup_dir=(args.backup_dir or (project / "backups")).resolve(),
    )
    print_checks(checks)
    if not preflight_ok(checks):
        print("FAIL: host preflight failed; no device was queried or modified", file=sys.stderr)
        return 1

    if args.command == "preflight":
        print("PASS: host preflight complete; no device was queried or modified")
        return 0

    print("PASS: host preflight complete; beginning read-only fastboot identity gate")
    try:
        identity = identify_crown()
    except DeviceGateError as exc:
        print(f"FAIL: Crown identity gate: {exc}", file=sys.stderr)
        print("PASS: no write-capable fastboot/Amonet command was executed", file=sys.stderr)
        return 2

    print(f"PASS: product={identity.product}")
    print(f"PASS: fastboot serial={identity.serial}")
    print(f"PASS: unlock_status={'true' if identity.unlocked else 'false'}")
    if identity.lk_build_desc:
        print(f"INFO: lk_build_desc={identity.lk_build_desc}")
    else:
        print("WARN: lk_build_desc unavailable; default Crown Amonet payload selection only")
    print("PASS: live device gate complete; device was queried read-only and not modified")

    if args.command == "identify":
        return 0

    if identity.unlocked:
        print("PASS: Crown bootloader already unlocked; Amonet will not be executed")
        return 0

    print()
    for line in unlock_warnings(profile_for_board("crown")):
        print(f"WARN: {line}")
    confirmation = input(f"Type {CONFIRM_PHRASE} to continue: ").strip()
    try:
        result = unlock_crown(
            identity,
            (args.amonet_dir or (project / "third_party" / "amonet-crown-v2.0.1")).resolve(),
            confirmation=confirmation,
        )
    except UnlockError as exc:
        print(f"FAIL: Crown unlock: {exc}", file=sys.stderr)
        return 3
    if result.identity.unlocked:
        print("PASS: bootloader unlocked; the same Show came up in TWRP")
        print("PASS: J19 complete; no TWRP/recovery/partition installation was performed")
        return 0
    print("FAIL: unlock result was not proven", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
