#!/usr/bin/env python3
"""Fail-closed Amonet unlock wrapper for Jarvis Crown v1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping

from jarvis_crown.boards import BoardProfile, profile_for_product
from jarvis_crown.device_gate import DeviceGateError, DeviceIdentity, identify_crown, identify_show
from jarvis_crown.recovery import RecoveryError, _adb_shell, _single_recovery, verify_twrp_board
from jarvis_crown.timing import POLL_SECONDS, POST_AMONET_TWRP_TIMEOUT_SECONDS

CONFIRM_PHRASE = "UNLOCK"
FASTBRICK_TIMEOUT_SECONDS = 600
VERIFY_INTERVAL_SECONDS = POLL_SECONDS
VERIFY_ATTEMPTS = int(POST_AMONET_TWRP_TIMEOUT_SECONDS / VERIFY_INTERVAL_SECONDS)

# fastbrick.sh prints this and exits while the payload is still writing. From here on the Show must
# not lose power: it writes preloader, LK, TEE and TWRP for up to five minutes, then boots TWRP.
EXPLOIT_LANDED = "Exploit most likely successful!"
KEEP_POWERED = "Keep the Show on mains power and USB and do not unplug it or press its buttons."
# No BROM download mode on these boards, so an unlock that dies half way can't be undone.
NO_BROM_BOARDS = frozenset({"cronos"})
# fastbrick.sh runs `clear` and colours its lines; neither belongs in our output.
_TERMINAL_CODES = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])|[\x00-\x08\x0b-\x1f\x7f]")

# Pinned from the user's known-good amonet-crown-v2.0.1 archive.
AMONET_UNLOCK_SHA256: dict[str, str] = {
    "fastbrick.sh": "aa93be2a752f8f4d4da31fa4bb733e9ce1dcdb9a55feda3795f52b95a9062ec5",
    "profile.sh": "844e584acc0bda882ff4c897cf2f55a5c9b8b9d913b9f5b7f897e6780371605a",
    "device.prop": "4313cbc0c8a75d418ec96890a317b89eae71bc78c7c92ace358000cb7d8d7bd5",
    "bin/fastbrick.img": "6b899c2919933b94a8c3e3454c22fe733d1a346d87c27640554a828aae9c6e9a",
    "bin/fastboot": "cb3d13b850143da85eb4b2099462514894459213da93d03d6ff4782d927d6e20",
    "bin/fastboot32": "8912e9926cfc45503ad960866501305a684462b1dd1a44c5d82b1f075d6dd8c1",
}
# What fastbrick runs. A board's hash manifest names exactly these, so a short one can't leave
# one of them unchecked (GENBU-172).
UNLOCK_FILES: tuple[str, ...] = tuple(AMONET_UNLOCK_SHA256)


class UnlockError(RuntimeError):
    """Raised when unlock cannot be proven safe/successful."""


def check_unlock_manifest(names) -> None:
    """Refuse a hash manifest that leaves out one of UNLOCK_FILES or names anything else."""
    names = set(names)
    missing = [name for name in UNLOCK_FILES if name not in names]
    extra = sorted(names - set(UNLOCK_FILES))
    if missing or extra:
        problems = ([f"missing {', '.join(missing)}"] if missing else []) + (
            [f"unknown {', '.join(extra)}"] if extra else [])
        raise UnlockError(
            f"Amonet hash manifest must name exactly {', '.join(UNLOCK_FILES)}; {'; '.join(problems)}"
        )


@dataclass(frozen=True)
class UnlockResult:
    identity: DeviceIdentity
    amonet_invoked: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_amonet_unlock_assets(
    amonet_dir: Path,
    *,
    expected_hashes: Mapping[str, str] = AMONET_UNLOCK_SHA256,
) -> None:
    check_unlock_manifest(expected_hashes)
    root = amonet_dir / "amonet"
    for relative, expected in expected_hashes.items():
        path = root / relative
        if not path.is_file():
            raise UnlockError(f"required Amonet unlock asset missing: {relative}")
        actual = _sha256(path)
        if actual.lower() != expected.lower():
            raise UnlockError(
                f"Amonet unlock asset hash mismatch: {relative} "
                f"(expected {expected}, got {actual})"
            )


@dataclass(frozen=True)
class FastbrickResult:
    returncode: int
    output: str


def unlock_warnings(profile: BoardProfile) -> list[str]:
    """What someone has to know before typing the unlock phrase."""
    lines = [
        f"unlocking {profile.model} runs the pinned Amonet exploit, which rewrites the bootloader.",
        "keep the Show on mains power and the USB cable plugged in for the whole stage.",
        f"it retries the payload until it lands and prints \"{EXPLOIT_LANDED}\".",
        "after that the Show writes for up to five minutes and comes up in TWRP. Don't unplug it or press "
        "its buttons, even when the screen looks stuck.",
        f"Jarvis waits up to {POST_AMONET_TWRP_TIMEOUT_SECONDS // 60} minutes for TWRP and says when it's done.",
    ]
    if profile.board in NO_BROM_BOARDS:
        lines.append(f"{profile.model} has no BROM rescue mode: a brick during this stage is permanent.")
    else:
        lines.append("a brick during this stage may not be recoverable.")
    return lines


def run_fastbrick_script(
    root: Path,
    on_line: Callable[[str], None],
    *,
    timeout: float = FASTBRICK_TIMEOUT_SECONDS,
) -> FastbrickResult:
    """Run upstream fastbrick.sh in its own process group, showing its output as it comes.

    On timeout the whole group goes, so no `timeout 8 fastboot flash brick` child can still land
    the exploit after Jarvis has given up on it.
    """
    try:
        proc = subprocess.Popen(
            ["bash", "fastbrick.sh"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise UnlockError(f"cannot execute Amonet fastbrick: {exc}") from exc
    assert proc.stdin is not None and proc.stdout is not None
    # YES answers the confirm prompt, x the closing "press any key".
    try:
        proc.stdin.write("YES\nx\n")
        proc.stdin.close()
    except BrokenPipeError:
        pass  # the script stopped before reading; its output says why

    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                line = lines.get(timeout=max(deadline - time.monotonic(), 0))
            except queue.Empty:
                _stop(proc, reader)
                raise UnlockError(
                    f"Amonet did not finish within {int(timeout)} s and was stopped. "
                    + _after_stop(seen)
                )
            if line is None:
                break
            line = _TERMINAL_CODES.sub("", line.rstrip("\n"))
            seen.append(line + "\n")
            if line.strip():
                on_line(line)
    except KeyboardInterrupt:
        # Its own session keeps Ctrl-C from reaching it, so stop it here rather than leave it orphaned.
        _stop(proc, reader)
        raise UnlockError("Amonet was interrupted and stopped. " + _after_stop(seen)) from None
    returncode = proc.wait()
    reader.join(timeout=5)
    proc.stdout.close()
    return FastbrickResult(returncode, "".join(seen))


def _after_stop(seen: list[str]) -> str:
    landed = any(EXPLOIT_LANDED in x for x in seen)
    return (
        ("The exploit had already landed. " if landed else "The last attempt may still have landed. ")
        + KEEP_POWERED
        + " Wait 15 minutes, then check whether it shows TWRP before running anything else."
    )


def _stop(proc: subprocess.Popen, reader: threading.Thread) -> None:
    _kill_group(proc)
    reader.join(timeout=5)
    if not reader.is_alive():
        proc.stdout.close()


def _kill_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _invoke_fastbrick(
    amonet_dir: Path,
    *,
    run: Callable[..., FastbrickResult] = run_fastbrick_script,
    say: Callable[[str], None] = print,
) -> None:
    root = amonet_dir / "amonet"
    result = run(root, say, timeout=FASTBRICK_TIMEOUT_SECONDS)
    out = result.output
    if "eMMC-RO" in out:
        raise UnlockError(
            "the Show's eMMC has worn out and locked itself read-only, so it can't be unlocked. "
            "Upstream says nothing was written and it is safe to reboot."
        )
    if "Device mismatch" in out:
        raise UnlockError(
            "the Amonet payload refused this device (Device mismatch): wrong board or wrong bundle. "
            "Re-identify the device read-only before any retry."
        )
    if "already unlocked" in out:
        raise UnlockError(
            "fastbrick says the bootloader is already unlocked, but the read-only check saw it locked; "
            "re-identify the device before anything else"
        )
    if result.returncode != 0 or EXPLOIT_LANDED not in out:
        raise UnlockError(
            f"Amonet fastbrick exited with status {result.returncode} without landing the exploit; "
            "device state must be re-identified before any retry"
        )


def _wait_for_twrp(
    original: DeviceIdentity,
    board: str,
    *,
    adb_run: Callable = subprocess.run,
    identify: Callable[[], DeviceIdentity] = identify_crown,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = VERIFY_ATTEMPTS,
    say: Callable[[str], None] = print,
) -> DeviceIdentity:
    """Wait for the Show to come up in TWRP after the exploit, without sending it anything.

    While the payload writes, only `adb devices` runs, which never reaches the Show. Fastboot is
    asked once, at the very end, in case a unit stopped in hacked fastboot instead of TWRP.
    """
    say(f"the exploit landed; the Show is writing its bootloader and will come up in TWRP. {KEEP_POWERED}")
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            serial = _single_recovery(run=adb_run)
            if serial:
                verify_twrp_board(serial, board, run=adb_run)
                twrp_serial = _adb_shell(serial, "getprop ro.serialno", run=adb_run).strip()
                if twrp_serial != original.serial:
                    raise UnlockError(
                        f"TWRP came up with serial {twrp_serial or 'unknown'}, not fastboot serial "
                        f"{original.serial}; refusing to continue on an ambiguous device. The unlock "
                        "itself has finished, so the Show can be unplugged safely now."
                    )
                return DeviceIdentity(
                    serial=original.serial,
                    product=original.product,
                    unlocked=True,
                    lk_build_desc=original.lk_build_desc,
                )
        except RecoveryError as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            sleep(VERIFY_INTERVAL_SECONDS)

    try:
        current = identify()
    except DeviceGateError as exc:
        current = None
        last_error = last_error or str(exc)
    if current is not None:
        if current.serial != original.serial:
            raise UnlockError(
                f"fastboot serial changed from {original.serial} to {current.serial}; "
                "refusing to continue on an ambiguous device"
            )
        if current.product != original.product:
            raise UnlockError(
                f"post-unlock device reports {current.product}, not original {original.product}"
            )
        if current.unlocked:
            return current
        last_error = f"{original.product} still reports unlock_status=false"
    raise UnlockError(
        f"the Show didn't come up in a checked TWRP within {POST_AMONET_TWRP_TIMEOUT_SECONDS // 60} minutes "
        f"(last state: {last_error or 'no recovery device seen'}). {KEEP_POWERED} "
        "If it is still writing, it can take longer. Check its screen before running anything else."
    )


def unlock_show(
    identity: DeviceIdentity,
    amonet_dir: Path,
    *,
    confirmation: str | None,
    run: Callable[..., FastbrickResult] = run_fastbrick_script,
    identify: Callable[[], DeviceIdentity] | None = None,
    adb_run: Callable = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    expected_hashes: Mapping[str, str] | None = None,
    say: Callable[[str], None] = print,
) -> UnlockResult:
    """Unlock one proven supported Show, or safely skip an already-unlocked one."""
    try:
        profile = profile_for_product(identity.product)
    except ValueError as exc:
        raise UnlockError(
            f"unlock wrapper received unsupported product {identity.product!r}; refusing Amonet"
        ) from exc
    if identity.unlocked:
        return UnlockResult(identity=identity, amonet_invoked=False)

    if not identity.lk_build_desc or not identity.lk_build_desc.strip():
        raise UnlockError(
            f"locked {profile.board} has no readable lk_build_desc; upstream warns that some "
            "firmware builds may be too new to unlock, so Jarvis Show refuses to run Amonet "
            "without a recorded bootloader build"
        )

    if profile.board == "crown":
        # Its pins are built in; a manifest may not swap them for others (GENBU-172).
        if expected_hashes is not None and {k: v.lower() for k, v in expected_hashes.items()} != AMONET_UNLOCK_SHA256:
            raise UnlockError("crown's Amonet unlock hashes are built in; --amonet-hashes can't replace them")
        expected_hashes = AMONET_UNLOCK_SHA256
    elif expected_hashes is None:
        raise UnlockError(
            f"{profile.board} Amonet bundle has not been cryptographically pinned in this checkout; "
            "supply a trusted board-specific hash manifest before unlock"
        )
    verify_amonet_unlock_assets(amonet_dir, expected_hashes=expected_hashes)

    if confirmation != profile.unlock_confirmation:
        raise UnlockError(
            f"locked {profile.board} requires exact confirmation phrase {profile.unlock_confirmation!r}"
        )

    _invoke_fastbrick(amonet_dir, run=run, say=say)
    if identify is None:
        identify = lambda: identify_show(expected_board=profile.board)
    unlocked = _wait_for_twrp(
        identity, profile.board, adb_run=adb_run, identify=identify, sleep=sleep, say=say,
    )
    return UnlockResult(identity=unlocked, amonet_invoked=True)


def unlock_crown(
    identity: DeviceIdentity,
    amonet_dir: Path,
    *,
    confirmation: str | None,
    run: Callable[..., FastbrickResult] = run_fastbrick_script,
    identify: Callable[[], DeviceIdentity] = identify_crown,
    adb_run: Callable = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    expected_hashes: Mapping[str, str] = AMONET_UNLOCK_SHA256,
    say: Callable[[str], None] = print,
) -> UnlockResult:
    """Compatibility wrapper for Crown callers/tests."""
    if identity.product != "CROWN":
        raise UnlockError(
            f"unlock wrapper received unsupported product {identity.product!r}; Crown wrapper requires CROWN"
        )
    return unlock_show(
        identity,
        amonet_dir,
        confirmation=confirmation,
        run=run,
        identify=identify,
        adb_run=adb_run,
        sleep=sleep,
        expected_hashes=expected_hashes,
        say=say,
    )
