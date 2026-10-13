#!/usr/bin/env python3
"""Pinned download sources for the large per-board install assets.

The LineageOS zip (staged only for its vendor drivers) and the board boot image are hundreds of MB and
never live in the repository. They are fetched over HTTPS into a cache outside the checkout and kept only
when their size and SHA-256 match their pin. Every source, pin and review note lives in the catalogue
(catalogue/assets/), which install.py, recovery.py and unlock.py read too, so the installer and the
fetcher cannot disagree about which file is trusted.

Amonet packages are only published as XDA attachments, which need a login. Those are marked manual: the
fetcher takes them from a folder the person downloaded into (~/Downloads by default) instead of the web,
and unpacks a pinned one next to it for the installer.
An empty sha256 means nobody has reviewed and pinned that package yet, so it is never used, only inspected.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from jarvis_crown import catalogue
from jarvis_crown.catalogue import profile_for_board


@dataclass(frozen=True)
class Asset:
    kind: str
    name: str
    url: str
    sha256: str
    size: int
    source: str
    manual: bool = False  # behind a login; downloaded by hand from url, then picked up locally


# Where the open-source parts of each Amonet package live, so a downloaded zip can be compared file by
# file before anyone pins it. The XDA threads link mt8163-echo-show; the per-board branches came first.
AMONET_SOURCE = ("R0rt1z2/amonet", ("mt8163-echo-show", "mt8163-checkers", "mt8163-crown", "mt8163-cronos"))

KIND_ORDER = ("lineage", "boot", "amonet")  # the order the fetcher works through them


def _asset(a: dict) -> Asset:
    # "" and 0 mean not pinned: the fetcher inspects such a file but never hands it to the installer.
    return Asset(a["kind"], a["file"], a["url"], a.get("sha256", ""), a.get("size", 0), a["source"],
                 manual=a["manual"])


def _from_catalogue(board: str) -> tuple[Asset, ...]:
    resolved = catalogue.default().resolve(board)["assets"]
    return tuple(_asset(resolved[kind]) for kind in KIND_ORDER)


ASSETS: dict[str, tuple[Asset, ...]] = {board: _from_catalogue(board) for board in catalogue.supported_boards()}


def default_cache_dir() -> Path:
    """$JARVIS_SHOW_ASSETS, else $XDG_CACHE_HOME/jarvis-show, else ~/.cache/jarvis-show."""
    explicit = os.environ.get("JARVIS_SHOW_ASSETS")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "jarvis-show"


def assets_for(board: str) -> tuple[Asset, ...]:
    try:
        return ASSETS[board]
    except KeyError:
        raise ValueError(f"no pinned assets for board {board!r}") from None


def asset_path(cache: Path, board: str, asset: Asset) -> Path:
    return cache / board / asset.name


def amonet_bundle_dir(cache: Path, board: str) -> Path:
    """Where fetch-show-assets unpacks the pinned Amonet zip; the installer's --amonet-dir default."""
    return cache / board / profile_for_board(board).amonet_dir_name
