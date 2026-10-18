"""A Show reset over SSH before it leaves the house (jarvis-show.py --remove-show --reset-show yes).

It is the screen's Factory reset, asked for from here: the mark goes on the Show and it restarts, and the
boot script erases on the way up, before anything that could write it back is running (t5_factory_reset
in tools/linux/techo5-lib.sh). Its Home Assistant key becomes a random one nobody holds, its pairing,
settings, SSH keys and logs go, and it comes back in setup mode. With keep_wifi the mark says keep-wifi,
and the Show keeps the networks it knew; only an image whose boot script knows that word is asked to
(KEEPS_WIFI), since an older one would erase them anyway.

Only the Show's own key logs in (~/.ssh/genbu_<name>, show_ssh.py), under its pinned host key, and the
serial it reports has to be the one its install record was saved under, so a stale address can't reset
some other Show. Its address is never said: the messages call it the Show.
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .home_assistant import HomeAssistant, HomeAssistantError
from .show_ssh import ssh_options
from .to_fastboot import SERIAL_PROBE

LIB = "/lib/techo5-lib.sh"
MARK = "/data/misc/techo5/factory_reset"
KEEPS_WIFI = r"(?m)^T5_RESET_KEEPS=.*\bwifi\b"
BOOT_ID = "/proc/sys/kernel/random/boot_id"
PROBE = (f"test -d /data/misc/techo5 && echo genbu; grep -q '^T5_RESET_KEEPS=.*wifi' {LIB} && echo keeps-wifi; "
         f"{SERIAL_PROBE}")


class ResetError(RuntimeError):
    pass


class NotRestarted(ResetError):
    """The mark is on the Show but it still runs: it erases the next time it starts."""


@dataclass
class Reach:
    host: str
    serial: str
    keeps_wifi: bool


def address(ha: HomeAssistant, entities: list[str], *, host: str | None = None,
            folder: Path | None = None, find_host: Callable[[], str | None] | None = None) -> str | None:
    """Where the Show is: host when given, else what its IP address sensor says in Home Assistant (read
    before its ESPHome entry goes), else what its install record kept, else mDNS."""
    if host:
        return host
    for entity in entities:
        if not (entity.startswith("sensor.") and entity.endswith("_ip_address")):
            continue
        try:
            state = str((ha.request("GET", f"/api/states/{entity}") or {}).get("state") or "")
            return str(ipaddress.ip_address(state))
        except (HomeAssistantError, ValueError):
            continue
    if folder is not None:
        try:
            kept = (folder / "address").read_text(encoding="utf-8").strip()
        except OSError:
            kept = ""
        if kept:
            return kept
    return (find_host() if find_host else None) or None


def _ssh(host: str, command: str, *, name: str, identity: Path, run: Callable,
         timeout: float = 30) -> subprocess.CompletedProcess:
    # BatchMode: the Show's own key or nothing. No stdin: the questions after this read the terminal.
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=3",
                "-o", "ServerAliveCountMax=2", *ssh_options(identity, name), f"root@{host}", command],
               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)


def _why(result: subprocess.CompletedProcess, host: str) -> str:
    last = ((result.stderr or "").strip().splitlines() or [f"ssh ended with {result.returncode}"])[-1]
    return last.replace(host, "the Show")


def probe(host: str, *, name: str, identity: Path | None, expected: str | None,
          run: Callable = subprocess.run) -> Reach:
    """Logs in and looks, nothing more: that it is a Genbu Show, the serial it reports, and whether its boot
    script can keep the Wi-Fi. ResetError says why it can't be reset from here."""
    if identity is None:
        raise ResetError(f"there is no key for it on this computer (~/.ssh/genbu_{name})")
    try:
        r = _ssh(host, PROBE, name=name, identity=identity, run=run)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResetError(f"SSH did not answer ({type(exc).__name__})") from None
    if r.returncode == 255 or "genbu" not in r.stdout.split():
        raise ResetError(f"SSH: {_why(r, host)}" if r.returncode == 255 else "it does not look like a Genbu Show")
    said = re.search(r"androidboot\.serialno=(\S+)", r.stdout)
    serial = said.group(1) if said else ""
    if expected is not None and serial != expected:
        raise ResetError(f"the Show there reports serial ending {(serial or '????')[-4:]}, not {expected[-4:]} "
                         "as recorded, so it may be another one")
    return Reach(host=host, serial=serial, keeps_wifi="keeps-wifi" in r.stdout.split())


def reset(reach: Reach, *, keep_wifi: bool, name: str, identity: Path, run: Callable = subprocess.run,
          wait: float = 60, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> None:
    """Leaves the mark, checks it is there, restarts the Show and waits until it has gone down; the boot
    script erases. ResetError when the mark could not be left: then nothing on the Show changed.
    NotRestarted when it still runs the same boot after wait seconds."""
    if keep_wifi and not reach.keeps_wifi:
        raise ResetError("its image can't keep the Wi-Fi through a reset")
    lines = "ssh keep-wifi" if keep_wifi else "ssh"
    try:
        r = _ssh(reach.host, f"umask 077; printf '%s\\n' {lines} > {MARK}.new && mv -f {MARK}.new {MARK} && sync "
                             f"&& cat {MARK} && cat {BOOT_ID}", name=name, identity=identity, run=run)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResetError(f"SSH did not answer ({type(exc).__name__})") from None
    said = r.stdout.split()
    if r.returncode != 0 or said[:-1] != lines.split():
        raise ResetError(f"the mark was not left ({_why(r, reach.host)})")
    boot = said[-1]
    try:
        # The connection drops with the restart, so how ssh ends says nothing.
        _ssh(reach.host, "reboot", name=name, identity=identity, run=run, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Gone down is enough: it stops answering, or answers from a new boot. Its shutdown gives the daemon
    # up to ten seconds before SSH stops.
    end = clock() + wait
    while True:
        try:
            r = _ssh(reach.host, f"cat {BOOT_ID}", name=name, identity=identity, run=run, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return
        if r.returncode != 0 or r.stdout.strip() != boot:
            return
        if clock() >= end:
            raise NotRestarted("it did not restart. The mark is on it, so it erases the next time it starts")
        sleep(5)
