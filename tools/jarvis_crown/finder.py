"""Which Shows there are: on the network, on USB, and in this computer's records, as one list.

On the network a Show answers on mDNS as an ESPHome device with platform=echolocal. One with a key
gives api_encryption; one in setup stays quiet until somebody taps its screen button, and then for 15
minutes gives api_provisioning=zero-psk instead, which is the window Home Assistant adds it in. On USB a
Show is in fastboot, in adb (Android or TWRP), or running with its serial console up; on the console
the setup mark says whether it is waiting to be added.

The list only reads. Adding a Show is Home Assistant's (Settings > Devices & services, Discovered), so
a Show that isn't in this Home Assistant is listed and never adopted from here (GENBU-223).

What a Show left behind on this computer once it is gone from the house, its SSH key pair, the host
key pinned for it and its install record, is moved into backups/<serial>/retired-<date>/ when asked:
a new Show with the same name would otherwise pick up the old key, and ssh would stop at the old pin.
The unlock backups next to it stay where they are.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jarvis_crown.home_assistant import node_slug
from jarvis_crown.reach import rule_name
from jarvis_crown.show_ssh import key_path, known_hosts_path
from jarvis_crown.shows import RECORD, Show

SERVICE = "_esphomelib._tcp"
SETUP_MARK = "/data/misc/techo5/setup-mode"  # layout.SetupPath on a Show

_TXT = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class Seen:
    """A Show answering on the network."""
    node: str
    friendly: str = ""
    board: str = ""
    mac: str = ""  # lower case, no colons, as the Show gives it
    version: str = ""
    in_setup: bool = False  # its window for being added is open
    address: str = ""


@dataclass(frozen=True)
class OnUsb:
    """A Show on this computer's USB."""
    serial: str
    state: str  # fastboot, recovery, android, running, in setup, locked (the console wants root's password)


def _txt(raw: str) -> dict[str, str]:
    out = {}
    for item in _TXT.findall(raw):
        key, _, value = item.replace('\\"', '"').replace("\\\\", "\\").partition("=")
        out[key] = value
    return out


def parse_browse(text: str) -> list[Seen]:
    """The Shows in avahi-browse -rtp's resolved lines, once each however many interfaces and address
    families they answer on. ESPHome devices that aren't Shows are left out."""
    found: dict[str, Seen] = {}
    for line in text.splitlines():
        fields = line.split(";", 9)
        if len(fields) < 10 or fields[0] != "=" or fields[4] != SERVICE:
            continue
        txt = _txt(fields[9])
        if txt.get("platform") != "echolocal":
            continue
        node = fields[3].replace("\\032", " ")
        seen = Seen(node=node, friendly=txt.get("friendly_name", ""), board=txt.get("board", ""),
                    mac=txt.get("mac", "").replace(":", "").lower(), version=txt.get("version", ""),
                    in_setup=txt.get("api_provisioning") == "zero-psk",
                    address=fields[7] if fields[2] == "IPv4" else "")
        if node not in found or (seen.address and not found[node].address):
            found[node] = seen
    return sorted(found.values(), key=lambda s: s.node)


def browse(*, run: Callable = subprocess.run, which: Callable = shutil.which, wait: int = 10) -> list[Seen] | None:
    """The Shows on the network now; None when this computer can't look (no avahi-browse)."""
    tool = which("avahi-browse")
    if not tool:
        return None
    try:
        out = run([tool, "-rtp", SERVICE], capture_output=True, text=True, timeout=wait, check=False).stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    except OSError:
        return None
    return parse_browse(out)


def _lines(argv: list[str], run: Callable, which: Callable) -> list[list[str]]:
    if not which(argv[0]):
        return []
    try:
        out = run(argv, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.split() for line in (out or "").splitlines()]


def usb(*, run: Callable = subprocess.run, which: Callable = shutil.which,
        consoles: Callable[[], list[tuple[str, str]]] | None = None,
        ask: Callable[[str], str | None] | None = None) -> list[OnUsb]:
    """The Shows on USB now. A running one is asked over its console whether it is in setup; a console
    that wants root's password is listed as locked and not logged in to."""
    units = []
    for f in _lines(["fastboot", "devices"], run, which):
        if len(f) >= 2 and f[1].lower() == "fastboot":
            units.append(OnUsb(f[0], "fastboot"))
    for f in _lines(["adb", "devices"], run, which)[1:]:
        if len(f) >= 2 and f[1] in ("device", "recovery"):
            units.append(OnUsb(f[0], "android" if f[1] == "device" else "recovery"))
    for port, serial in (consoles or (lambda: []))():
        state = "running"
        if ask is not None:
            try:
                said = (ask(port) or "").split()
            except Exception:  # noqa: BLE001 (ConsoleLocked, or a port somebody else holds)
                said = ["", "locked"]
            if len(said) == 2:
                serial = serial or said[0]
                state = {"setup": "in setup", "keyed": "running", "locked": "locked"}.get(said[1], "running")
        units.append(OnUsb(serial or port, state))
    return units


CONSOLE_ASK = ("s=$(tr ' ' '\\n' < /proc/cmdline | sed -n 's/^androidboot.serialno=//p'); "
               f"if [ -e {SETUP_MARK} ]; then echo \"$s setup\"; else echo \"$s keyed\"; fi")


@dataclass
class Row:
    """One Show in the list, from whatever saw it."""
    name: str
    board: str = ""
    record: Show | None = None
    seen: Seen | None = None
    on_usb: OnUsb | None = None
    in_ha: bool | None = None  # None: this Home Assistant wasn't asked
    notes: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.on_usb and self.on_usb.state == "in setup":
            return "on USB, in setup: tap Let Home Assistant add it on its screen"
        if self.seen and self.seen.in_setup:
            return "waiting to be added: in Home Assistant, Settings > Devices & services, Discovered, Add"
        if self.in_ha:
            return "in this Home Assistant"
        if self.on_usb:
            return {"fastboot": "on USB, in fastboot", "recovery": "on USB, in TWRP", "android": "on USB, on Android",
                    "locked": "on USB; its console wants root's password", "running": "on USB, running"}[self.on_usb.state]
        if self.seen and self.in_ha is False:
            return "on the network, not in this Home Assistant: another one has its key, or it was reset " \
                   "and waits for a tap on its screen"
        if self.seen:
            return "on the network"
        if self.record and self.record.reset:
            return f"reset from here on {self.record.reset[:10]}"
        if self.in_ha is False:
            return "not on the network and not in this Home Assistant"
        return "not on the network"

    @property
    def leftover(self) -> bool:
        """Installed from here, and gone from this Home Assistant: reset (from here or by its Delete in
        Home Assistant), or not around any more. Its keys here open nothing."""
        if self.record is None or self.in_ha is not False:
            return False
        if self.on_usb and self.on_usb.state in ("fastboot", "recovery", "android"):
            return False  # mid-install
        return bool(self.record.reset) or self.seen is None or self.seen.in_setup or \
            bool(self.on_usb and self.on_usb.state == "in setup")

    @property
    def reset_seen(self) -> bool:
        """Known to be reset: from here, or it is in setup now. Not seen at all proves nothing."""
        return bool(self.record and self.record.reset) or bool(self.seen and self.seen.in_setup) or \
            bool(self.on_usb and self.on_usb.state == "in setup")


def merge(records: list[Show], seen: list[Seen], on_usb: list[OnUsb],
          in_ha: Callable[[str], bool | None] = lambda name: None) -> list[Row]:
    """One row per Show: a record matched to what answers on the network by node name or MAC, and to
    USB by serial; whatever matches nothing gets a row of its own."""
    rows: list[Row] = []
    left = list(seen)
    usb_left = list(on_usb)
    for show in records:
        row = Row(name=show.name, board=show.board, record=show)
        for s in left:
            if s.node == node_slug(show.name) or (show.mac and s.mac == show.mac.replace(":", "").lower()):
                row.seen = s
                left.remove(s)
                break
        for u in usb_left:
            if u.serial == show.folder.name:
                row.on_usb = u
                usb_left.remove(u)
                break
        rows.append(row)
    rows += [Row(name=s.friendly or s.node, board=s.board, seen=s) for s in left]
    rows += [Row(name=f"serial ending {u.serial[-4:]}", on_usb=u) for u in usb_left]
    for row in rows:
        row.in_ha = in_ha(row.name)
    return rows


def leftovers(row: Row, key_dir: Path | None = None) -> list[Path]:
    """What this computer still keeps of a Show gone from the house: its SSH key pair, the host key
    pinned for it, and its install record. Only the files that are there."""
    if row.record is None:
        return []
    key = key_path(row.record.name, key_dir)
    files = [key, key.with_name(key.name + ".pub"), known_hosts_path(row.record.name, key_dir),
             row.record.folder / RECORD]
    return [f for f in files if f.is_file()]


def retire(row: Row, key_dir: Path | None = None, *, today: date | None = None) -> Path:
    """Moves the leftovers into backups/<serial>/retired-<date>/, where nothing looks for them; returns
    that folder. Moved, not deleted: a Show that turns out not to be gone gets them back by moving
    them home."""
    assert row.record is not None
    to = row.record.folder / f"retired-{(today or date.today()).isoformat()}"
    to.mkdir(mode=0o700, exist_ok=True)
    for f in leftovers(row, key_dir):
        shutil.move(str(f), str(to / f.name))
    return to


def router_note(row: Row) -> str:
    """The router rules made for the Show stay until somebody takes them out; the installer never
    changes the router."""
    rn = rule_name(row.record.name if row.record else row.name)
    return (f"the router keeps the rules made for it until you take them out. On OpenWrt the install guide's "
            f"genbu_unrules helper does it: genbu_unrules {rn}")
