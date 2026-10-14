#!/usr/bin/env python3
"""Pinned download sources for the large per-board install assets.

The LineageOS zip (staged only for its vendor drivers) and the board boot image are hundreds of MB and
never live in the repository. They are fetched over HTTPS into a cache outside the checkout and kept only
when their size and SHA-256 match what is listed here. Boot hashes come from install.py, so the installer
and the fetcher cannot disagree about which image is trusted.

Amonet packages are only published as XDA attachments, which need a login. Those are marked manual: the
fetcher takes them from a folder the person downloaded into (~/Downloads by default) instead of the web,
and unpacks a pinned one next to it for the installer.
An empty sha256 means nobody has reviewed and pinned that package yet, so it is never used, only inspected.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from jarvis_crown.boards import profile_for_board
from jarvis_crown.install import BOOT_SHA256_BY_BOARD


@dataclass(frozen=True)
class Asset:
    kind: str
    name: str
    url: str
    sha256: str
    size: int
    source: str
    manual: bool = False  # behind a login; downloaded by hand from url, then picked up locally


LINEAGE_RELEASES = "https://github.com/amazon-oss/releases/releases/download"
TECHO5_RELEASES = "https://github.com/HuskerMinion/techo5/releases/download"
XDA_ATTACHMENTS = "https://xdaforums.com/attachments"
XDA_CRONOS_THREAD = (
    "https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-show-5-2nd-gen-2021-cronos.4772596/"
)

# Where the open-source parts of each Amonet package live, so a downloaded zip can be compared file by
# file before anyone pins it. The XDA threads link mt8163-echo-show; the per-board branches came first.
AMONET_SOURCE = ("R0rt1z2/amonet", ("mt8163-echo-show", "mt8163-checkers", "mt8163-crown", "mt8163-cronos"))

# LineageOS 18.1 builds from amazon-oss/releases. Each sha256 is the one the release notes publish and
# the one GitHub reports as the asset digest. 20260904 is the first build whose kernel is
# 4.9.337-g8d928c5176cc, the ABI the Jarvis boot images and the vendor-module gate require.
ASSETS: dict[str, tuple[Asset, ...]] = {
    "crown": (
        Asset(
            "lineage", "lineage-18.1-20260904-UNOFFICIAL-crown.zip",
            f"{LINEAGE_RELEASES}/lineage-18.1-crown-v0.5/lineage-18.1-20260904-UNOFFICIAL-crown.zip",
            "a01359a5e13dad8ac24c5507e8a747163b992012410b14d2ca9a8711ec4a1491", 489922458,
            "amazon-oss/releases lineage-18.1-crown-v0.5",
        ),
        Asset(
            "boot", "techo5-boot-crown-v1.0.1.img",
            f"{TECHO5_RELEASES}/v1.0.1/techo5-boot-crown-v1.0.1.img",
            BOOT_SHA256_BY_BOARD["crown"] or "", 13156352,
            "HuskerMinion/techo5 v1.0.1 (signed manifest)",
        ),
        # Reviewed 2026-10-09: scripts match R0rt1z2/amonet (main.py/common.py as of 999f8d0ab5), and
        # fastbrick.sh, profile.sh, device.prop, fastbrick.img, fastboot(32) and twrp.img are the bytes
        # unlock.py and recovery.py already pin. Extract it and pass the folder as --amonet-dir.
        Asset(
            "amonet", "amonet-crown-v2.0.1.zip",
            f"{XDA_ATTACHMENTS}/amonet-crown-v2-0-1-zip.6373841/",
            "5db974c321ae2f4586f7c51c95b7bdffb61ef85bde05ce7e62f78196a984c24f", 44233517,
            "XDA thread 4762900 (R0rt1z2), needs an XDA login",
            manual=True,
        ),
    ),
    "checkers": (
        Asset(
            "lineage", "lineage-18.1-20260904-UNOFFICIAL-checkers.zip",
            f"{LINEAGE_RELEASES}/lineage-18.1-checkers-v0.7/lineage-18.1-20260904-UNOFFICIAL-checkers.zip",
            "785fa643fd68b2e6f6f02d96a2da58373c6a577b92a27cf6cec69603bb94068e", 485795474,
            "amazon-oss/releases lineage-18.1-checkers-v0.7",
        ),
        Asset(
            "boot", "techo5-boot-checkers-v1.0.1.img",
            f"{TECHO5_RELEASES}/v1.0.1/techo5-boot-checkers-v1.0.1.img",
            BOOT_SHA256_BY_BOARD["checkers"] or "", 13172736,
            "HuskerMinion/techo5 v1.0.1 (signed manifest)",
        ),
        # Needed only by units first unlocked with Amonet 1.x (boot starts with "microloader by xyz"):
        # flashing this zip in TWRP moves them to the 2.x layout (jarvis-show.py amonet-upgrade).
        # Reviewed 2026-10-09: update-binary is byte-identical to R0rt1z2/amonet mt8163-echo-show HEAD,
        # the other scripts match upstream (main.py/common.py as of 999f8d0ab5), fastbrick.sh and
        # fastboot(32) are the bytes unlock.py pins, device.prop says checkers, and twrp.img is the
        # 16.1.0-20260812-checkers build. Preloader, LK, TZ and kaeru exist only in this zip.
        Asset(
            "amonet", "amonet-checkers-v2.0.1.zip",
            f"{XDA_ATTACHMENTS}/amonet-checkers-v2-0-1-zip.6373840/",
            "770324a8ed5ab922c0383f8ba072d70fc0190cc2c879f12f67b8d6cfa3ad30ee", 63836775,
            "XDA thread 4762900 (R0rt1z2), browser download (XDA's CDN refuses scripts)",
            manual=True,
        ),
    ),
    "cronos": (
        Asset(
            "lineage", "lineage-18.1-20260904-UNOFFICIAL-cronos.zip",
            f"{LINEAGE_RELEASES}/lineage-18.1-cronos-v0.4/lineage-18.1-20260904-UNOFFICIAL-cronos.zip",
            "4c355998061a454792128d4b730932b47ed05a3d2a6d2628599218f44cc84678", 484847340,
            "amazon-oss/releases lineage-18.1-cronos-v0.4",
        ),
        # Upstream stopped shipping a cronos boot image after v0.7.16; see BOOT_SHA256_BY_BOARD.
        Asset(
            "boot", "techo5-boot-v0.7.16.img",
            f"{TECHO5_RELEASES}/v0.7.16/techo5-boot-v0.7.16.img",
            BOOT_SHA256_BY_BOARD["cronos"] or "", 13553664,
            "HuskerMinion/techo5 v0.7.16 (signed manifest)",
        ),
        # Reviewed 2026-10-14: update-binary and the scripts match R0rt1z2/amonet mt8163-echo-show
        # (main.py/common.py as of 999f8d0ab5; the two commits after it only speed up bootrom-mode eMMC
        # writes), fastbrick.sh and fastboot(32) are the bytes unlock.py pins for crown, device.prop says
        # cronos, twrp.img is the 16.1.0-20260812 omni_cronos build, and cronos-kaeru.bin is the stock
        # kaeru install-show.py's LOGO_KAERU pins. Preloader, LK, TZ and fastbrick.img exist only in
        # this zip. The attachment link is on the first post of the thread.
        Asset(
            "amonet", "amonet-cronos-v2.0.1.zip",
            XDA_CRONOS_THREAD,
            "07a49c8fb2b244330297810b261333ba23976eca5ab3e0d9032f45f4c9847621", 44403627,
            "XDA thread 4772596 (R0rt1z2), browser download (XDA's CDN refuses scripts)",
            manual=True,
        ),
    ),
}


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
