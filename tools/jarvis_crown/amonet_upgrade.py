#!/usr/bin/env python3
"""Move a Checkers unlocked with Amonet 1.x to the 2.x layout, from TWRP.

Amonet 1.x left a microloader at the start of boot, and the install gate refuses to write over it. The
way out the Amonet author gives (XDA thread 4762900) is flashing the 2.x zip in TWRP. Its update-binary
rewrites the preloader (boot0), lk, tee1, tee2, expdb (kaeru), recovery and swdl, wipes misc and
reboots to recovery. This stage runs exactly that, and nothing else, under these conditions:

- the Amonet and Lineage zips match their pins in assets.py (an unpinned Amonet zip is refused);
- the zip's device.prop names the board and carries every binary the update-binary needs;
- the unit is the board in TWRP, as root, and boot really starts with the 1.x microloader;
- every partition the update-binary will resolve is the one expected, so the backup covers it;
- a verified backup of all of them exists (backup_recovery_state, under before-amonet2/);
- the operator typed the exact phrase.

Afterwards each written partition is compared with the zip's bytes. Then boot.img from the pinned
Lineage zip goes onto boot (mmcblk0p9, checked by name), so boot no longer holds the microloader and
the normal install gate can pass.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Callable
import zipfile

from jarvis_crown.assets import assets_for
from jarvis_crown.catalogue import profile_for_board
from jarvis_crown.device_gate import DeviceIdentity
from jarvis_crown.recovery import (
    RecoveryError,
    SubprocessRecoveryClient,
    _sha256,
    adb_recovery_serials,
    backup_recovery_state,
    boot_layout,
    twrp_device,
)
from jarvis_crown.timing import POLL_SECONDS, TWRP_REENUM_TIMEOUT_SECONDS

# The directories the update-binary searches, in its order; the first block device found wins.
BY_NAME_DIRS = (
    "/dev/block/by-name",
    "/dev/block/platform/soc/11230000.mmc/by-name",
    "/dev/block/platform/mtk-msdc.0/by-name",
    "/dev/block/platform/soc/by-name",
    "/dev/block/platform/*/by-name",
    "/dev/block/platform/*/*/by-name",
)

# What the update-binary writes: (names it looks up, the partition that must be, zip binary or None
# for the misc wipe). Only Checkers has been mapped on a real unit.
TARGETS = {
    "checkers": (
        (("lk_real",), "mmcblk0p3", "lk.bin"),
        (("tee2_real",), "mmcblk0p6", "tz.img"),
        (("tee1_real",), "mmcblk0p4", "tee-payload.bin"),
        (("expdb",), "mmcblk0p7", "checkers-kaeru.bin"),
        (("recovery",), "mmcblk0p10", "twrp.img"),
        (("swdl_real", "swdl"), "mmcblk0p11", "twrp.img"),
        (("MISC", "misc"), "mmcblk0p8", None),
    ),
}
BOOT_PARTITION = "mmcblk0p9"
UPDATE_BINARY = "META-INF/com/google/android/update-binary"
DEVICE_ZIP = "/tmp/jarvis-amonet.zip"
DEVICE_BOOT = "/tmp/jarvis-lineage-boot.img"
INSTALL_TIMEOUT_SECONDS = 300


class UpgradeError(RuntimeError):
    """Raised when the Amonet upgrade cannot be proven safe or did not land."""


@dataclass(frozen=True)
class UpgradeResult:
    upgraded: bool
    backup: Path | None


def confirmation_phrase(board: str) -> str:
    return f"UPGRADE AMONET {board.upper()}"


def _pin(board: str, kind: str):
    for asset in assets_for(board):
        if asset.kind == kind:
            return asset
    raise UpgradeError(f"no {kind} asset is listed for {board}")


def check_amonet_zip(board: str, path: Path) -> dict[str, bytes]:
    """The pinned zip's binaries by name, after checking it is the right package for the board."""
    asset = _pin(board, "amonet")
    if not asset.sha256:
        raise UpgradeError(f"{asset.name} is not pinned yet; review it with tools/fetch-show-assets.py first")
    if not path.is_file() or path.stat().st_size != asset.size or _sha256(path) != asset.sha256:
        raise UpgradeError(f"{path} is not the pinned {asset.name}")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if UPDATE_BINARY not in names or "amonet/device.prop" not in names:
            raise UpgradeError(f"{asset.name} is not a TWRP-flashable Amonet package")
        prop = z.read("amonet/device.prop").decode("utf-8", "replace")
        if not re.search(rf"^DEVICE={re.escape(board)}\s*$", prop, re.M):
            raise UpgradeError(f"{asset.name} device.prop does not name {board}")
        wanted = {"preloader.img"} | {b for _, _, b in TARGETS[board] if b}
        binaries = {}
        for name in sorted(wanted):
            member = f"amonet/bin/{name}"
            if member not in names:
                raise UpgradeError(f"{asset.name} is missing {member}")
            binaries[name] = z.read(member)
            if not binaries[name]:
                raise UpgradeError(f"{asset.name} has an empty {member}")
    return binaries


def lineage_boot_image(board: str, path: Path) -> bytes:
    asset = _pin(board, "lineage")
    if not path.is_file() or path.stat().st_size != asset.size or _sha256(path) != asset.sha256:
        raise UpgradeError(f"{path} is not the pinned {asset.name}")
    with zipfile.ZipFile(path) as z:
        data = z.read("boot.img")
    if not data.startswith(b"ANDROID!"):
        raise UpgradeError(f"boot.img in {asset.name} is not an Android boot image")
    return data


def resolve_targets(client, board: str) -> None:
    """Prove every name the update-binary looks up lands on the partition the backup covers."""
    dirs = " ".join(BY_NAME_DIRS)
    for names, expected, _ in TARGETS[board]:
        script = (
            f"for n in {' '.join(names)}; do for d in {dirs}; do "
            'if [ -b "$d/$n" ]; then readlink -f "$d/$n"; exit 0; fi; done; done; echo MISSING'
        )
        got = client.shell(f"sh -c {shlex.quote(script)}").strip()
        if got != f"/dev/block/{expected}":
            raise UpgradeError(f"{'/'.join(names)} resolves to {got}, expected /dev/block/{expected}; nothing was written")
    if client.shell("test -b /dev/block/mmcblk0boot0 && test -e /sys/block/mmcblk0boot0/force_ro && echo OK") != "OK":
        raise UpgradeError("mmcblk0boot0 or its force_ro switch is missing; nothing was written")
    name = client.shell(f"grep PARTNAME /sys/class/block/{BOOT_PARTITION}/uevent | cut -d= -f2").strip()
    if name != "boot":
        raise UpgradeError(f"{BOOT_PARTITION} is named {name!r}, not boot; nothing was written")


def check_fit(client, board: str, binaries: dict[str, bytes], boot: bytes) -> None:
    """The update-binary dd's without looking at sizes; a short write to lk or tee would break the chain."""
    writes = [("mmcblk0boot0", "preloader.img", len(binaries["preloader.img"])), (BOOT_PARTITION, "boot.img", len(boot))]
    writes += [(part, binary, len(binaries[binary])) for _, part, binary in TARGETS[board] if binary]
    for part, label, length in writes:
        raw = client.shell(f"cat /sys/class/block/{part}/size").strip()
        if not raw.isdigit() or int(raw) * 512 < length:
            raise UpgradeError(f"{label} ({length} bytes) does not fit {part} ({raw or '?'} sectors); nothing was written")


def _prefix_hash(client, block: str, length: int) -> str:
    out = client.shell(f"head -c {length} {block} | sha256sum").split()
    if not out or not re.fullmatch(r"[0-9a-f]{64}", out[0]):
        raise UpgradeError(f"could not hash {block}")
    return out[0]


def verify_written(client, board: str, binaries: dict[str, bytes]) -> None:
    checks = [("/dev/block/mmcblk0boot0", binaries["preloader.img"])]
    for _, part, binary in TARGETS[board]:
        checks.append((f"/dev/block/{part}", binaries[binary] if binary else b"\0" * 4096))
    for block, data in checks:
        if _prefix_hash(client, block, len(data)) != hashlib.sha256(data).hexdigest():
            raise UpgradeError(f"{block} does not hold what the Amonet zip should have written")


class AdbUpgradeClient(SubprocessRecoveryClient):
    def push(self, local: Path, remote: str) -> None:
        result = subprocess.run(["adb", "-s", self.serial, "push", str(local), remote],
                                capture_output=True, text=True, timeout=300, check=False)
        if result.returncode != 0:
            raise UpgradeError(f"adb push {local.name} failed: {result.stderr.strip() or result.returncode}")

    def install_zip(self, remote: str) -> str:
        """twrp install; the Amonet update-binary reboots at the end, so a dropped link is expected."""
        try:
            result = subprocess.run(["adb", "-s", self.serial, "shell", f"twrp install {remote}"],
                                    capture_output=True, text=True, timeout=INSTALL_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            return "timed out"
        return (result.stdout + result.stderr).strip()

    def boot_id(self) -> str | None:
        try:
            return self.shell("cat /proc/sys/kernel/random/boot_id").strip() or None
        except RecoveryError:
            return None

    def in_recovery(self) -> bool:
        try:
            return self.serial in adb_recovery_serials()
        except RecoveryError:
            return False


# The update-binary sleeps 5 s and reboots. Still up on the same boot this long after twrp install
# returned means it stopped before the end.
NO_REBOOT_SECONDS = 60


def wait_for_reboot(client, previous_boot_id: str, output: str, *, timeout: float = TWRP_REENUM_TIMEOUT_SECONDS,
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
    start = clock()
    while clock() - start < timeout:
        current = client.boot_id() if client.in_recovery() else None
        if current and current != previous_boot_id:
            return
        if current == previous_boot_id and clock() - start >= NO_REBOOT_SECONDS:
            raise UpgradeError("TWRP finished the Amonet zip without rebooting, so its update-binary stopped "
                               f"early. TWRP said: {output[-400:] or 'nothing'}")
        sleep(POLL_SECONDS)
    raise UpgradeError("the Show did not come back in TWRP after the Amonet flash; leave it powered and check the screen")


def upgrade_amonet(
    client,
    board: str,
    amonet_zip: Path,
    lineage_zip: Path,
    backup_root: Path,
    *,
    confirm: Callable[[str], str],
    progress: Callable[[str], None] = lambda _: None,
    wait: Callable = wait_for_reboot,
) -> UpgradeResult:
    profile = profile_for_board(board)
    if board not in TARGETS:
        raise UpgradeError(f"the Amonet upgrade has only been mapped for {', '.join(TARGETS)}")
    binaries = check_amonet_zip(board, amonet_zip)
    boot = lineage_boot_image(board, lineage_zip)

    try:
        expected = twrp_device(board)
    except RecoveryError as exc:
        raise UpgradeError(str(exc)) from None
    product = client.shell("getprop ro.product.device").strip().lower()
    if product != expected:
        raise UpgradeError(f"TWRP reports ro.product.device={product or 'unknown'}, not {expected}")
    if "uid=0" not in client.shell("id"):
        raise UpgradeError("TWRP adb shell is not root")
    layout = boot_layout(client.shell)
    if layout == "PLAIN":
        progress("boot has no Amonet 1.x microloader; nothing to upgrade")
        return UpgradeResult(upgraded=False, backup=None)
    if layout != "AMONET1":
        raise UpgradeError(f"cannot read the boot layout (probe returned {layout!r}); nothing was written")
    resolve_targets(client, board)
    check_fit(client, board, binaries, boot)

    progress("backing up every partition the upgrade touches")
    identity = DeviceIdentity(client.serial, profile.fastboot_product, True, None)
    backup = backup_recovery_state(client, backup_root / "before-amonet2", fastboot_identity=identity)

    phrase = confirmation_phrase(board)
    if confirm(phrase) != phrase:
        raise UpgradeError("confirmation phrase did not match; nothing was written")

    try:
        progress("pushing the pinned Amonet zip")
        client.push(amonet_zip, DEVICE_ZIP)
        if client.shell(f"sha256sum {DEVICE_ZIP}").split()[0] != _pin(board, "amonet").sha256:
            raise UpgradeError("the zip changed on its way to the Show; nothing was written")
        before = client.boot_id()
        if not before:
            raise UpgradeError("cannot read the TWRP boot id; nothing was written")

        progress("flashing Amonet 2.x in TWRP (do not unplug)")
        output = client.install_zip(DEVICE_ZIP)
        wait(client, before, output)
        verify_written(client, board, binaries)

        progress("putting the pinned Lineage boot image on boot")
        staged = backup_root / "before-amonet2" / "lineage-boot.img"
        staged.write_bytes(boot)
        client.push(staged, DEVICE_BOOT)
        digest = hashlib.sha256(boot).hexdigest()
        if client.shell(f"sha256sum {DEVICE_BOOT}").split()[0] != digest:
            raise UpgradeError("boot.img changed on its way to the Show; boot was not written")
        client.shell(f"dd if={DEVICE_BOOT} of=/dev/block/{BOOT_PARTITION} bs=4096 && sync && rm -f {DEVICE_BOOT}")
        if _prefix_hash(client, f"/dev/block/{BOOT_PARTITION}", len(boot)) != digest:
            raise UpgradeError("boot does not hold the Lineage boot image after writing it")
        if boot_layout(client.shell) != "PLAIN":
            raise UpgradeError("boot still does not read as a plain image")
    except (UpgradeError, RecoveryError) as exc:
        raise UpgradeError(f"{exc}. The state before the upgrade is backed up in {backup.path}") from exc
    progress("Amonet 2.x is in place and boot is plain" + (f" (TWRP said: {output[-200:]})" if output else ""))
    return UpgradeResult(upgraded=True, backup=backup.path)
