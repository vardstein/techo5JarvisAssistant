#!/usr/bin/env python3
"""Freeze what the installer's board owners gave before GENBU-163 stage b moved them onto the catalogue.

This was run once, at commit 7d601d0, while tools/jarvis_crown/boards.py and the pin constants in
install.py, recovery.py, unlock.py and assets.py were still the owners:

    python3 tests/fixtures/make_board_catalogue_baseline.py > tests/fixtures/board-catalogue-baseline.json

It cannot run on later commits, because boards.py is gone there. To redo it, check out 7d601d0 in a
spare worktree and run it there with that worktree as the current folder. tests/test_board_catalogue.py
compares the catalogue-backed code with this file, so the move is proven to change nothing.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib.util
import io
import json
from pathlib import Path
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import assets, boards, device_gate, install, recovery, unlock  # noqa: E402

# Inputs the lookups were fed. Case and space variants are here on purpose: today's code folds them.
BOARD_INPUTS = ["crown", "checkers", "cronos", "Crown", " CHECKERS ", "CRONOS", "", "laguna", "echo-show-8-gen2"]
PRODUCT_INPUTS = ["CROWN", "CHECKERS", "CRONOS", "crown", " checkers ", "Cronos", "", "LAGUNA", "CHECKERS2"]


def outcome(fn, value):
    try:
        return {"ok": dataclasses.asdict(fn(value))}
    except Exception as exc:  # noqa: BLE001 - the type and text are what we freeze
        return {"error": type(exc).__name__, "message": str(exc)}


def choices(path: Path, option: str) -> list[str]:
    """The literal --board choices of a script, read with argparse rather than by eye."""
    seen: list[str] = []
    real_add = argparse.ArgumentParser.add_argument

    def spy(self, *names, **kwargs):
        if option in names:
            seen.extend(kwargs["choices"])
        return real_add(self, *names, **kwargs)

    argparse.ArgumentParser.add_argument = spy
    try:
        spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_") + "_baseline", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old_argv = sys.argv
        sys.argv = [path.name, "--help"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                (module.parse_args if hasattr(module, "parse_args") else module.main)()
        except SystemExit:
            pass
        finally:
            sys.argv = old_argv
    finally:
        argparse.ArgumentParser.add_argument = real_add
    return seen


def main() -> None:
    out = {
        "_note": "Frozen at 7d601d0 by tests/fixtures/make_board_catalogue_baseline.py. Do not edit by hand.",
        "profiles": {name: dataclasses.asdict(p) for name, p in boards.PROFILES.items()},
        "product_to_board": boards.PRODUCT_TO_BOARD,
        "known_unsupported_products": boards.KNOWN_UNSUPPORTED_PRODUCTS,
        "supported_summary": boards.SUPPORTED_SUMMARY,
        "unsupported_product_message": {p: boards.unsupported_product_message(p) for p in PRODUCT_INPUTS},
        "profile_for_board": {b: outcome(boards.profile_for_board, b) for b in BOARD_INPUTS},
        "profile_for_product": {p: outcome(boards.profile_for_product, p) for p in PRODUCT_INPUTS},
        "device_gate_supported_products": sorted(device_gate.SUPPORTED_PRODUCTS),
        "install": {
            "BOOT_SHA256_BY_BOARD": install.BOOT_SHA256_BY_BOARD,
            "CROWN_BOOT_SHA256": install.CROWN_BOOT_SHA256,
            "CHECKERS_BOOT_SHA256": install.CHECKERS_BOOT_SHA256,
            "CRONOS_BOOT_SHA256": install.CRONOS_BOOT_SHA256,
            "SUPPORTED_BOARDS": list(install.SUPPORTED_BOARDS),
            "PROFILE_ID": install.PROFILE_ID,
            "FINAL_CONFIRM_PHRASE": install.FINAL_CONFIRM_PHRASE,
        },
        "recovery": {
            "TWRP_SHA256": recovery.TWRP_SHA256,
            "TWRP_SHA256_BY_BOARD": recovery.TWRP_SHA256_BY_BOARD,
        },
        "unlock": {
            "CONFIRM_PHRASE": unlock.CONFIRM_PHRASE,
            "AMONET_UNLOCK_SHA256": unlock.AMONET_UNLOCK_SHA256,
        },
        "assets": {
            "ASSETS": {b: [dataclasses.asdict(a) for a in t] for b, t in assets.ASSETS.items()},
            "assets_for": {b: outcome(lambda x: _Wrap(assets.assets_for(x)), b)
                           for b in BOARD_INPUTS},
            "amonet_bundle_dir": {b: str(assets.amonet_bundle_dir(Path("CACHE"), b)) for b in boards.PROFILES},
        },
        "board_choices": {
            "tools/jarvis-show.py": choices(ROOT / "tools" / "jarvis-show.py", "--board"),
            "tools/fetch-show-assets.py": choices(ROOT / "tools" / "fetch-show-assets.py", "--board"),
        },
    }
    json.dump(out, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


@dataclasses.dataclass
class _Wrap:
    """Lets outcome() freeze assets_for's tuple of Asset the same way it freezes a profile."""
    assets: tuple

    def __post_init__(self):
        self.assets = [dataclasses.asdict(a) for a in self.assets]


if __name__ == "__main__":
    main()
