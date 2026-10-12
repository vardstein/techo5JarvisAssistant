#!/usr/bin/env python3
"""Fail-closed handoff into TECHO5's proven Show slot-store installer.

Jarvis Show shares one rootfs across crown/checkers. Boot images remain strictly
board-specific and must be hash-pinned before any destructive handoff.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from typing import Callable

from jarvis_crown.boards import profile_for_board
from jarvis_crown.recovery import RecoveryError, verify_backup

CHECKERS_BOOT_SHA256 = "6fd696dce2592d2237a432e23ed7c032f1643bd1fe475d3094eccd9f7d2079b2"
CROWN_BOOT_SHA256 = "e00bed964d9b2ce31df152d183d2aeb45a7839cc5f7460a93dd97d70715ad1b7"
BOOT_SHA256_BY_BOARD: dict[str, str | None] = {
    # Upstream TECHO5 v1.0.1 techo5-boot-crown-v1.0.1.img, for the same reasons as checkers below. Its
    # ramdisk is v0.8.0's byte for byte; the kernel only adds the quick-tap unmute fix (upstream #17).
    "crown": CROWN_BOOT_SHA256,
    # Upstream TECHO5 v1.0.1 techo5-boot-checkers-v1.0.1.img: listed in the v1.0.1 manifest signed by
    # the upstream release key, kernel 4.9.337-g8d928c5176cc (the Jarvis vendor-module ABI).
    "checkers": CHECKERS_BOOT_SHA256,
}
ROOTFS_MARKER = "etc/jarvis-show-release.json"
PROFILE_ID = "genbu-show-v1"
SUPPORTED_BOARDS = ("crown", "checkers")
UPDATE_CHANNELS = ("stable", "staging", "dev")  # echod/internal/update/channel.go
FINAL_CONFIRM_PHRASE = "ERASE"  # compatibility alias
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InstallError(RuntimeError):
    """Raised before/around the destructive install handoff."""


@dataclass(frozen=True)
class RootfsIdentity:
    product: str
    boards: tuple[str, ...]
    version: str
    sha256: str

    @property
    def board(self) -> str:
        """Legacy single-board view; shared images report 'shared'."""
        return self.boards[0] if len(self.boards) == 1 else "shared"


@dataclass(frozen=True)
class InstallPlan:
    board: str
    argv: tuple[str, ...]
    rootfs: RootfsIdentity
    backup: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_boot(path: Path, board: str, *, expected_sha256: str | None = None) -> None:
    profile = profile_for_board(board)
    if not path.is_file():
        raise InstallError(f"{profile.board} boot image missing: {path}")
    expected = expected_sha256 or BOOT_SHA256_BY_BOARD.get(profile.board)
    if expected is None:
        raise InstallError(
            f"{profile.board} boot image has not been cryptographically pinned in this checkout"
        )
    if not SHA256_RE.fullmatch(expected.lower()):
        raise InstallError(f"invalid {profile.board} boot SHA-256 pin")
    actual = _sha256(path)
    if actual != expected.lower():
        raise InstallError(
            f"{profile.board} boot image is not the pinned known-good image "
            f"(expected {expected.lower()}, got {actual})"
        )


def verify_crown_boot(path: Path) -> None:
    """Compatibility wrapper for prior Crown tests/callers."""
    verify_boot(path, "crown")


def _read_marker(archive: tarfile.TarFile, marker_name: str) -> bytes | None:
    for member in archive.getmembers():
        normalized = member.name.lstrip("./")
        if normalized != marker_name:
            continue
        if not member.isfile() or member.size > 4096:
            raise InstallError(f"rootfs {marker_name} marker is not a small regular file")
        handle = archive.extractfile(member)
        if handle is None:
            raise InstallError(f"cannot read {marker_name} from rootfs")
        return handle.read(4097)
    return None


def verify_jarvis_rootfs(path: Path, expected_sha256: str, *, board: str = "crown") -> RootfsIdentity:
    """Require caller-pinned bytes plus a Jarvis Show product marker allowing board."""
    profile = profile_for_board(board)
    expected = expected_sha256.strip().lower()
    if not SHA256_RE.fullmatch(expected):
        raise InstallError("rootfs SHA-256 must be exactly 64 lowercase hex characters")
    if not path.is_file():
        raise InstallError(f"Jarvis Show rootfs missing: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise InstallError(f"rootfs SHA-256 mismatch (expected {expected}, got {actual})")

    try:
        with tarfile.open(path, mode="r:*") as archive:
            raw = _read_marker(archive, ROOTFS_MARKER)
            if raw is None:
                raise InstallError(f"rootfs does not contain a regular {ROOTFS_MARKER} marker")
    except InstallError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise InstallError(f"rootfs is not a readable tar archive: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid Jarvis rootfs marker: {exc}") from exc

    product = payload.get("product")
    raw_boards = payload.get("boards")
    if product != PROFILE_ID or not isinstance(raw_boards, list):
        raise InstallError(
            f"rootfs marker is not {PROFILE_ID!r} with an explicit boards list"
        )
    boards = tuple(str(value).lower() for value in raw_boards)
    if not boards or any(value not in SUPPORTED_BOARDS for value in boards):
        raise InstallError(f"rootfs marker has invalid supported boards: {boards!r}")
    if len(set(boards)) != len(boards):
        raise InstallError("rootfs marker contains duplicate board entries")

    if profile.board not in boards:
        raise InstallError(
            f"rootfs supports {boards!r}, not requested board {profile.board!r}"
        )
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 64:
        raise InstallError("rootfs marker has no valid Jarvis Show version")
    return RootfsIdentity(
        product=str(product),
        boards=boards,
        version=version.strip(),
        sha256=actual,
    )


def verify_install_backup(backup_root: Path, *, adb_serial: str, board: str = "crown") -> Path:
    """Require J20's complete atomic backup and bind it to this board/recovery serial."""
    profile = profile_for_board(board)
    path = backup_root / "partitions"
    try:
        verify_backup(path, expected_product=profile.fastboot_product)
    except RecoveryError as exc:
        raise InstallError(f"recovery backup is not complete/valid: {exc}") from exc
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read recovery backup manifest: {exc}") from exc
    recorded = manifest.get("adb_serial")
    if recorded != adb_serial:
        raise InstallError(
            f"recovery backup belongs to adb serial {recorded!r}, not current recovery serial {adb_serial!r}"
        )
    return path


def make_install_plan(
    *,
    repo_root: Path,
    adb_serial: str,
    name: str,
    backup_root: Path,
    work_dir: Path,
    boot_image: Path,
    rootfs: Path,
    rootfs_sha256: str,
    confirmation: str,
    board: str = "crown",
    boot_sha256: str | None = None,
    wifi: str | None = None,
    wifi_passphrase_file: Path | None = None,
    ssh_key: Path | None = None,
    ha_url: str | None = None,
    ha_token_file: Path | None = None,
    dashcast: str | None = None,
    dashcast_key_file: Path | None = None,
    music_assistant: str | None = None,
    root_password_file: Path | None = None,
    update_channel: str | None = None,
) -> InstallPlan:
    profile = profile_for_board(board)
    if confirmation != profile.install_confirmation:
        raise InstallError(
            f"final confirmation must be exactly: {profile.install_confirmation}"
        )
    if not adb_serial.strip():
        raise InstallError("ADB recovery serial is required")
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 64 or any(ord(ch) < 32 for ch in clean_name):
        raise InstallError("device name must be 1-64 printable characters")

    verify_boot(boot_image, profile.board, expected_sha256=boot_sha256)
    rootfs_identity = verify_jarvis_rootfs(rootfs, rootfs_sha256, board=profile.board)
    backup = verify_install_backup(backup_root, adb_serial=adb_serial, board=profile.board)

    installer = repo_root / "tools" / "install-show.py"
    if not installer.is_file():
        raise InstallError(f"upstream slot-store installer missing: {installer}")
    if backup_root.name != adb_serial:
        raise InstallError(
            f"backup root must be backups/<adb-serial>; got {backup_root} for {adb_serial}"
        )

    argv = [
        sys.executable,
        str(installer),
        "--serial", adb_serial,
        "--name", clean_name,
        "--boot", str(boot_image.resolve()),
        "--rootfs", str(rootfs.resolve()),
        "--jarvis-show-prestaged",
        "--jarvis-show-board", profile.board,
        "--jarvis-show-version", rootfs_identity.version,
        "--amazon-logo",
        "--force",
        "--backups", str(backup_root.parent.resolve()),
        "--work", str(work_dir.resolve()),
    ]
    if wifi:
        argv.extend(["--wifi", wifi])
        if wifi_passphrase_file:
            argv.extend(["--wifi-passphrase-file", str(wifi_passphrase_file.resolve())])
    elif wifi_passphrase_file:
        raise InstallError("--wifi-passphrase-file requires a Wi-Fi network name")
    if ssh_key:
        argv.extend(["--ssh-key", str(ssh_key.resolve())])
    # What the Show is ready with at first boot. Secrets travel as files, never as arguments.
    if bool(ha_url) != bool(ha_token_file):
        raise InstallError("Home Assistant access needs both its address and a token file")
    if ha_url and ha_token_file:
        argv.extend(["--ha-url", ha_url, "--ha-token-file", str(ha_token_file)])
    if bool(dashcast) != bool(dashcast_key_file):
        raise InstallError("the DashCast server needs both its address and a key file")
    if dashcast and dashcast_key_file:
        argv.extend(["--dashcast", dashcast, "--dashcast-key-file", str(dashcast_key_file)])
    if music_assistant:
        argv.extend(["--music-assistant", music_assistant])
    if root_password_file:
        argv.extend(["--root-password-file", str(root_password_file)])
    if update_channel:
        if update_channel not in UPDATE_CHANNELS:
            raise InstallError(f"unknown update channel {update_channel!r}")
        argv.extend(["--update-channel", update_channel])

    required = {
        "--jarvis-show-prestaged",
        "--jarvis-show-board",
        "--amazon-logo",
        "--force",
        "--boot",
        "--rootfs",
    }
    if not required.issubset(set(argv)) or "--lineage-zip" in argv:
        raise InstallError("internal error: unsafe Jarvis Show install argument set")

    return InstallPlan(
        board=profile.board,
        argv=tuple(argv),
        rootfs=rootfs_identity,
        backup=backup,
    )


def execute_install(plan: InstallPlan, *, run: Callable = subprocess.run) -> None:
    """Execute the already-validated plan; no shell and no implicit network fetches."""
    try:
        result = run(list(plan.argv), check=False)
    except OSError as exc:
        raise InstallError(f"cannot start TECHO5 slot-store installer: {exc}") from exc
    if result.returncode != 0:
        raise InstallError(f"TECHO5 slot-store installer failed with exit code {result.returncode}")
