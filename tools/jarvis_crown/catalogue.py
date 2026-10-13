#!/usr/bin/env python3
"""Genbu Show board catalogue: load, check, resolve, explain.

The catalogue is catalogue/boards/<board>.json and catalogue/assets/<asset>.json. A board names its
assets by id and never copies them. There is no inheritance and there are no defaults: every key a
board or asset may have is listed below, an unknown key or value is an error, and a value that is
missing stays missing. Lookups are exact. Anything that does not match exactly one board is refused.

This module is the only owner of the entity shape. Nothing else reads catalogue/ directly.

    python3 tools/jarvis_crown/catalogue.py check
    python3 tools/jarvis_crown/catalogue.py resolve crown
    python3 tools/jarvis_crown/catalogue.py explain crown assets.boot.sha256
    python3 tools/jarvis_crown/catalogue.py check-files DIR...   # hash local copies against the pins
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import zipfile

ROOT = Path(__file__).resolve().parents[2] / "catalogue"

# Closed sets. Adding a value is a reviewed change to this file, made when a real board needs it.
UNLOCK_METHODS = frozenset({"amonet-kaeru"})
ASSET_KINDS = frozenset({"lineage", "boot", "amonet"})
# Exact-match identity keys, one per stage the installer sees the unit in. A key nobody has
# captured from a real unit is left out of the board, not filled with a placeholder.
IDENTITY_KEYS = frozenset({"fastboot_product", "twrp_device"})

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_BOARD_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEMBER = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


class CatalogueError(ValueError):
    """The catalogue is malformed, or a lookup did not match exactly one entity."""


def _keys(where: str, obj: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(obj, dict):
        raise CatalogueError(f"{where}: expected an object")
    missing = required - obj.keys()
    unknown = obj.keys() - required - optional
    if missing:
        raise CatalogueError(f"{where}: missing {sorted(missing)}")
    if unknown:
        raise CatalogueError(f"{where}: unknown {sorted(unknown)}")


def _str(where: str, value: Any, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CatalogueError(f"{where}: expected a non-empty string without outer spaces")
    if pattern is not None and not pattern.match(value):
        raise CatalogueError(f"{where}: {value!r} has the wrong form")
    return value


def _int(where: str, value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise CatalogueError(f"{where}: expected a positive integer")
    return value


def _check_asset(where: str, a: Any) -> None:
    _keys(where, a, {"id", "kind", "file", "url", "source", "manual"},
          {"sha256", "size", "kaeru", "members"})
    _str(f"{where}#/id", a["id"], _ID)
    if a["kind"] not in ASSET_KINDS:
        raise CatalogueError(f"{where}#/kind: {a['kind']!r} not in {sorted(ASSET_KINDS)}")
    _str(f"{where}#/file", a["file"], _MEMBER)
    if not _str(f"{where}#/url", a["url"]).startswith("https://"):
        raise CatalogueError(f"{where}#/url: only https sources")
    _str(f"{where}#/source", a["source"])
    if type(a["manual"]) is not bool:
        raise CatalogueError(f"{where}#/manual: expected true or false")
    # A pin is the sha256 and the size together. Without them the asset is known but never used.
    if ("sha256" in a) != ("size" in a):
        raise CatalogueError(f"{where}: sha256 and size come together")
    if "sha256" in a:
        _str(f"{where}#/sha256", a["sha256"], _SHA256)
        _int(f"{where}#/size", a["size"])
    if a["kind"] == "amonet":
        _str(f"{where}#/kaeru", a.get("kaeru"), _MEMBER)
        if "members" in a:
            if "sha256" not in a:
                raise CatalogueError(f"{where}#/members: member pins need the zip itself pinned")
            if not isinstance(a["members"], dict) or not a["members"]:
                raise CatalogueError(f"{where}#/members: expected a non-empty path -> sha256 table")
            for path, digest in a["members"].items():
                _str(f"{where}#/members key", path, _MEMBER)
                _str(f"{where}#/members/{path}", digest, _SHA256)
    elif "kaeru" in a or "members" in a:
        raise CatalogueError(f"{where}: kaeru and members belong to amonet zips only")
    if not a["file"].startswith(a["id"]) or a["file"][len(a["id"]):] not in (".zip", ".img"):
        raise CatalogueError(f"{where}: file must be <id>.zip or <id>.img")


def _check_board(where: str, b: Any, assets: dict[str, dict]) -> None:
    _keys(where, b, {"id", "model", "profile_id", "identity", "display", "policy", "assets"})
    _str(f"{where}#/id", b["id"], _BOARD_ID)
    _str(f"{where}#/model", b["model"])
    _str(f"{where}#/profile_id", b["profile_id"], _BOARD_ID)
    identity = b["identity"]
    _keys(f"{where}#/identity", identity, set(), set(IDENTITY_KEYS))
    if not identity:
        raise CatalogueError(f"{where}#/identity: a board needs at least one identity key")
    for key, value in identity.items():
        _str(f"{where}#/identity/{key}", value)
    _keys(f"{where}#/display", b["display"], {"width", "height"})
    for key in ("width", "height"):
        _int(f"{where}#/display/{key}", b["display"][key])
    _keys(f"{where}#/policy", b["policy"], {"install"})
    _keys(f"{where}#/policy/install", b["policy"]["install"], {"unlock"})
    if b["policy"]["install"]["unlock"] not in UNLOCK_METHODS:
        raise CatalogueError(f"{where}#/policy/install/unlock: not in {sorted(UNLOCK_METHODS)}")
    _keys(f"{where}#/assets", b["assets"], set(ASSET_KINDS))
    for kind, ref in b["assets"].items():
        if ref not in assets:
            raise CatalogueError(f"{where}#/assets/{kind}: no asset {ref!r}")
        if assets[ref]["kind"] != kind:
            raise CatalogueError(f"{where}#/assets/{kind}: {ref!r} is a {assets[ref]['kind']} asset")


def _load_dir(path: Path) -> dict[str, tuple[Path, dict]]:
    out: dict[str, tuple[Path, dict]] = {}
    for file in sorted(path.glob("*")):
        if file.suffix != ".json" or not file.is_file():
            raise CatalogueError(f"{file}: only .json entity files belong here")
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CatalogueError(f"{file}: {exc}") from None
        if not isinstance(data, dict) or data.get("id") != file.stem:
            raise CatalogueError(f"{file}: id must equal the file name")
        out[file.stem] = (file, data)
    return out


class Catalogue:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self._assets = _load_dir(root / "assets")
        self._boards = _load_dir(root / "boards")
        for name, (file, data) in self._assets.items():
            _check_asset(self._rel(file), data)
        assets = {k: v for k, (_, v) in self._assets.items()}
        for name, (file, data) in self._boards.items():
            _check_board(self._rel(file), data, assets)
        used = {ref for _, b in self._boards.values() for ref in b["assets"].values()}
        unused = sorted(set(assets) - used)
        if unused:
            raise CatalogueError(f"assets no board uses: {unused}")
        for key in IDENTITY_KEYS:
            seen: dict[str, str] = {}
            for name, (_, b) in self._boards.items():
                value = b["identity"].get(key)
                if value is None:
                    continue
                if value in seen:
                    raise CatalogueError(f"identity {key}={value!r} names both {seen[value]} and {name}")
                seen[value] = name

    def _rel(self, file: Path) -> str:
        return str(file.relative_to(self.root.parent))

    def boards(self) -> list[str]:
        return sorted(self._boards)

    def board_for(self, key: str, value: str) -> str:
        """The one board whose identity key equals value exactly, else refuse."""
        if key not in IDENTITY_KEYS:
            raise CatalogueError(f"{key!r} is not an identity key")
        hits = [n for n, (_, b) in self._boards.items() if b["identity"].get(key) == value]
        if len(hits) != 1:
            raise CatalogueError(f"no catalogue board has {key}={value!r}; refusing")
        return hits[0]

    def resolve(self, board: str) -> dict:
        """Everything the installer needs for one board, with each asset filled in from its owner."""
        if board not in self._boards:
            raise CatalogueError(f"no catalogue board {board!r}; refusing")
        _, b = self._boards[board]
        out = json.loads(json.dumps(b))
        out["assets"] = {kind: json.loads(json.dumps(self._assets[ref][1])) for kind, ref in b["assets"].items()}
        return out

    def explain(self, board: str, path: str) -> tuple[Any, str]:
        """The resolved value at a dotted path and the file + JSON pointer that owns it."""
        value: Any = self.resolve(board)
        file, _ = self._boards[board]
        pointer = ""
        parts = path.split(".")
        for i, part in enumerate(parts):
            if not isinstance(value, dict) or part not in value:
                raise CatalogueError(f"{board}: {path} is not set in the catalogue")
            if i == 1 and parts[0] == "assets":
                file, _ = self._assets[self._boards[board][1]["assets"][part]]
                pointer = ""
            else:
                pointer += "/" + part
            value = value[part]
        return value, f"{self._rel(file)}#{pointer or '/'}"


def check_files(cat: Catalogue, dirs: list[Path]) -> list[str]:
    """Hash every pinned asset (and pinned zip member) found in dirs. Read-only; reports, never fixes."""
    lines = []
    for name in sorted(cat._assets):
        a = cat._assets[name][1]
        found = [d / a["file"] for d in dirs if (d / a["file"]).is_file()]
        if not found:
            lines.append(f"{name}: no local copy")
            continue
        if "sha256" not in a:
            lines.append(f"{name}: local copy, but the catalogue pins nothing to compare")
            continue
        digest = hashlib.sha256()
        with found[0].open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        ok = digest.hexdigest() == a["sha256"] and found[0].stat().st_size == a["size"]
        lines.append(f"{name}: {'match' if ok else 'MISMATCH'}")
        if ok and a.get("members"):
            with zipfile.ZipFile(found[0]) as z:
                names = set(z.namelist())
                for member, want in sorted(a["members"].items()):
                    got = hashlib.sha256(z.read(member)).hexdigest() if member in names else None
                    lines.append(f"  {member}: {'match' if got == want else 'MISSING' if got is None else 'MISMATCH'}")
            kaeru = a["kaeru"]
            lines.append(f"  {kaeru}: {'present' if kaeru in names else 'MISSING'} (kaeru, not pinned)")
    return lines


def main(argv: list[str]) -> int:
    try:
        cat = Catalogue()
        if argv == ["check"]:
            print(f"ok: {len(cat.boards())} boards, {len(cat._assets)} assets")
        elif len(argv) >= 2 and argv[0] == "check-files":
            lines = check_files(cat, [Path(d).expanduser() for d in argv[1:]])
            print("\n".join(lines))
            return 1 if any("MISMATCH" in l or "MISSING" in l for l in lines) else 0
        elif len(argv) == 2 and argv[0] == "resolve":
            print(json.dumps(cat.resolve(argv[1]), indent=2))
        elif len(argv) == 3 and argv[0] == "explain":
            value, owner = cat.explain(argv[1], argv[2])
            print(f"{json.dumps(value)}  <- {owner}")
        else:
            print("usage: check | resolve <board> | explain <board> <path> | check-files <dir>...",
                  file=sys.stderr)
            return 2
    except CatalogueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
