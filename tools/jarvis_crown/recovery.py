#!/usr/bin/env python3
"""TWRP handoff and atomic recovery backup for Jarvis Crown v1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable, Protocol

from jarvis_crown.boards import profile_for_board, profile_for_product
from jarvis_crown.device_gate import DeviceIdentity, SUPPORTED_PRODUCTS, identify_crown, identify_show
from jarvis_crown.timing import (COMMAND_TIMEOUT_SECONDS, POLL_SECONDS, TWRP_REENUM_TIMEOUT_SECONDS)

TWRP_SHA256 = "b6b1446436de27cf860ebc170d4e3dffe3068ab90396ec915a123589d0d6f7d8"
TWRP_SHA256_BY_BOARD: dict[str, str | None] = {
    "crown": TWRP_SHA256,
    # bin/twrp.img of the pinned amonet-checkers-v2.0.1.zip (jarvis_crown/assets.py), 16.1.0-20260812
    "checkers": "dcc75bb651894c343039d45af73c4d900594762f6f55187c1ebdaf12d0ae59f5",
    # bin/twrp.img of the pinned amonet-cronos-v2.0.1.zip, 16.1.0-20260812
    "cronos": "09a72dab093e9f4d444733bd4517af09444a0904a9a5243277df0ecd263873d8",
}
SMALL_PARTITIONS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 14, 15)
BOOT_AREAS = ("boot0", "boot1")
TWRP_WAIT_INTERVAL_SECONDS = POLL_SECONDS
TWRP_WAIT_ATTEMPTS = int(TWRP_REENUM_TIMEOUT_SECONDS / TWRP_WAIT_INTERVAL_SECONDS)
# A TWRP that has only just come up can hand back an empty read (adb exec-out passes no exit status on),
# seen on a cronos right after the TWRP handoff. Reading again is harmless; a short read never counts.
READ_ATTEMPTS = 5


class RecoveryError(RuntimeError):
    """Raised when recovery handoff/backup cannot be proven safe."""


@dataclass(frozen=True)
class RecoverySession:
    adb_serial: str
    twrp_flashed: bool


@dataclass(frozen=True)
class BackupResult:
    path: Path
    files: int
    reused: bool


class RecoveryClient(Protocol):
    serial: str

    def shell(self, command: str) -> str: ...
    def dump_block(self, block: str, destination: Path) -> None: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_text(argv: list[str], *, run: Callable = subprocess.run) -> subprocess.CompletedProcess:
    try:
        return run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecoveryError(f"command timed out: {' '.join(argv)}") from exc
    except OSError as exc:
        raise RecoveryError(f"cannot execute {' '.join(argv)}: {exc}") from exc


def _combined(result: subprocess.CompletedProcess) -> str:
    return "\n".join(x for x in (result.stdout, result.stderr) if x)


def adb_recovery_serials(*, run: Callable = subprocess.run) -> list[str]:
    result = _run_text(["adb", "devices"], run=run)
    if result.returncode != 0:
        raise RecoveryError(f"adb devices failed: {_combined(result).strip() or result.returncode}")
    found: list[str] = []
    for raw in result.stdout.splitlines():
        fields = raw.strip().split()
        if len(fields) >= 2 and fields[1].lower() == "recovery":
            found.append(fields[0])
    return found


def _adb_shell(serial: str, command: str, *, run: Callable = subprocess.run) -> str:
    result = _run_text(["adb", "-s", serial, "shell", command], run=run)
    if result.returncode != 0:
        raise RecoveryError(
            f"adb shell failed on {serial}: {_combined(result).strip() or result.returncode}"
        )
    return result.stdout.strip()


BOOT_LAYOUT_PROBE = (
    "tmp=/tmp/jarvis-boot-prefix.$$; "
    "if dd if=/dev/block/mmcblk0p9 of=$tmp bs=512 count=2 2>/dev/null; then "
    "if grep -qa microloader $tmp; then echo AMONET1; else echo PLAIN; fi; "
    "else echo READ-FAIL; fi; rm -f $tmp"
)


def boot_layout(shell: Callable[[str], str]) -> str:
    """AMONET1 when boot starts with the Amonet 1.x microloader, PLAIN when not, READ-FAIL otherwise."""
    return shell(BOOT_LAYOUT_PROBE).strip()


def verify_twrp_board(serial: str, board: str, *, run: Callable = subprocess.run) -> None:
    product = _adb_shell(serial, "getprop ro.product.device", run=run).strip().lower()
    if product != board:
        raise RecoveryError(
            f"TWRP device reports ro.product.device={product or 'unknown'}, not {board}"
        )
    ident = _adb_shell(serial, "id", run=run)
    if "uid=0" not in ident:
        raise RecoveryError("TWRP adb shell is not root; refusing partition backup")
    block = _adb_shell(serial, "test -b /dev/block/mmcblk0p9 && echo OK", run=run)
    if block != "OK":
        raise RecoveryError(
            f"expected {board} boot block /dev/block/mmcblk0p9 is unavailable"
        )

    # Amonet 1.x and 2.x use mutually incompatible boot layouts. 1.x put a
    # microloader header in the first KiB of boot; a plain Jarvis/Lineage boot
    # image must never be flashed until that retired layout is gone. This is
    # especially important for an already-unlocked unit, where Jarvis skips
    # its own pinned Amonet 2.x stage.
    layout = boot_layout(lambda command: _adb_shell(serial, command, run=run))
    if layout == "AMONET1":
        raise RecoveryError(
            "legacy Amonet 1.x boot microloader detected; its boot layout is incompatible "
            "with Jarvis Show. " + amonet1_upgrade_hint(board)
        )
    if layout != "PLAIN":
        raise RecoveryError(
            f"cannot prove a modern/plain boot layout (probe returned {layout!r}); "
            "refusing any later Jarvis boot write"
        )


def amonet1_upgrade_hint(board: str) -> str:
    """Where a unit still on Amonet 1.x goes next. amonet-upgrade only knows the boards whose
    partitions were mapped on a real unit (GENBU-206); pointing anything else at it is a dead end."""
    # Imported here: amonet_upgrade imports this module.
    from jarvis_crown.amonet_upgrade import TARGETS
    from jarvis_crown.assets import XDA_CRONOS_THREAD

    if board in TARGETS:
        return (
            f"Upgrade it first with: jarvis-show.py amonet-upgrade --board {board} "
            "(tools/fetch-show-assets.py picks up and checks the Amonet zip)"
        )
    if board == "cronos":
        return (
            "amonet-upgrade has no map for cronos. The XDA thread updates an unlocked cronos by "
            "flashing the new amonet-cronos zip in TWRP (tools/fetch-show-assets.py picks up and "
            "checks it). Nobody here has done that from 1.x, and the zip rewrites the bootloader on a "
            f"board with no BROM, so read the thread first: {XDA_CRONOS_THREAD} Then run this again; "
            "this check runs before anything is written."
        )
    return (
        f"amonet-upgrade has no map for {board}. Update Amonet the way the board's XDA thread says, "
        "then run this again."
    )


def verify_twrp_crown(serial: str, *, run: Callable = subprocess.run) -> None:
    """Compatibility wrapper for Crown tests/callers."""
    verify_twrp_board(serial, "crown", run=run)


def identify_recovery_show(expected_board: str, *, run: Callable = subprocess.run) -> DeviceIdentity:
    """Prove one already-running TWRP instance belongs to the requested board.

    This is only suitable for resuming the install flow after it already reached
    recovery. The caller must supply the expected board; recovery properties are
    never used to auto-select a destructive board profile.
    """
    profile = profile_for_board(expected_board)
    serial = _single_recovery(run=run)
    if serial is None:
        raise RecoveryError("no TWRP recovery device detected")
    verify_twrp_board(serial, profile.board, run=run)
    return DeviceIdentity(
        serial=serial,
        product=profile.fastboot_product,
        unlocked=True,
        lk_build_desc=None,
    )


def _pinned_twrp(amonet_dir: Path, board: str, expected_sha256: str | None = None) -> Path:
    path = amonet_dir / "amonet" / "bin" / "twrp.img"
    if not path.is_file():
        raise RecoveryError(f"pinned TWRP image missing: {path}")
    expected = expected_sha256 or (TWRP_SHA256 if board == "crown" else TWRP_SHA256_BY_BOARD.get(board))
    if expected is None:
        raise RecoveryError(
            f"{board} TWRP image has not been cryptographically pinned in this checkout"
        )
    actual = _sha256(path)
    if actual != expected:
        raise RecoveryError(
            f"TWRP image hash mismatch for {board} (expected {expected}, got {actual})"
        )
    return path


def _single_recovery(*, run: Callable = subprocess.run) -> str | None:
    serials = adb_recovery_serials(run=run)
    if len(serials) > 1:
        raise RecoveryError("multiple adb recovery devices detected; refusing ambiguous TWRP target")
    return serials[0] if serials else None


def ensure_twrp(
    identity: DeviceIdentity,
    amonet_dir: Path,
    *,
    fastboot_run: Callable = subprocess.run,
    adb_run: Callable = subprocess.run,
    identify: Callable[[], DeviceIdentity] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    wait_attempts: int = TWRP_WAIT_ATTEMPTS,
    expected_twrp_sha256: str | None = None,
) -> RecoverySession:
    """Use an existing board-matched TWRP, otherwise flash only recovery+swdl."""
    try:
        profile = profile_for_product(identity.product)
    except ValueError as exc:
        raise RecoveryError(
            f"TWRP stage received unsupported product {identity.product!r}"
        ) from exc
    if not identity.unlocked:
        raise RecoveryError(
            f"TWRP stage requires a proven unlocked {profile.fastboot_product} identity"
        )

    existing = _single_recovery(run=adb_run)
    if existing:
        verify_twrp_board(existing, profile.board, run=adb_run)
        return RecoverySession(adb_serial=existing, twrp_flashed=False)

    if identify is None:
        identify = lambda: identify_show(expected_board=profile.board)
    current = identify()
    if current.serial != identity.serial or current.product != identity.product or not current.unlocked:
        raise RecoveryError("fastboot identity changed before TWRP flash; refusing to write")

    image = _pinned_twrp(amonet_dir, profile.board, expected_twrp_sha256)
    commands = (
        ["fastboot", "-s", identity.serial, "flash", "recovery", str(image)],
        ["fastboot", "-s", identity.serial, "flash", "swdl", str(image)],
        ["fastboot", "-s", identity.serial, "reboot", "recovery"],
    )
    for argv in commands:
        result = _run_text(argv, run=fastboot_run)
        if result.returncode != 0:
            raise RecoveryError(
                f"TWRP handoff failed at {' '.join(argv[3:])}: "
                f"{_combined(result).strip() or result.returncode}; stop and recover before retry"
            )

    for attempt in range(wait_attempts):
        serial = _single_recovery(run=adb_run)
        if serial:
            verify_twrp_board(serial, profile.board, run=adb_run)
            return RecoverySession(adb_serial=serial, twrp_flashed=True)
        if attempt + 1 < wait_attempts:
            sleep(TWRP_WAIT_INTERVAL_SECONDS)
    raise RecoveryError(
        f"{profile.board} did not appear in TWRP after recovery+swdl flash; stop before further writes"
    )


class SubprocessRecoveryClient:
    def __init__(self, serial: str, *, run: Callable = subprocess.run):
        self.serial = serial
        self._run = run

    def shell(self, command: str) -> str:
        return _adb_shell(self.serial, command, run=self._run)

    def dump_block(self, block: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("wb") as output:
                result = subprocess.run(
                    ["adb", "-s", self.serial, "exec-out", "sh", "-c", f"dd if={block} bs=4096 2>/dev/null"],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=180,
                    check=False,
                )
                output.flush()
                os.fsync(output.fileno())
        except subprocess.TimeoutExpired as exc:
            raise RecoveryError(f"timed out while backing up {block}") from exc
        except OSError as exc:
            raise RecoveryError(f"could not back up {block}: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise RecoveryError(f"reading {block} failed: {detail or result.returncode}")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned[:64] or "part"


def _block_size(client: RecoveryClient, sys_name: str) -> int:
    raw = client.shell(f"cat /sys/class/block/{sys_name}/size").strip()
    try:
        sectors = int(raw)
    except ValueError as exc:
        raise RecoveryError(f"invalid size for {sys_name}: {raw!r}") from exc
    size = sectors * 512
    if size <= 0:
        raise RecoveryError(f"zero/invalid size for {sys_name}")
    return size


def _device_hash(client: RecoveryClient, block: str) -> str:
    raw = client.shell(f"sha256sum {block}").strip().split()
    if not raw or not re.fullmatch(r"[0-9a-fA-F]{64}", raw[0]):
        raise RecoveryError(f"device did not return a valid SHA-256 for {block}")
    return raw[0].lower()


def _backup_one(
    client: RecoveryClient,
    *,
    block: str,
    sys_name: str,
    filename: str,
    destination: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    expected_size = _block_size(client, sys_name)
    out = destination / filename
    for attempt in range(READ_ATTEMPTS):
        client.dump_block(block, out)
        actual_size = out.stat().st_size
        if actual_size >= expected_size or attempt + 1 == READ_ATTEMPTS:
            break
        sleep(POLL_SECONDS)
    if actual_size != expected_size:
        raise RecoveryError(
            f"backup size mismatch for {block}: got {actual_size}, expected {expected_size}"
        )
    host_hash = _sha256(out)
    device_hash = _device_hash(client, block)
    if host_hash != device_hash:
        raise RecoveryError(
            f"backup hash mismatch for {block}: host {host_hash}, device {device_hash}"
        )
    return host_hash, actual_size


def verify_backup(path: Path, *, expected_product: str | None = None) -> BackupResult:
    path = Path(path)
    sums_path = path / "SHA256SUMS"
    manifest_path = path / "manifest.json"
    if not sums_path.is_file() or not manifest_path.is_file():
        raise RecoveryError(f"backup is incomplete: {path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"backup manifest is invalid: {exc}") from exc
    product = str(manifest.get("product", "")).upper()
    if product not in SUPPORTED_PRODUCTS or manifest.get("complete") is not True:
        raise RecoveryError("backup manifest does not certify a complete supported Jarvis Show backup")
    if expected_product is not None and product != expected_product.upper():
        raise RecoveryError(
            f"backup manifest is for product {product}, expected {expected_product.upper()}"
        )

    entries: dict[str, str] = {}
    for raw in sums_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            digest, name = raw.split("  ", 1)
        except ValueError as exc:
            raise RecoveryError(f"invalid SHA256SUMS line: {raw!r}") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or name in entries:
            raise RecoveryError(f"invalid/duplicate SHA256SUMS entry: {raw!r}")
        entries[name] = digest

    expected_count = len(SMALL_PARTITIONS) + len(BOOT_AREAS)
    if len(entries) != expected_count or manifest.get("files") != expected_count:
        raise RecoveryError(
            f"backup has {len(entries)} files, expected {expected_count}"
        )
    covered = set()
    for name, digest in entries.items():
        file_path = path / name
        if not file_path.is_file():
            raise RecoveryError(f"backup file missing: {name}")
        if _sha256(file_path) != digest:
            raise RecoveryError(f"backup file hash mismatch: {name}")
        match = re.match(r"^p(\d+)-", name)
        if match:
            covered.add(int(match.group(1)))
    if covered != set(SMALL_PARTITIONS):
        raise RecoveryError(
            f"backup partition coverage mismatch: {sorted(covered)}"
        )
    for boot in BOOT_AREAS:
        if f"mmcblk0{boot}.img" not in entries:
            raise RecoveryError(f"backup missing eMMC boot area {boot}")
    return BackupResult(path=path, files=expected_count, reused=True)


def backup_recovery_state(
    client: RecoveryClient,
    backup_root: Path,
    *,
    fastboot_identity: DeviceIdentity,
) -> BackupResult:
    """Atomically save and verify recovery-critical partitions before install writes."""
    if fastboot_identity.product not in SUPPORTED_PRODUCTS or not fastboot_identity.unlocked:
        raise RecoveryError("backup requires a proven unlocked supported Jarvis Show identity")

    final = backup_root / "partitions"
    if final.exists():
        return verify_backup(final, expected_product=fastboot_identity.product)

    partial = backup_root / "partitions.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True, exist_ok=False)

    entries: list[tuple[str, str, int]] = []
    try:
        for number in SMALL_PARTITIONS:
            sys_name = f"mmcblk0p{number}"
            block = f"/dev/block/{sys_name}"
            name = client.shell(
                f"grep PARTNAME /sys/class/block/{sys_name}/uevent | cut -d= -f2"
            ) or "part"
            filename = f"p{number}-{_safe_name(name)}.img"
            digest, size = _backup_one(
                client,
                block=block,
                sys_name=sys_name,
                filename=filename,
                destination=partial,
            )
            entries.append((digest, filename, size))

        for boot in BOOT_AREAS:
            sys_name = f"mmcblk0{boot}"
            filename = f"{sys_name}.img"
            digest, size = _backup_one(
                client,
                block=f"/dev/block/{sys_name}",
                sys_name=sys_name,
                filename=filename,
                destination=partial,
            )
            entries.append((digest, filename, size))

        (partial / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for digest, name, _ in entries),
            encoding="utf-8",
        )
        manifest = {
            "complete": True,
            "product": fastboot_identity.product,
            "fastboot_serial": fastboot_identity.serial,
            "adb_serial": client.serial,
            "files": len(entries),
            "bytes": sum(size for _, _, size in entries),
            "partitions": list(SMALL_PARTITIONS),
            "boot_areas": list(BOOT_AREAS),
        }
        (partial / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verify_backup(partial, expected_product=fastboot_identity.product)
        backup_root.mkdir(parents=True, exist_ok=True)
        partial.replace(final)
        verified = verify_backup(final, expected_product=fastboot_identity.product)
        return BackupResult(path=verified.path, files=verified.files, reused=False)
    except Exception:
        # Never leave a partial backup looking reusable.
        if partial.exists():
            shutil.rmtree(partial)
        raise
