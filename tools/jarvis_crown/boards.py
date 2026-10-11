#!/usr/bin/env python3
"""Supported Jarvis Show board profiles.

The runtime/rootfs is shared. Destructive install assets remain board-specific and
must never be mixed between products.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoardProfile:
    board: str
    fastboot_product: str
    model: str
    product_id: str
    display_width: int
    display_height: int
    amonet_dir_name: str
    amonet_kaeru_name: str
    unlock_confirmation: str
    install_confirmation: str


PROFILES = {
    "crown": BoardProfile(
        board="crown",
        fastboot_product="CROWN",
        model="Echo Show 8 1st gen / C7H6N3",
        product_id="jarvis-crown-v1",
        display_width=1280,
        display_height=800,
        amonet_dir_name="amonet-crown-v2.0.1",
        amonet_kaeru_name="crown-kaeru.bin",
        unlock_confirmation="UNLOCK",
        install_confirmation="ERASE",
    ),
    "checkers": BoardProfile(
        board="checkers",
        fastboot_product="CHECKERS",
        model="Echo Show 5 1st gen / H23K37",
        product_id="jarvis-checkers-v1",
        display_width=960,
        display_height=480,
        amonet_dir_name="amonet-checkers-v2.0.1",
        amonet_kaeru_name="checkers-kaeru.bin",
        unlock_confirmation="UNLOCK",
        install_confirmation="ERASE",
    ),
}

PRODUCT_TO_BOARD = {profile.fastboot_product: board for board, profile in PROFILES.items()}

# Products upstream may know about but Jarvis Show v1 intentionally refuses.
# Keep this list descriptive only: no destructive code path may select these profiles.
KNOWN_UNSUPPORTED_PRODUCTS = {
    "CRONOS": "Echo Show 5 2nd gen",
}


def unsupported_product_message(product: str) -> str:
    key = product.strip().upper()
    if key in KNOWN_UNSUPPORTED_PRODUCTS:
        return (
            f"{key} is {KNOWN_UNSUPPORTED_PRODUCTS[key]}, which is intentionally unsupported by "
            "Jarvis Show v1. Only Echo Show 5 1st gen (CHECKERS) and Echo Show 8 1st gen "
            "(CROWN) are supported. Do not flash this device; no write was attempted."
        )
    return (
        f"{key or product!r} is not a supported first-generation Jarvis Show target. "
        "Jarvis Show v1 supports only CHECKERS (Echo Show 5 1st gen) and CROWN "
        "(Echo Show 8 1st gen). This may be a newer generation; do not flash it. "
        "No write was attempted."
    )


def profile_for_board(board: str) -> BoardProfile:
    key = board.strip().lower()
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unsupported Jarvis Show board {board!r}; choose crown or checkers") from exc


def profile_for_product(product: str) -> BoardProfile:
    key = product.strip().upper()
    try:
        return PROFILES[PRODUCT_TO_BOARD[key]]
    except KeyError as exc:
        raise ValueError(f"unsupported fastboot product {product!r}") from exc
