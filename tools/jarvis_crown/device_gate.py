#!/usr/bin/env python3
"""Read-only live-device gate for Jarvis Crown v1.

This module may invoke fastboot read/query operations only. It never flashes,
boots, erases, reboots, unlocks, or writes device state.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from typing import Callable, Sequence

from jarvis_crown.catalogue import profile_for_board, profile_for_product, supported_boards

FASTBOOT_TIMEOUT_SECONDS = 8
_PROFILES = tuple(profile_for_board(board) for board in supported_boards())
SUPPORTED_PRODUCTS = {profile.fastboot_product for profile in _PROFILES}
SUPPORTED_PRODUCT = "CROWN"  # compatibility alias for Crown-only callers/tests


# Products upstream may know about but Jarvis Show v1 intentionally refuses.
# Keep this list descriptive only: no destructive code path may select these profiles.
KNOWN_UNSUPPORTED_PRODUCTS: dict[str, str] = {}

# "CHECKERS (Echo Show 5 1st gen), CRONOS (Echo Show 5 2nd gen) and CROWN (Echo Show 8 1st gen)":
# the catalogue's products and models, the model cut before its " / " part number.
def _summary() -> str:
    parts = [f"{p.fastboot_product} ({p.model.partition(' / ')[0]})"
             for p in sorted(_PROFILES, key=lambda p: p.fastboot_product)]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


SUPPORTED_SUMMARY = _summary()


def unsupported_product_message(product: str) -> str:
    key = product.strip().upper()
    if key in KNOWN_UNSUPPORTED_PRODUCTS:
        return (
            f"{key} is {KNOWN_UNSUPPORTED_PRODUCTS[key]}, which is intentionally unsupported by "
            f"Jarvis Show v1. Only {SUPPORTED_SUMMARY} are supported. Do not flash this "
            "device; no write was attempted."
        )
    return (
        f"{key or product!r} is not a supported Jarvis Show target. "
        f"Jarvis Show v1 supports only {SUPPORTED_SUMMARY}. This may be a newer "
        "generation; do not flash it. No write was attempted."
    )


class DeviceGateError(RuntimeError):
    """Raised when live device identity cannot be proven safe."""


@dataclass(frozen=True)
class DeviceIdentity:
    serial: str
    product: str
    unlocked: bool
    lk_build_desc: str | None


def _run(
    argv: Sequence[str],
    *,
    timeout: int = FASTBOOT_TIMEOUT_SECONDS,
    run: Callable = subprocess.run,
) -> subprocess.CompletedProcess:
    try:
        return run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeviceGateError(f"fastboot query timed out after {timeout}s: {' '.join(argv)}") from exc
    except OSError as exc:
        raise DeviceGateError(f"cannot execute fastboot query {' '.join(argv)}: {exc}") from exc


def _output(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _require_success(result: subprocess.CompletedProcess, action: str) -> str:
    text = _output(result)
    if result.returncode != 0:
        summary = text.strip().replace("\n", " | ") or f"exit {result.returncode}"
        raise DeviceGateError(f"{action} failed: {summary}")
    return text


def fastboot_serials(*, run: Callable = subprocess.run) -> list[str]:
    result = _run(["fastboot", "devices"], run=run)
    text = _require_success(result, "fastboot devices")
    serials: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) < 2 or fields[1].lower() != "fastboot":
            continue
        serials.append(fields[0])
    return serials


def _getvar(serial: str, name: str, *, run: Callable = subprocess.run) -> str:
    result = _run(["fastboot", "-s", serial, "getvar", name], run=run)
    text = _require_success(result, f"fastboot getvar {name}")
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*:\s*(.*?)\s*$", re.IGNORECASE)
    for raw in text.splitlines():
        match = pattern.match(raw)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    raise DeviceGateError(f"fastboot getvar {name} returned no parseable {name}: value")


def _check_in_step(serial: str, *, run: Callable = subprocess.run) -> None:
    """Refuses a fastboot that answers out of step.

    A Show whose fastboot was cut off mid-command (seen on a crown after an interrupted run) can
    answer every question with the answer to the one before: product says "true", unlock_status
    says "CROWN". Nothing it says can be trusted then, and a flash over it would go wrong. Its
    serial number is the one answer known beforehand, so it is asked before and after the rest.
    """
    try:
        said = _getvar(serial, "serialno", run=run)
    except DeviceGateError as exc:
        said = None
        reason = str(exc)
    else:
        reason = "its answer was meant for an earlier question"
    if said != serial:
        raise DeviceGateError(
            "the Show's fastboot is answering out of step (asked for its serial number, "
            f"{reason}), so nothing it says can be trusted and nothing was written. Restart it into "
            "fastboot (fastboot reboot-bootloader, or unplug it and start it in fastboot again) and "
            "run this again"
        )


def identify_show(*, expected_board: str | None = None, run: Callable = subprocess.run) -> DeviceIdentity:
    """Prove exactly one connected fastboot device is a supported Jarvis Show.

    All operations in this function are read-only fastboot queries. When
    expected_board is supplied, a different supported board still fails closed.
    """
    serials = fastboot_serials(run=run)
    if not serials:
        raise DeviceGateError("no fastboot device detected")
    if len(serials) != 1:
        raise DeviceGateError(
            "exactly one fastboot device is required; detected: " + ", ".join(serials)
        )

    serial = serials[0]
    _check_in_step(serial, run=run)
    product = _getvar(serial, "product", run=run).upper()
    try:
        detected = profile_for_product(product)
    except ValueError as exc:
        raise DeviceGateError(unsupported_product_message(product)) from exc
    if expected_board is not None:
        expected = profile_for_board(expected_board)
        if detected.board != expected.board:
            raise DeviceGateError(
                f"connected product {product!r} is {detected.board}, expected {expected.board}"
            )

    raw_unlock = _getvar(serial, "unlock_status", run=run).strip().lower()
    if raw_unlock not in {"true", "false"}:
        raise DeviceGateError(
            f"unrecognized unlock_status {raw_unlock!r}; refusing to continue"
        )

    lk_build_desc: str | None
    try:
        lk_build_desc = _getvar(serial, "lk_build_desc", run=run)
    except DeviceGateError:
        # Already-unlocked units can continue to the TWRP layout gate without
        # LK metadata. Locked units are refused by unlock_show(): we require the
        # bootloader build to be readable before invoking the destructive Amonet
        # exploit, even though the pinned Crown bundle has a default payload.
        lk_build_desc = None
    _check_in_step(serial, run=run)

    return DeviceIdentity(
        serial=serial,
        product=product,
        unlocked=raw_unlock == "true",
        lk_build_desc=lk_build_desc,
    )


def identify_crown(*, run: Callable = subprocess.run) -> DeviceIdentity:
    """Compatibility wrapper requiring the Crown profile."""
    return identify_show(expected_board="crown", run=run)


def identify_checkers(*, run: Callable = subprocess.run) -> DeviceIdentity:
    """Require an Echo Show 5 1st gen / checkers target."""
    return identify_show(expected_board="checkers", run=run)


def in_plain_words(err: DeviceGateError) -> str:
    """Why the Show could not be checked, and what to do, for somebody who has not read the above. The
    gate's own message stays in the log line next to it (GENBU-216)."""
    said = str(err)
    if said == "no fastboot device detected":
        return ("No Show was found in fastboot. Plug it in over USB and start it in fastboot, or leave it "
                "running: install restarts a Show on Android, TWRP or Jarvis into fastboot itself. To check "
                "only this computer, give --board crown, checkers or cronos.")
    if said.startswith("exactly one fastboot device"):
        return "More than one device is in fastboot. Unplug everything but the Show and run this again."
    if said.startswith("cannot execute fastboot"):
        return "fastboot isn't on this computer: install Android platform-tools, then run this again."
    if said.startswith("fastboot query timed out"):
        return ("The Show stopped answering in fastboot. Unplug it, start it in fastboot again and run this "
                "again.")
    if said.startswith("connected product"):
        return ("The Show plugged in is another model than --board says. Give the right --board, or leave "
                "it out and the installer goes by the Show.")
    if "not a supported Jarvis Show target" in said or "intentionally unsupported" in said:
        return ("This device isn't one the installer supports, so nothing will be written to it. Only "
                f"{SUPPORTED_SUMMARY} are.")
    return said
