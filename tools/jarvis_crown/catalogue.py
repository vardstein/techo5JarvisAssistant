#!/usr/bin/env python3
"""Genbu Show board catalogue: load, check, resolve, explain.

The catalogue is catalogue/products/<product>.json, catalogue/boards/<board>.json and
catalogue/assets/<asset>.json. A product lists its boards by id, and a board names its assets by id;
neither copies what it names. There is no inheritance and there are no defaults: every key a
board or asset may have is listed below, an unknown key or value is an error, and a value that is
missing stays missing. "notes" is free text for people (review history, why a pin is or is not there);
no code reads it. Lookups are exact. Anything that does not match exactly one board is refused.

This module is the only owner of the entity shape. Nothing else reads catalogue/ directly: the installer
asks profile_for_board() and profile_for_product() below, or resolve() for pins.

    python3 tools/jarvis_crown/catalogue.py check
    python3 tools/jarvis_crown/catalogue.py resolve crown
    python3 tools/jarvis_crown/catalogue.py explain crown assets.boot.sha256
    python3 tools/jarvis_crown/catalogue.py check-files DIR...   # hash local copies against the pins
    python3 tools/jarvis_crown/catalogue.py on-show crown        # the Show's board.json
    python3 tools/jarvis_crown/catalogue.py write-on-show        # echod's copies of every board.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
import zipfile

ROOT = Path(__file__).resolve().parents[2] / "catalogue"

# Closed sets. Adding a value is a reviewed change to this file, made when a real board needs it.
UNLOCK_METHODS = frozenset({"amonet-kaeru"})
ASSET_KINDS = frozenset({"lineage", "boot", "amonet"})
# Exact-match identity keys, one per stage the unit is seen in: fastboot, TWRP, and running
# (android_product, androidboot.product on the kernel command line, what echod and board.json
# go by). A key nobody has captured from a real unit is left out of the board, not filled with a
# placeholder.
IDENTITY_KEYS = frozenset({"fastboot_product", "twrp_device", "android_product"})
# What board.json on a Show holds of its board (Catalogue.on_show), and where echod keeps a copy of
# each board's, which a test holds equal to it: echod refuses a key it does not know.
ON_SHOW_KEYS = ("id", "model", "profile_id", "identity", "display")
ON_SHOW_DIR = ROOT.parent / "echod" / "internal" / "board" / "boards"
# The pictures a kaeru wordmark slot is known with besides Amazon's (tools/boot-logo/README.md):
# boot_logo.py writes the OpenJade one, install-show.py the TECHO5 one.
WORDMARK_LOGOS = frozenset({"openjade", "techo5"})

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_BOARD_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX = re.compile(r"^(?:[0-9a-f]{2})+$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MEMBER = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
# Product blocks the catalogue carries but does not own the inside of. Nothing in the installer reads
# them; they are kept here so the product has one file, and checked only for being non-empty objects.
PRODUCT_BLOCKS = frozenset({"assistant", "display", "security", "music"})


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


def _check_wordmark(where: str, w: Any) -> None:
    """kaeru's wordmark slot: where it is, kaeru's checked length, and kaeru's sha256 as the zip ships it
    (stock) and with each logo in it. openjade_large is a bigger OpenJade picture together with the four
    instructions that size its box (tools/boot-logo/README.md)."""
    _keys(where, w, {"offset", "length", "stock", "logos"}, {"openjade_large"})
    _int(f"{where}#/offset", w["offset"])
    _int(f"{where}#/length", w["length"])
    _str(f"{where}#/stock", w["stock"], _SHA256)
    if not isinstance(w["logos"], dict) or not w["logos"]:
        raise CatalogueError(f"{where}#/logos: expected a non-empty logo -> sha256 table")
    for name, digest in w["logos"].items():
        if name not in WORDMARK_LOGOS:
            raise CatalogueError(f"{where}#/logos: {name!r} not in {sorted(WORDMARK_LOGOS)}")
        _str(f"{where}#/logos/{name}", digest, _SHA256)
    if "openjade_large" in w:
        big = w["openjade_large"]
        _keys(f"{where}#/openjade_large", big, {"file", "sha256", "code_at", "code_stock", "code", "kaeru_sha256"})
        _str(f"{where}#/openjade_large/file", big["file"], _ID)
        _str(f"{where}#/openjade_large/sha256", big["sha256"], _SHA256)
        _int(f"{where}#/openjade_large/code_at", big["code_at"])
        _str(f"{where}#/openjade_large/code_stock", big["code_stock"], _HEX)
        _str(f"{where}#/openjade_large/code", big["code"], _HEX)
        if len(big["code"]) != len(big["code_stock"]):
            raise CatalogueError(f"{where}#/openjade_large: code and code_stock differ in length")
        _str(f"{where}#/openjade_large/kaeru_sha256", big["kaeru_sha256"], _SHA256)
        if "openjade" not in w["logos"]:
            raise CatalogueError(f"{where}#/openjade_large: needs logos.openjade too")


def _check_asset(where: str, a: Any) -> None:
    _keys(where, a, {"id", "kind", "file", "url", "source", "manual"},
          {"sha256", "size", "kaeru", "wordmark", "members", "upstream", "notes"})
    _str(f"{where}#/id", a["id"], _ID)
    if a["kind"] not in ASSET_KINDS:
        raise CatalogueError(f"{where}#/kind: {a['kind']!r} not in {sorted(ASSET_KINDS)}")
    _str(f"{where}#/file", a["file"], _MEMBER)
    if not _str(f"{where}#/url", a["url"]).startswith("https://"):
        raise CatalogueError(f"{where}#/url: only https sources")
    _str(f"{where}#/source", a["source"])
    if "notes" in a:
        _str(f"{where}#/notes", a["notes"])
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
        # Where its open-source parts live, so the fetcher can compare a new zip file by file.
        up = a.get("upstream")
        _keys(f"{where}#/upstream", up, {"repo", "branches"})
        _str(f"{where}#/upstream/repo", up["repo"], _REPO)
        if not isinstance(up["branches"], list) or not up["branches"]:
            raise CatalogueError(f"{where}#/upstream/branches: expected a non-empty list of branch names")
        for i, branch in enumerate(up["branches"]):
            _str(f"{where}#/upstream/branches/{i}", branch, _ID)
        if "members" in a:
            if "sha256" not in a:
                raise CatalogueError(f"{where}#/members: member pins need the zip itself pinned")
            if not isinstance(a["members"], dict) or not a["members"]:
                raise CatalogueError(f"{where}#/members: expected a non-empty path -> sha256 table")
            for path, digest in a["members"].items():
                _str(f"{where}#/members key", path, _MEMBER)
                _str(f"{where}#/members/{path}", digest, _SHA256)
        if "wordmark" in a:
            _check_wordmark(f"{where}#/wordmark", a["wordmark"])
    elif {"kaeru", "wordmark", "members", "upstream"} & set(a):
        raise CatalogueError(f"{where}: kaeru, wordmark, members and upstream belong to amonet zips only")
    if not a["file"].startswith(a["id"]) or a["file"][len(a["id"]):] not in (".zip", ".img"):
        raise CatalogueError(f"{where}: file must be <id>.zip or <id>.img")


def _check_board(where: str, b: Any, assets: dict[str, dict]) -> None:
    _keys(where, b, {"id", "model", "profile_id", "identity", "display", "policy", "assets"}, {"notes"})
    _str(f"{where}#/id", b["id"], _BOARD_ID)
    if "notes" in b:
        _str(f"{where}#/notes", b["notes"])
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


def _check_product(where: str, p: Any, boards: dict[str, dict]) -> None:
    _keys(where, p, {"id", "version", "boards", "first_boards"} | PRODUCT_BLOCKS)
    _str(f"{where}#/id", p["id"], _BOARD_ID)
    _int(f"{where}#/version", p["version"])
    if not isinstance(p["boards"], list) or not p["boards"]:
        raise CatalogueError(f"{where}#/boards: expected a non-empty list of board ids")
    if len(set(map(str, p["boards"]))) != len(p["boards"]):
        raise CatalogueError(f"{where}#/boards: a board is listed twice")
    for i, ref in enumerate(p["boards"]):
        if ref not in boards:
            raise CatalogueError(f"{where}#/boards/{i}: no board {ref!r}")
        # The installer finds a unit in fastboot first, so a board it installs needs that key.
        if "fastboot_product" not in boards[ref]["identity"]:
            raise CatalogueError(f"{where}#/boards/{i}: {ref} has no identity.fastboot_product")
    # The boards the first release named. Shows from then refuse a manifest or rootfs marker naming any
    # other, so a later board reads a signed manifest of its own and sits in the marker's more_boards.
    first = p["first_boards"]
    if not isinstance(first, list) or not first:
        raise CatalogueError(f"{where}#/first_boards: expected a non-empty list of board ids")
    if len(set(map(str, first))) != len(first):
        raise CatalogueError(f"{where}#/first_boards: a board is listed twice")
    for i, ref in enumerate(first):
        if ref not in p["boards"]:
            raise CatalogueError(f"{where}#/first_boards/{i}: {ref!r} is not one of the product's boards")
    for block in PRODUCT_BLOCKS:
        if not isinstance(p[block], dict) or not p[block]:
            raise CatalogueError(f"{where}#/{block}: expected a non-empty object")


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
        self._products = _load_dir(root / "products")
        for name, (file, data) in self._assets.items():
            _check_asset(self._rel(file), data)
        assets = {k: v for k, (_, v) in self._assets.items()}
        for name, (file, data) in self._boards.items():
            _check_board(self._rel(file), data, assets)
        used = {ref for _, b in self._boards.values() for ref in b["assets"].values()}
        unused = sorted(set(assets) - used)
        if unused:
            raise CatalogueError(f"assets no board uses: {unused}")
        repos = {a["upstream"]["repo"] for a in assets.values() if a["kind"] == "amonet"}
        if len(repos) > 1:
            raise CatalogueError(f"amonet assets name different upstream repos: {sorted(repos)}")
        boards = {k: v for k, (_, v) in self._boards.items()}
        for name, (file, data) in self._products.items():
            _check_product(self._rel(file), data, boards)
        listed = {ref for _, p in self._products.values() for ref in p["boards"]}
        unlisted = sorted(set(boards) - listed)
        if unlisted:
            raise CatalogueError(f"boards no product lists: {unlisted}")
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

    def product(self) -> dict:
        """The one product in the catalogue. A second product needs someone to decide who picks it."""
        if len(self._products) != 1:
            raise CatalogueError(f"expected exactly one product, found {sorted(self._products)}; refusing")
        (_, p), = self._products.values()
        return json.loads(json.dumps(p))

    def profile(self, board: str) -> "BoardProfile":
        """The installer's view of one board the product lists."""
        if board not in self.product()["boards"]:
            raise CatalogueError(f"no catalogue board {board!r} in the product; refusing")
        r = self.resolve(board)
        amonet = r["assets"]["amonet"]
        return BoardProfile(
            board=r["id"],
            fastboot_product=r["identity"]["fastboot_product"],
            model=r["model"],
            product_id=r["profile_id"],
            display_width=r["display"]["width"],
            display_height=r["display"]["height"],
            amonet_dir_name=amonet["file"].removesuffix(".zip"),
            amonet_kaeru_name=PurePosixPath(amonet["kaeru"]).name,
        )

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

    def on_show(self, board: str) -> dict:
        """board.json: what the Show itself keeps of its board, written at install next to its profile.
        Only what the Show reads, so no assets and no install policy (docs/board-catalogue.md)."""
        r = self.resolve(board)
        return {key: r[key] for key in ON_SHOW_KEYS}

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


@dataclass(frozen=True)
class BoardProfile:
    """What the install steps need to know about one board, all of it read from the catalogue.

    The confirmation phrases used to live here too. They are the same on every board, so they belong to
    the step that asks for them: unlock.CONFIRM_PHRASE and install.FINAL_CONFIRM_PHRASE.
    """
    board: str
    fastboot_product: str
    model: str
    product_id: str
    display_width: int
    display_height: int
    amonet_dir_name: str  # the amonet zip's file name without .zip; the folder it unpacks into
    amonet_kaeru_name: str  # the file name of the zip's kaeru payload, under amonet/bin/


_default: Catalogue | None = None


def default() -> Catalogue:
    """The repository's catalogue, loaded and checked once per process."""
    global _default
    if _default is None:
        _default = Catalogue()
    return _default


def load_or_exit() -> Catalogue:
    """default(), for a command line tool to call before it imports the rest of jarvis_crown, several of
    whose modules read the catalogue at import. A broken catalogue still stops every command, read-only
    ones too, but with one plain message instead of a traceback (GENBU-173). Nothing has run yet."""
    try:
        return default()
    except (CatalogueError, OSError, UnicodeDecodeError) as exc:
        sys.exit(f"The board catalogue is broken, so nothing ran and no Show was touched:\n  {exc}\n"
                 "Fix that file and run this again. docs/board-catalogue.md says what belongs in it.")


def supported_boards() -> tuple[str, ...]:
    """The product's boards, in the order the product lists them."""
    return tuple(default().product()["boards"])


def first_boards() -> tuple[str, ...]:
    """The boards the first release named, which every Show already knows."""
    return tuple(default().product()["first_boards"])


def later_boards() -> tuple[str, ...]:
    """The product's other boards, in its order: each reads manifest-<board>.json (GENBU-176)."""
    first = first_boards()
    return tuple(b for b in supported_boards() if b not in first)


def _or_list(items: tuple[str, ...]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


# OPEN DECISION (GENBU-163 Q8): the two lookups below strip spaces and fold case before they match,
# exactly as boards.py did, so "Crown" picks crown and "crown" picks CROWN. The catalogue itself
# matches exactly (board_for, resolve). Whether the installer should stop folding is Jade's call;
# until then this keeps today's behaviour, and tests/test_board_catalogue.py pins it.
def profile_for_board(board: str) -> BoardProfile:
    key = board.strip().lower()
    boards = supported_boards()
    if key not in boards:
        raise ValueError(f"unsupported Jarvis Show board {board!r}; choose {_or_list(boards)}")
    return default().profile(key)


def profile_for_product(product: str) -> BoardProfile:
    key = product.strip().upper()
    try:
        board = default().board_for("fastboot_product", key)
    except CatalogueError:
        raise ValueError(f"unsupported fastboot product {product!r}") from None
    if board not in supported_boards():
        raise ValueError(f"unsupported fastboot product {product!r}")
    return default().profile(board)


def on_show_text(cat: Catalogue, board: str) -> str:
    """board.json as the installer writes it: one line, so it fits one console command."""
    return json.dumps(cat.on_show(board), separators=(",", ":")) + "\n"


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
            n = len(cat._products)
            print(f"ok: {n} product{'s' * (n != 1)}, {len(cat.boards())} boards, {len(cat._assets)} assets")
        elif len(argv) >= 2 and argv[0] == "check-files":
            lines = check_files(cat, [Path(d).expanduser() for d in argv[1:]])
            print("\n".join(lines))
            return 1 if any("MISMATCH" in l or "MISSING" in l for l in lines) else 0
        elif len(argv) == 2 and argv[0] == "resolve":
            print(json.dumps(cat.resolve(argv[1]), indent=2))
        elif len(argv) == 2 and argv[0] == "on-show":
            print(on_show_text(cat, argv[1]), end="")
        elif argv == ["write-on-show"]:
            for board in cat.boards():
                (ON_SHOW_DIR / f"{board}.json").write_text(on_show_text(cat, board))
            print(f"wrote {len(cat.boards())} boards to {ON_SHOW_DIR.relative_to(ROOT.parent)}")
        elif len(argv) == 3 and argv[0] == "explain":
            value, owner = cat.explain(argv[1], argv[2])
            print(f"{json.dumps(value)}  <- {owner}")
        else:
            print("usage: check | resolve <board> | explain <board> <path> | check-files <dir>... | on-show <board>"
                  " | write-on-show",
                  file=sys.stderr)
            return 2
    except CatalogueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
