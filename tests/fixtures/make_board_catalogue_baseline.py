#!/usr/bin/env python3
"""Write tests/fixtures/board-catalogue-baseline.json: what the installer's board lookups and pin tables give.

The first versions of this file read tools/jarvis_crown/boards.py and the pin constants in install.py,
recovery.py, unlock.py and assets.py, which owned those facts until GENBU-163 stage b moved them onto
the catalogue. They were run at 7d601d0, and again at d448873e when dev was merged in, and the parity
tests in tests/test_board_catalogue.py proved the catalogue-backed code gives the same answers, error
messages included. That proof is in the history (f57e414f and before).

Since GENBU-190 this reads today's owners, so the file can follow a change made on purpose (Q8, a new
pin, a new board). Nothing that changes the catalogue may change this file by accident: the parity
tests fail first. When the change is meant, run

    python3 tests/fixtures/make_board_catalogue_baseline.py > tests/fixtures/board-catalogue-baseline.json

read the diff, and commit it in the same commit as the change, saying why each changed value changed.
test_generator_writes_the_fixture checks the file is exactly what this writes, so it can't be edited by
hand. The two confirmation phrases left the board profile in stage b; they are written into each
profile from the step constants that own them now, so the file keeps its shape.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import assets, catalogue, device_gate, install, recovery, unlock  # noqa: E402

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


def profile(board: str) -> dict:
    """A board's profile in the file's shape: the BoardProfile fields, then the two phrases."""
    return {**dataclasses.asdict(catalogue.profile_for_board(board)),
            "unlock_confirmation": unlock.CONFIRM_PHRASE,
            "install_confirmation": install.FINAL_CONFIRM_PHRASE}


def lookup(fn, value):
    """outcome() for a profile lookup, with the phrases added the way profile() adds them."""
    out = outcome(fn, value)
    if "ok" in out:
        out["ok"] = profile(out["ok"]["board"])
    return out


def baseline() -> dict:
    boards = catalogue.supported_boards()
    return {
        "_note": "Written by tests/fixtures/make_board_catalogue_baseline.py (GENBU-190). Do not edit by hand.",
        "profiles": {name: profile(name) for name in boards},
        "product_to_board": {catalogue.profile_for_board(name).fastboot_product: name for name in boards},
        "known_unsupported_products": device_gate.KNOWN_UNSUPPORTED_PRODUCTS,
        "supported_summary": device_gate.SUPPORTED_SUMMARY,
        "unsupported_product_message": {p: device_gate.unsupported_product_message(p) for p in PRODUCT_INPUTS},
        "profile_for_board": {b: lookup(catalogue.profile_for_board, b) for b in BOARD_INPUTS},
        "profile_for_product": {p: lookup(catalogue.profile_for_product, p) for p in PRODUCT_INPUTS},
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
            "amonet_bundle_dir": {b: str(assets.amonet_bundle_dir(Path("CACHE"), b)) for b in boards},
        },
        "board_choices": {
            "tools/jarvis-show.py": choices(ROOT / "tools" / "jarvis-show.py", "--board"),
            "tools/fetch-show-assets.py": choices(ROOT / "tools" / "fetch-show-assets.py", "--board"),
        },
    }


def render() -> str:
    return json.dumps(baseline(), indent=2, sort_keys=False) + "\n"


def main() -> None:
    sys.stdout.write(render())


@dataclasses.dataclass
class _Wrap:
    """Lets outcome() freeze assets_for's tuple of Asset the same way it freezes a profile."""
    assets: tuple

    def __post_init__(self):
        self.assets = [dataclasses.asdict(a) for a in self.assets]


if __name__ == "__main__":
    main()
