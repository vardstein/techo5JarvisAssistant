#!/usr/bin/env python3
"""Host-only preflight for the Jarvis Crown installer.

This module deliberately performs no adb/fastboot/device I/O. It only validates
that the host and local installation inputs are safe enough for later stages to
begin device discovery.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable
import zipfile

from jarvis_crown import ui
from jarvis_crown.boards import profile_for_board

MIN_FREE_BYTES = 8 * 1024 * 1024 * 1024
REQUIRED_TOOLS = {
    "adb": "install Android platform-tools (adb)",
    "fastboot": "install Android platform-tools (fastboot)",
    "git": "install Git",
    "bash": "install bash (required by Amonet fastbrick)",
    "timeout": "install GNU coreutils timeout (required by Amonet fastbrick)",
}
SERIAL_GROUPS = {"dialout", "uucp"}
AMONET_REQUIRED_COMMON = (
    "fastbrick.sh",
    "profile.sh",
    "device.prop",
    "bin/fastbrick.img",
    "bin/twrp.img",
    "bin/preloader.img",
    "bin/lk.bin",
)
# Crown compatibility for existing tests/tools.
AMONET_REQUIRED = AMONET_REQUIRED_COMMON + ("bin/crown-kaeru.bin",)


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str
    # What to do about a failed check, in words for somebody who has not read the code (GENBU-216).
    fix: str = ""

    @property
    def failed(self) -> bool:
        return self.level == "FAIL"


def _group_names() -> set[str]:
    import grp

    names: set[str] = set()
    if not hasattr(os, "getgroups"):
        return names
    for gid in os.getgroups():
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    return names


def _serial_fix() -> str:
    import getpass

    user = getpass.getuser()
    if Path("/etc/NIXOS").exists():
        return (f"This login can't open the Show's rescue console yet. Add \"dialout\" to "
                f"users.users.{user}.extraGroups in your NixOS configuration, run nixos-rebuild switch, then "
                "log out and in again.")
    return (f"This login can't open the Show's rescue console yet. Run: sudo usermod -aG dialout {user}, then "
            "log out and in again. If you already did, logging out and in is all that's left.")


def _modemmanager_active(run: Callable = subprocess.run) -> bool:
    if shutil.which("systemctl") is None:
        return False
    result = run(
        ["systemctl", "is-active", "--quiet", "ModemManager"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, str(exc)
    if not path.is_dir():
        return False, "not a directory"
    if not os.access(path, os.W_OK | os.X_OK):
        return False, "not writable by this user"
    return True, "writable"


def _device_prop(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _check_amonet(amonet_dir: Path, board: str = "crown") -> Check:
    profile = profile_for_board(board)
    root = amonet_dir / "amonet"
    required = AMONET_REQUIRED_COMMON + (f"bin/{profile.amonet_kaeru_name}",)
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        return Check(
            "FAIL",
            "amonet",
            f"local {profile.board} Amonet bundle incomplete: " + ", ".join(missing),
            f"The install files for the {profile.model} aren't downloaded yet. Get them with: "
            f"python3 tools/fetch-show-assets.py --board {profile.board}",
        )
    props = _device_prop(root / "device.prop")
    if props.get("DEVICE", "").lower() != profile.board:
        return Check(
            "FAIL",
            "amonet",
            f"bundle declares DEVICE={props.get('DEVICE', 'unknown')}, not {profile.board}",
            f"These Amonet files are for another Show model. Leave --amonet-dir out to use the ones for "
            f"the {profile.model}.",
        )
    return Check("PASS", "amonet", f"{amonet_dir} (DEVICE={profile.board})")


def lineage_board(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata = archive.read("META-INF/com/android/metadata").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"not a usable LineageOS zip: {exc}") from exc
    for line in metadata.splitlines():
        if line.startswith("pre-device="):
            return line.split("=", 1)[1].strip()
    raise ValueError("LineageOS metadata has no pre-device entry")


def _check_lineage(path: Path | None, board: str = "crown") -> Check:
    profile = profile_for_board(board)
    if path is None:
        return Check(
            "WARN",
            "lineage",
            f"not supplied yet; vendor staging requires the {profile.board} LineageOS ZIP before install",
        )
    if not path.is_file():
        return Check("FAIL", "lineage", f"file not found: {path}",
                     "There's no LineageOS zip at that path. Check it, or leave --lineage-zip out and the "
                     "install fetches the right one.")
    try:
        detected = lineage_board(path)
    except ValueError as exc:
        return Check("FAIL", "lineage", f"{path}: {exc}",
                     "That file isn't a LineageOS zip the install can use. Leave --lineage-zip out and the "
                     "install fetches the right one.")
    if detected.lower() != profile.board:
        return Check("FAIL", "lineage", f"ZIP declares pre-device={detected}, not {profile.board}",
                     f"That LineageOS zip is for another Show model, not the {profile.model}. Leave "
                     "--lineage-zip out and the install fetches the right one.")
    return Check("PASS", "lineage", f"{path} (pre-device={profile.board})")


def _disk_checks(work_dir: Path, backup_dir: Path, min_free_bytes: int) -> list[Check]:
    checks: list[Check] = []
    seen_devices: set[int] = set()
    for label, path in (("work-dir", work_dir), ("backup-dir", backup_dir)):
        ok, detail = _writable_dir(path)
        checks.append(Check("PASS" if ok else "FAIL", label, f"{path}: {detail}",
                            "" if ok else f"The installer can't write to {path}. Give it another folder with "
                                          f"--{label}, or fix that one's permissions."))
        if not ok:
            continue
        try:
            stat = path.stat()
            free = shutil.disk_usage(path).free
        except OSError as exc:
            checks.append(Check("FAIL", f"disk-space-{label}", f"cannot inspect free space: {exc}",
                                f"The installer can't tell how much room is left there. Give it another "
                                f"folder with --{label}."))
            continue
        if stat.st_dev in seen_devices:
            continue
        seen_devices.add(stat.st_dev)
        gib = free / (1024 ** 3)
        if free < min_free_bytes:
            checks.append(Check(
                "FAIL",
                f"disk-space-{label}",
                f"{gib:.1f} GiB free; at least {min_free_bytes / (1024 ** 3):.0f} GiB required",
                f"Not enough room for the Show's backup and the install files. Free up some space, or give "
                f"the installer another folder with --{label}.",
            ))
        else:
            checks.append(Check("PASS", f"disk-space-{label}", f"{gib:.1f} GiB free"))
    return checks


def run_preflight(
    *,
    repo_root: Path,
    amonet_dir: Path,
    work_dir: Path,
    backup_dir: Path,
    lineage_zip: Path | None = None,
    board: str = "crown",
    min_free_bytes: int = MIN_FREE_BYTES,
) -> list[Check]:
    """Return host/input readiness checks without opening or querying a device."""
    checks: list[Check] = []

    profile = profile_for_board(board)

    if not sys.platform.startswith("linux"):
        checks.append(Check("FAIL", "host-os", "Jarvis Show installer supports Linux hosts only",
                            "The installer runs on Linux only. A Linux live USB stick works too."))
    else:
        checks.append(Check("PASS", "host-os", "Linux host"))

    if sys.version_info < (3, 10):
        checks.append(Check("FAIL", "python", "Python 3.10 or newer is required",
                            "This Python is too old. Run the installer with Python 3.10 or newer."))
    else:
        checks.append(Check("PASS", "python", f"Python {sys.version_info[0]}.{sys.version_info[1]}"))

    for exe, hint in REQUIRED_TOOLS.items():
        found = shutil.which(exe)
        checks.append(Check("PASS" if found else "FAIL", f"tool-{exe}", found or hint,
                            "" if found else f"{exe} isn't on this computer: {hint}, then run this again."))

    if os.geteuid() == 0:
        checks.append(Check(
            "WARN",
            "host-root",
            "running as root is supported for recovery, but normal installs should use an unprivileged serial-enabled user",
        ))
        checks.append(Check("PASS", "serial-permission", "root can open the USB serial device"))
    else:
        groups = _group_names()
        usable = sorted(groups & SERIAL_GROUPS)
        if usable:
            checks.append(Check("PASS", "serial-permission", "member of " + ", ".join(usable)))
        else:
            checks.append(Check(
                "FAIL",
                "serial-permission",
                "this login is not in dialout (or uucp) yet, which the rescue console needs; install sorts it out or says what to run",
                _serial_fix(),
            ))

    if _modemmanager_active():
        checks.append(Check("FAIL", "modemmanager", "ModemManager is active and may seize /dev/ttyACM*; stop it for installation",
                            "ModemManager is running and can grab the Show's rescue console. Stop it for the "
                            "install with: sudo systemctl stop ModemManager"))
    else:
        checks.append(Check("PASS", "modemmanager", "not active"))

    if not repo_root.is_dir():
        checks.append(Check("FAIL", "repo", f"repository missing: {repo_root}",
                            "The installer's own files are missing. Download the repository again."))
    else:
        checks.append(Check("PASS", "repo", str(repo_root)))

    checks.append(_check_amonet(amonet_dir, profile.board))
    checks.append(_check_lineage(lineage_zip, profile.board))
    checks.extend(_disk_checks(work_dir, backup_dir, min_free_bytes))
    return checks


def print_checks(checks: list[Check]) -> None:
    say = {"PASS": ui.out.done, "WARN": ui.out.warn, "FAIL": ui.out.fail}
    ui.out.heading("Checks")
    for check in checks:
        say.get(check.level, ui.out.note)(f"{check.name}: {check.detail}")
        if check.failed and check.fix:
            ui.out.note(check.fix, indent=4)


def preflight_ok(checks: list[Check]) -> bool:
    return not any(check.failed for check in checks)
