#!/usr/bin/env python3
"""One-command Jarvis Show install orchestration.

The orchestration keeps destructive boundaries explicit and dependency-injectable so CI can prove
stage ordering without a device. All local install inputs are validated before Lineage staging formats
userdata. The same exact final confirmation then authorizes both vendor staging and the slot-store
handoff; a failed check never falls through to a later stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping

from jarvis_crown.boards import BoardProfile, profile_for_board
from jarvis_crown.device_gate import DeviceIdentity, identify_show
from jarvis_crown.install import InstallPlan, execute_install, make_install_plan
from jarvis_crown.preflight import Check, preflight_ok, run_preflight
from jarvis_crown.shows import record_show
from jarvis_crown.recovery import (
    BackupResult,
    RecoverySession,
    SubprocessRecoveryClient,
    backup_recovery_state,
    ensure_twrp,
)
from jarvis_crown.unlock import UnlockResult, unlock_show


class FlowError(RuntimeError):
    """A one-command install gate failed before completion."""


@dataclass(frozen=True)
class InstallInputs:
    board: str
    repo_root: Path
    amonet_dir: Path
    lineage_zip: Path
    work_dir: Path
    backups_dir: Path
    name: str
    boot_image: Path
    rootfs: Path
    rootfs_sha256: str
    boot_sha256: str | None = None
    twrp_sha256: str | None = None
    amonet_hashes: Mapping[str, str] | None = None
    wifi: str | None = None
    wifi_passphrase_file: Path | None = None
    ssh_key: Path | None = None
    ha_url: str | None = None
    ha_token_file: Path | None = None
    dashcast: str | None = None
    dashcast_key_file: Path | None = None
    music_assistant: str | None = None
    expected_fastboot_serial: str | None = None
    root_password_file: Path | None = None
    update_channel: str | None = None


@dataclass(frozen=True)
class FlowResult:
    profile: BoardProfile
    identity: DeviceIdentity
    recovery: RecoverySession
    backup: BackupResult
    plan: InstallPlan


@dataclass
class FlowDeps:
    preflight: Callable = run_preflight
    identify: Callable = identify_show
    unlock: Callable = unlock_show
    recovery: Callable = ensure_twrp
    backup: Callable = backup_recovery_state
    make_plan: Callable = make_install_plan
    stage_lineage: Callable | None = None
    execute: Callable = execute_install
    client_factory: Callable = SubprocessRecoveryClient


def lineage_stage_argv(inputs: InstallInputs, adb_serial: str) -> list[str]:
    return [
        sys.executable,
        str(inputs.repo_root / "tools" / "install-show.py"),
        "--serial", adb_serial,
        "--lineage-zip", str(inputs.lineage_zip.resolve()),
        "--jarvis-show-stage-lineage-only",
        "--jarvis-show-board", profile_for_board(inputs.board).board,
        "--backups", str(inputs.backups_dir.resolve()),
        "--work", str(inputs.work_dir.resolve()),
    ]


def execute_lineage_stage(
    inputs: InstallInputs,
    adb_serial: str,
    *,
    run: Callable = subprocess.run,
) -> None:
    argv = lineage_stage_argv(inputs, adb_serial)
    try:
        result = run(argv, check=False)
    except OSError as exc:
        raise FlowError(f"cannot start Lineage vendor staging: {exc}") from exc
    if result.returncode != 0:
        raise FlowError(f"Lineage vendor staging failed with exit code {result.returncode}")


def _raise_preflight(checks: list[Check]) -> None:
    failed = [f"{check.name}: {check.detail}" for check in checks if check.failed]
    raise FlowError("host/input preflight failed: " + "; ".join(failed))


def run_install_flow(
    inputs: InstallInputs,
    *,
    confirm_unlock: Callable[[BoardProfile], str],
    confirm_install: Callable[[BoardProfile], str],
    deps: FlowDeps | None = None,
    progress: Callable[[str], None] | None = None,
    initial_identity: DeviceIdentity | None = None,
) -> FlowResult:
    deps = deps or FlowDeps()
    stage_lineage = deps.stage_lineage or execute_lineage_stage
    profile = profile_for_board(inputs.board)
    say = progress or (lambda _message: None)

    say("preflight")
    checks = deps.preflight(
        repo_root=inputs.repo_root,
        amonet_dir=inputs.amonet_dir,
        lineage_zip=inputs.lineage_zip,
        work_dir=inputs.work_dir,
        backup_dir=inputs.backups_dir,
        board=profile.board,
    )
    if not preflight_ok(checks):
        _raise_preflight(checks)

    say("identify")
    identity = initial_identity or deps.identify(expected_board=profile.board)
    if identity.product != profile.fastboot_product:
        raise FlowError(
            f"device identity product {identity.product!r} does not match expected "
            f"{profile.fastboot_product!r}"
        )
    if inputs.expected_fastboot_serial is not None and identity.serial != inputs.expected_fastboot_serial:
        raise FlowError(
            "device changed after auto-detection: "
            f"expected {inputs.expected_fastboot_serial}, found {identity.serial}; stopping before writes"
        )

    if not identity.unlocked:
        say("unlock")
        phrase = confirm_unlock(profile)
        unlocked: UnlockResult = deps.unlock(
            identity,
            inputs.amonet_dir,
            confirmation=phrase,
            expected_hashes=inputs.amonet_hashes,
        )
        identity = unlocked.identity
        if not identity.unlocked:
            raise FlowError("unlock stage returned without a proven unlocked identity")
    else:
        say("unlock-skipped")

    say("recovery")
    session: RecoverySession = deps.recovery(
        identity,
        inputs.amonet_dir,
        expected_twrp_sha256=inputs.twrp_sha256,
    )

    say("backup")
    backup_root = inputs.backups_dir / session.adb_serial
    record_show(backup_root, name=inputs.name, board=profile.board)
    client = deps.client_factory(session.adb_serial)
    backup: BackupResult = deps.backup(
        client,
        backup_root,
        fastboot_identity=identity,
    )

    # Build the pure, fully validated final plan before the first userdata erase. Supplying the exact
    # phrase here does not authorize execution: it merely lets make_install_plan validate all local
    # bytes and the board-bound backup. The user confirmation below is the destructive gate.
    say("validate-install-inputs")
    plan: InstallPlan = deps.make_plan(
        repo_root=inputs.repo_root,
        adb_serial=session.adb_serial,
        name=inputs.name,
        backup_root=backup_root,
        work_dir=inputs.work_dir,
        boot_image=inputs.boot_image,
        boot_sha256=inputs.boot_sha256,
        rootfs=inputs.rootfs,
        rootfs_sha256=inputs.rootfs_sha256,
        confirmation=profile.install_confirmation,
        board=profile.board,
        wifi=inputs.wifi,
        wifi_passphrase_file=inputs.wifi_passphrase_file,
        ssh_key=inputs.ssh_key,
        ha_url=inputs.ha_url,
        ha_token_file=inputs.ha_token_file,
        dashcast=inputs.dashcast,
        dashcast_key_file=inputs.dashcast_key_file,
        music_assistant=inputs.music_assistant,
        root_password_file=inputs.root_password_file,
        update_channel=inputs.update_channel,
    )

    phrase = confirm_install(profile)
    if phrase != profile.install_confirmation:
        raise FlowError(f"final confirmation must be exactly: {profile.install_confirmation}")

    say("stage-lineage")
    stage_lineage(inputs, session.adb_serial)

    say("install")
    deps.execute(plan)
    say("complete")
    return FlowResult(
        profile=profile,
        identity=identity,
        recovery=session,
        backup=backup,
        plan=plan,
    )
