"""Getting a Show into fastboot without holding buttons: the step before install and unlock.

A Show on Android or in TWRP goes there with `adb reboot bootloader`. A Show already running Jarvis
goes there over SSH with its own `rebootto bootloader` (cmd/rebootto), which marks fastboot for the
bootloader the way Android does. Before that reboot, the serial the Show reports has to be the one
its install record was saved under, so a stale address can't send some other Show into fastboot.
Only reboots happen here; nothing on the Show is written.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from jarvis_crown.device_gate import DeviceGateError, fastboot_serials
from jarvis_crown.shows import show_named
from jarvis_crown.timing import COMMAND_TIMEOUT_SECONDS, FASTBOOT_REENUM_TIMEOUT_SECONDS, POLL_SECONDS

REBOOTTO = "/usr/local/bin/rebootto"
SERIAL_PROBE = "tr ' ' '\\n' </proc/cmdline | grep '^androidboot.serialno='"
BACK_HINT = "`fastboot continue` starts it normally again"


class ToFastbootError(RuntimeError):
    pass


def _adb_devices(run: Callable) -> list[tuple[str, str]]:
    try:
        r = run(["adb", "devices"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in r.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            found.append((fields[0], fields[1]))
    return found


def _ssh(host: str, command: str, *, run: Callable, timeout: float = 30) -> subprocess.CompletedProcess:
    # BatchMode: the key the install wrote or nothing, never a password question.
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=3",
                "-o", "ServerAliveCountMax=2", f"root@{host}", command],
               capture_output=True, text=True, timeout=timeout, check=False)


def _address(name: str | None, backups: Path, host: str | None, find_host: Callable | None):
    """(address, expected serial or None) of the Show to reboot."""
    show = show_named(backups, name) if name else None
    if show is None and not host:
        raise ToFastbootError(
            "no Show on fastboot or adb" + (f", and none named {name!r} was installed from here" if name else "")
            + ". Put it into fastboot, or give --name of an installed Show (or --host) so it can be sent there")
    if not host and show is not None:
        try:
            host = (show.folder / "address").read_text(encoding="utf-8").strip()
        except OSError:
            host = ""
        if not host and find_host is not None:
            host = find_host(show.name)() or ""
    if not host:
        raise ToFastbootError(f"{show.name}'s address isn't known; give it with --host, or put it into fastboot")
    return host, (show.folder.name if show is not None else None)


def _wait(say: Callable, *, run: Callable, sleep: Callable, clock: Callable, timeout: float, after: str) -> None:
    say(f"waiting for it in fastboot (up to {int(timeout // 60)} min)")
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            if fastboot_serials(run=run):
                return
        except DeviceGateError:
            pass
        sleep(POLL_SECONDS)
    raise ToFastbootError(f"{after}, but no Show showed up on fastboot here. Check the USB cable carries "
                          f"data and goes straight to this computer; {BACK_HINT}")


def bring_to_fastboot(*, name: str | None, backups: Path, host: str | None = None, keep_recovery: bool = False,
                      find_host: Callable | None = None, run: Callable = subprocess.run,
                      sleep: Callable = time.sleep, clock: Callable = time.monotonic,
                      timeout: float = FASTBOOT_REENUM_TIMEOUT_SECONDS, say: Callable = print) -> str:
    """Leaves the Show on fastboot, rebooting it there when needed; returns what was done ('' for nothing).

    keep_recovery: a Show in TWRP stays there (an install with --board carries on from TWRP).
    """
    try:
        if fastboot_serials(run=run):
            return ""
    except DeviceGateError:
        pass  # fastboot itself is missing or broken; the identity gate says so properly

    adb = _adb_devices(run)
    if keep_recovery and any(state == "recovery" for _, state in adb):
        return ""
    if len(adb) > 1:
        raise ToFastbootError(f"{len(adb)} devices on adb; leave only the Show to install connected")
    if adb:
        serial, state = adb[0]
        if state == "unauthorized":
            raise ToFastbootError("the Show on adb hasn't allowed this computer yet; accept the prompt on its "
                                  "screen and run this again")
        if state not in ("device", "recovery"):
            raise ToFastbootError(f"the Show on adb is {state!r}; put it into fastboot")
        say(f"the Show (serial ending {serial[-4:]}) is on {'TWRP' if state == 'recovery' else 'Android'}; "
            "rebooting it into fastboot")
        r = run(["adb", "-s", serial, "reboot", "bootloader"], capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT_SECONDS, check=False)
        if r.returncode != 0:
            raise ToFastbootError(f"adb reboot bootloader: {(r.stderr or r.stdout).strip() or r.returncode}")
        _wait(say, run=run, sleep=sleep, clock=clock, timeout=timeout, after="it rebooted")
        return "adb"

    host, expected = _address(name, backups, host, find_host)
    try:
        probe = _ssh(host, f"test -x {REBOOTTO} && echo rebootto; {SERIAL_PROBE}", run=run)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToFastbootError(f"no Show on fastboot or adb, and SSH to {host} failed ({exc}); "
                              "put it into fastboot") from None
    if probe.returncode not in (0, 1) or "rebootto" not in probe.stdout:
        why = (probe.stderr or "").strip().splitlines()[-1:] or ["no rebootto there"]
        raise ToFastbootError(f"no Show on fastboot or adb, and {host} can't be sent there over SSH "
                              f"({why[0]}); put it into fastboot")
    said = re.search(r"androidboot\.serialno=(\S+)", probe.stdout)
    serial = said.group(1) if said else None
    if expected is not None and serial != expected:
        raise ToFastbootError(f"the Show at {host} reports serial ending {(serial or '????')[-4:]}, not "
                              f"{expected[-4:]} as recorded; nothing was rebooted. Give the right one with "
                              "--host, or put it into fastboot")
    say(f"the Show at {host} (serial ending {(serial or '????')[-4:]}) runs Jarvis; rebooting it into fastboot")
    try:
        # The connection drops with the reboot, so how ssh ends says nothing; fastboot showing up does.
        _ssh(host, f"{shlex.quote(REBOOTTO)} bootloader", run=run, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass
    _wait(say, run=run, sleep=sleep, clock=clock, timeout=timeout, after="it was told to reboot into fastboot")
    return "ssh"
