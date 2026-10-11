"""The Genbu house without Home Assistant: how versions rank, which version the house runs, how long a main
update Show has been gone and what to do about it, and which Show to recommend in its place. The glue in
coordinator.py feeds it what Home Assistant sees and acts on what it answers. See docs/update-leader.md.

Everything here is plain Python so the tests run without Home Assistant installed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# The shape a Show's version has to have before the house believes it, the same as echod's
# update.ValidVersion: dotted numerals, optionally a prerelease and _build detail.
VERSION = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?(_[0-9A-Za-z.-]+)?$")

# When a main update Show has been gone this long, the house says so (the Shows put it on their update
# page), asks you in Repairs, and picks a new main by itself.
STATUS_AFTER = timedelta(hours=1)
ASK_AFTER = timedelta(hours=24)
PICK_AFTER = timedelta(days=7)
WAIT_MORE = timedelta(days=7)  # "keep waiting 7 more days" in the fix flow

# What missing() says about each main update Show.
PRESENT, QUIET, STATUS, ASK, PICK = "present", "quiet", "status", "ask", "pick"


def valid(v: str | None) -> bool:
    return bool(v) and bool(VERSION.match(v))


def _parse(v: str) -> tuple[list[int], str] | None:
    v = v.strip().removeprefix("v").split("_", 1)[0]
    v, _, pre = v.partition("-")
    try:
        numbers = [int(p) for p in v.split(".")]
    except ValueError:
        return None
    if any(n < 0 for n in numbers):
        return None
    return numbers, pre


_CHANNELS = {"dev": 0, "staging": 1, "stable": 2}


def _channel_build(tail: str) -> tuple[int, list[int]] | None:
    fields = tail.split(".")
    if fields[0] not in _CHANNELS or len(fields) < 2:
        return None
    try:
        return _CHANNELS[fields[0]], [int(f) for f in fields[1:]]
    except ValueError:
        return None


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _numbers(a: list[int], b: list[int]) -> int:
    for i in range(max(len(a), len(b))):
        x, y = a[i] if i < len(a) else 0, b[i] if i < len(b) else 0
        if x != y:
            return _sign(x - y)
    return 0


def _prerelease(a: str, b: str) -> int:
    ca, cb = _channel_build(a), _channel_build(b)
    if ca and cb:
        # Two CI builds go by their run number whatever channel they name, as on the Show and in
        # Home Assistant's own update card.
        return _numbers(ca[1], cb[1]) or _sign(ca[0] - cb[0])
    af, bf = a.split("."), b.split(".")
    for i in range(max(len(af), len(bf))):
        if i >= len(af):
            return -1
        if i >= len(bf):
            return 1
        x, y = af[i], bf[i]
        if x.isdigit() and y.isdigit():
            if int(x) != int(y):
                return _sign(int(x) - int(y))
        elif x != y:
            return -1 if x < y else 1
    return 0


def compare(a: str, b: str) -> int | None:
    """How a ranks against b, as echod's update/version.go ranks them: negative when a is older, zero
    when they are the same, positive when a is newer. None when either is not a version."""
    pa, pb = _parse(a), _parse(b)
    if pa is None or pb is None:
        return None
    if n := _numbers(pa[0], pb[0]):
        return n
    if not pa[1] and not pb[1]:
        return 0
    if not pa[1]:
        return 1  # a release is past its own prereleases
    if not pb[1]:
        return -1
    return _prerelease(pa[1], pb[1])


def newer(a: str, b: str) -> bool:
    return (compare(a, b) or 0) > 0


@dataclass
class Show:
    """A Genbu Show as Home Assistant sees it right now."""

    node: str  # the ESPHome node name, the one a Show finds in main_shows
    name: str  # what Home Assistant calls it
    installed: str = ""  # installed_version of its firmware update entity
    committed: str = ""  # its committed_version sensor
    online: bool = False
    watch: tuple = ()  # the entity ids Home Assistant reads these from
    device: str = ""  # Home Assistant's device id, which stays when the Show is renamed
    area: str = ""  # the room Home Assistant has it in
    satellite: str = ""  # its assist_satellite entity
    mac: str = ""  # its MAC, which a rename doesn't change


def _iso(t: datetime | None) -> str:
    return t.replace(microsecond=0).isoformat() if t else ""  # the Shows read it as RFC 3339


def _time(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def missing_step(since: datetime | None, now: datetime, wait_until: datetime | None = None) -> str:
    """Where a main update Show that has been gone since `since` stands. "Keep waiting" moves the pick
    to wait_until, and the question comes back a day before it."""
    if since is None:
        return PRESENT
    pick_at, ask_at = since + PICK_AFTER, since + ASK_AFTER
    if wait_until and wait_until > pick_at:
        pick_at, ask_at = wait_until, max(ask_at, wait_until - ASK_AFTER)
    if now >= pick_at:
        return PICK
    if now >= ask_at:
        return ASK
    if now - since >= STATUS_AFTER:
        return STATUS
    return QUIET


def recommend(shows: list[Show], house: str, leaving: str | None = None) -> str | None:
    """The Show to make the main update Show: one that is online, preferably already on the house
    version, then the newest one. None when no Show is online."""
    best: tuple | None = None
    for s in shows:
        if not s.online or s.node == leaving:
            continue
        mine = s.committed or s.installed
        key = (bool(house) and mine == house, mine if valid(mine) else "", s.node)
        if best is None or _better(key, best):
            best = key
    return best[2] if best else None


def _better(a: tuple, b: tuple) -> bool:
    if a[0] != b[0]:
        return a[0]
    if (c := compare(a[1], b[1]) if a[1] and b[1] else (1 if a[1] else -1 if b[1] else 0)) != 0:
        return c > 0
    return a[2] < b[2]


class House:
    """What the integration remembers about the house, kept in a dict it stores across restarts:

    mains          the main update Shows, by node name; only this class changes it
    committed      each Show's last committed version, kept while it is offline
    missing_since  when each main update Show was first seen gone
    wait_until     a later pick, from "keep waiting"
    told           the missing spells already pushed to a phone
    devices        each Show's node name by device id, to notice a rename
    music          the player picked in each Show's "Music plays on", by device id
    house, since   the house version and when it last changed
    """

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if data is not None else {}
        for k in ("committed", "missing_since", "wait_until", "told", "devices", "music"):
            self.data.setdefault(k, {})
        self.data.setdefault("mains", [])
        self.data.setdefault("house", "")
        self.data.setdefault("since", "")

    @property
    def mains(self) -> list[str]:
        return list(self.data["mains"])

    def see(self, shows: list[Show], now: datetime) -> bool:
        """Takes in what Home Assistant shows now; True when anything worth storing changed."""
        before = repr(self.data)
        self._renamed(shows)
        online = {s.node for s in shows if s.online}
        for s in shows:
            if valid(s.committed):
                self.data["committed"][s.node] = s.committed
        for node in self.data["mains"]:
            if node in online:
                self.data["missing_since"].pop(node, None)
                self.data["wait_until"].pop(node, None)
                self.data["told"].pop(node, None)
            else:
                self.data["missing_since"].setdefault(node, _iso(now))
        self._forget_old()
        self._settle(now)
        return repr(self.data) != before

    def _renamed(self, shows: list[Show]) -> None:
        # A renamed Show comes back under a new node name on the same device. A main keeps its place.
        devices = self.data["devices"]
        for s in shows:
            if not s.device:
                continue
            old = devices.get(s.device)
            devices[s.device] = s.node
            if not old or old == s.node:
                continue
            if old in self.data["mains"] and s.node not in self.data["mains"]:
                self.data["mains"] = [s.node if m == old else m for m in self.data["mains"]]
            for k in ("committed", "missing_since", "wait_until", "told"):
                if old in self.data[k]:
                    self.data[k].setdefault(s.node, self.data[k].pop(old))

    def _forget_old(self) -> None:
        for k in ("missing_since", "wait_until", "told"):
            for node in [n for n in self.data[k] if n not in self.data["mains"]]:
                del self.data[k][node]

    def _settle(self, now: datetime) -> None:
        # The newest version a main update Show has committed. With no main, or none that has
        # committed anything yet, the house stays where it was.
        best = ""
        for node in self.data["mains"]:
            v = self.data["committed"].get(node, "")
            if valid(v) and (not best or newer(v, best)):
                best = v
        if best and best != self.data["house"]:
            self.data["house"], self.data["since"] = best, _iso(now)

    @property
    def version(self) -> str:
        return self.data["house"]

    @property
    def since(self) -> str:
        return self.data["since"]

    def missing_since(self) -> str:
        """When the house lost its last main update Show, for the Shows' update page; empty while one
        of them is there."""
        mains = self.data["mains"]
        times = [self.data["missing_since"].get(n) for n in mains]
        if not mains or not all(times):
            return ""
        return max(times)

    def missing(self, now: datetime) -> dict[str, str]:
        """Each main update Show and where it stands; one entry per main, however many Shows follow."""
        return {node: missing_step(_time(self.data["missing_since"].get(node)), now,
                                   _time(self.data["wait_until"].get(node)))
                for node in self.data["mains"]}

    def gone_since(self, node: str) -> datetime | None:
        return _time(self.data["missing_since"].get(node))

    def pick_at(self, node: str) -> datetime | None:
        since = self.gone_since(node)
        if since is None:
            return None
        wait = _time(self.data["wait_until"].get(node))
        return max(since + PICK_AFTER, wait) if wait else since + PICK_AFTER

    def tell_once(self, node: str) -> bool:
        """True the first time a spell of this main being gone is worth a push."""
        since = self.data["missing_since"].get(node, "")
        if not since or self.data["told"].get(node) == since:
            return False
        self.data["told"][node] = since
        return True

    def make_main(self, node: str, now: datetime) -> None:
        """The select: node becomes the main update Show, in the first place if there are more."""
        rest = [m for m in self.data["mains"][1:] if m != node]
        self.data["mains"] = [node, *rest]
        self._forget_old()
        self._settle(now)

    def set_extra(self, nodes: list[str], now: datetime) -> None:
        """The options: more main update Shows next to the first."""
        first = self.data["mains"][:1]
        self.data["mains"] = first + [n for n in dict.fromkeys(nodes) if n not in first]
        self._forget_old()
        self._settle(now)

    def replace(self, gone: str, new: str | None, now: datetime) -> None:
        """A main update Show is gone for good: new takes its place, or with None, it just leaves."""
        mains = [m for m in self.data["mains"] if m != new]
        if new and gone in mains:
            mains[mains.index(gone)] = new
        else:
            mains = [m for m in mains if m != gone]
        self.data["mains"] = mains
        self._forget_old()
        self._settle(now)

    def music_choice(self, device: str) -> str:
        return self.data["music"].get(device, "")

    def choose_music(self, device: str, player: str) -> None:
        """The Show's "Music plays on": a player, or "auto" (music.AUTO) to go back to the usual one."""
        if player and player != "auto":
            self.data["music"][device] = player
        else:
            self.data["music"].pop(device, None)

    def wait_more(self, node: str, now: datetime) -> None:
        self.data["wait_until"][node] = _iso(now + WAIT_MORE)

    def auto_pick(self, node: str, shows: list[Show], now: datetime) -> str | None:
        """What the house does when nobody answered for PICK_AFTER: with another main update Show still
        there, the gone one just leaves; otherwise the recommended Show takes its place. Returns the new
        main, "" when the gone one just left, or None when nothing could be done yet (no Show online)."""
        online = {s.node for s in shows if s.online}
        if any(m in online for m in self.data["mains"] if m != node):
            self.replace(node, None, now)
            return ""
        new = recommend(shows, self.version, leaving=node)
        if new is None:
            return None
        self.replace(node, new, now)
        return new
