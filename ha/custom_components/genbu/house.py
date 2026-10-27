"""The Genbu house without Home Assistant: how versions rank, which version the house runs, which release
channel it reads, and where that channel's manifest is. The glue in coordinator.py feeds it what Home
Assistant sees, and update.py offers what the channel has. See docs/update-leader.md.

Everything here is plain Python so the tests run without Home Assistant installed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# The shape a Show's version has to have before the house believes it, the same as echod's
# update.ValidVersion: dotted numerals, optionally a prerelease and _build detail.
VERSION = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?(_[0-9A-Za-z.-]+)?$")

# Where the Shows' releases are, the same addresses as echod's update/channel.go.
RELEASES = "https://github.com/vardstein/techo5JarvisAssistant/releases"
CHANNELS = ("stable", "staging", "dev")

# What Genbu before 1.4.0 kept about main update Shows, dropped on the first start of 1.4.0 (GENBU-119).
OLD_KEYS = ("mains", "missing_since", "wait_until", "told", "seeded")


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

    node: str  # the ESPHome node name, the one a Show calls itself by
    name: str  # what Home Assistant calls it
    installed: str = ""  # installed_version of its firmware update entity
    committed: str = ""  # its committed_version sensor
    online: bool = False
    watch: tuple = ()  # the entity ids Home Assistant reads these from
    device: str = ""  # Home Assistant's device id, which stays when the Show is renamed
    area: str = ""  # the room Home Assistant has it in
    satellite: str = ""  # its assist_satellite entity
    mac: str = ""  # its MAC, which a rename doesn't change
    key: str = ""  # its "Home Assistant key" sensor, how its key stands (GENBU-196)
    encrypted: bool = False  # whether its ESPHome connection has an encryption key
    model: str = ""  # its board, as its device says (the wake race's board offset)
    status: str = ""  # its update_status line: what its move to the house version waits for (GENBU-249)
    updating: bool = False  # its firmware update is running
    at: int | None = None  # how far that update's download is, in percent
    cut: str = ""  # its "Reply cut off" event: a wake word stopped its reply (GENBU-250)
    check: str = ""  # its "Check for updates" button, which has the house read the channel too (GENBU-157)
    disabled: bool = False  # its ESPHome entry or device, or its genbu device, is disabled (GENBU-261)


# GENBU-249: the list of Shows under the house's update card, in Home Assistant's language. Why a Show
# waits, or why its install failed, is its own update_status line, which the Show writes in English.
STAGES = {
    "de": {"head": "{done} von {online} Shows auf {house}", "current": "aktuell", "downloading": "lädt {at} %",
           "checking": "prüft", "installing": "installiert", "restarting": "startet neu", "waiting": "wartet",
           "offline": "offline", "failed": "⚠ fehlgeschlagen", "back": "kam nach dem Neustart nicht wieder"},
    "en": {"head": "{done} of {online} Shows on {house}", "current": "up to date", "downloading": "downloading {at}%",
           "checking": "checking", "installing": "installing", "restarting": "restarting", "waiting": "waiting",
           "offline": "offline", "failed": "⚠ failed", "back": "didn't come back after the restart"},
}

# GENBU-215: the lines echod's firmware writes to update_status while an install runs, and when one fails.
_STEPS = (("Checking ", "checking"), ("Restarting into ", "restarting"), ("Installing ", "installing"))
_FAILED = re.compile(r"^Update to \S+ failed: (?P<why>.+)$"
                     r"|^Version \S+ did not install here(?: \((?P<here>.+)\))?; staying on \S+$"
                     r"|^Version \S+ could not be fetched: (?P<fetch>.+)$"
                     r"|^rolled back from (?P<back>\S+)$")
# A plain reason with the error behind it: the card shows the reason, the Show's sensor has the error.
_ERROR = re.compile(r"^(?P<why>.+?) \(.+\)$")


def failure(status: str, lang: str = "en") -> str | None:
    """Why the Show's last install failed, from its update_status line; None when the line is no failure."""
    m = _FAILED.match(status)
    if not m:
        return None
    if m["back"]:
        return STAGES.get(lang, STAGES["en"])["back"]
    why = m["why"] or m["here"] or m["fetch"] or ""
    plain = _ERROR.match(why)
    return plain["why"] if plain else why


def stage(show: Show, house: str, lang: str = "en") -> str:
    """Where one Show is on its way to the house version, as one line of the update card's list."""
    words = STAGES.get(lang, STAGES["en"])
    if not show.online:
        return words["offline"]
    if show.updating:
        for prefix, step in _STEPS:
            if show.status.startswith(prefix):
                return words[step]
        if show.at is not None and show.at < 100:
            return words["downloading"].format(at=show.at)
        return words["installing"]
    if show.installed == house:
        return words["current"]
    why = failure(show.status, lang)
    if why is not None:
        return f"{words['failed']}: {why}" if why else words["failed"]
    # The Show's line names the house version once it has seen it; before that it still says what it
    # followed, which is no reason.
    if house and house in show.status:
        return f"{words['waiting']}: {show.status}"
    return words["waiting"]


def overview(shows: list[Show], house: str, lang: str = "en") -> str:
    """Every Show with its version and stage, as Markdown for the update card's dialog."""
    if not shows:
        return ""
    online = [s for s in shows if s.online]
    done = sum(1 for s in online if s.installed == house)
    lines = [f"**{STAGES.get(lang, STAGES['en'])['head'].format(done=done, online=len(online), house=house)}**", ""]
    for s in sorted(shows, key=lambda s: s.name.lower()):
        version = f" · {s.installed}" if s.online and s.installed else ""
        lines.append(f"- **{s.name}**{version} · {stage(s, house, lang)}")
    return "\n".join(lines)


def oldest_behind(shows: list[Show], house: str) -> str:
    """The oldest version an online Show still runs below the house version; "" when none is behind."""
    oldest = ""
    for s in shows:
        if s.online and valid(s.installed) and newer(house, s.installed) and (not oldest or newer(oldest, s.installed)):
            oldest = s.installed
    return oldest


def channel_of(version: str) -> str:
    """The channel a version was built for: v1.4.0-staging.55 is staging, a plain v1.4.0 is stable."""
    pre = version.removeprefix("v").split("_", 1)[0].partition("-")[2]
    name = pre.split(".", 1)[0]
    return name if name in CHANNELS else "stable"


# The manifests a release may have, the one to read first first. Releases sign manifest-v2.json since
# GENBU-191; from the release after the bridge on, their manifest.json is the bridge's, so it names the
# bridge's version, not theirs. Releases before the bridge have only manifest.json.
MANIFESTS = ("manifest-v2.json", "manifest.json")


def manifest_url(channel: str, name: str = "manifest.json") -> str:
    """Where a channel's newest manifest is."""
    if channel == "stable":
        return f"{RELEASES}/latest/download/{name}"
    return f"{RELEASES}/download/channel-{channel}/{name}"


def tag_url(version: str, name: str = "manifest.json") -> str:
    """Where one version's own manifest is, for going back to it."""
    return f"{RELEASES}/download/{version}/{name}"


def _iso(t: datetime | None) -> str:
    return t.replace(microsecond=0).isoformat() if t else ""


class House:
    """What the integration remembers about the house, kept in a dict it stores across restarts:

    committed      each Show's last committed version, kept while it is offline
    devices        each Show's node name by device id, to notice a rename
    music          the player picked in each Show's "Music plays on", by device id
    switches       each Show's Recipes and Updates by voice, by device id, when somebody switched them
    keys           the Home Assistant user each Show's key was seen to belong to, by device id (GENBU-196)
    leaving        the Shows deleted in Home Assistant and not yet gone, by node (GENBU-223, removal.py)
    off            the Shows disabled in Home Assistant whose user Genbu switched off, by device id, until
                   they are back with a new key (GENBU-261, keycare.py)
    house, since   the house version, the one every Show moves to, and when it last changed
    channel        the release channel the house reads; empty for the house version's own
    night          whether a waiting update installs by itself at night
    llm            whether the house's assistant is a language model, for the LLM extras (GENBU-240)
    before         the house version before the last install, for going back
    """

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if data is not None else {}
        for k in OLD_KEYS:
            self.data.pop(k, None)
        for k in ("committed", "devices", "music", "switches", "keys", "leaving", "off"):
            self.data.setdefault(k, {})
        for k in ("house", "since", "channel", "before"):
            self.data.setdefault(k, "")
        self.data.setdefault("night", False)
        self.data.setdefault("llm", False)

    def see(self, shows: list[Show], now: datetime) -> bool:
        """Takes in what Home Assistant shows now; True when anything worth storing changed."""
        before = repr(self.data)
        self._renamed(shows)
        for s in shows:
            if valid(s.committed):
                self.data["committed"][s.node] = s.committed
        if not self.data["house"]:
            # A new house starts where its newest Show is, which is where the Shows came from: nothing moves.
            newest = ""
            for v in self.data["committed"].values():
                if valid(v) and (not newest or newer(v, newest)):
                    newest = v
            if newest:
                self.data["house"], self.data["since"] = newest, _iso(now)
        return repr(self.data) != before

    def _renamed(self, shows: list[Show]) -> None:
        # A renamed Show comes back under a new node name on the same device.
        devices = self.data["devices"]
        for s in shows:
            if not s.device:
                continue
            old = devices.get(s.device)
            devices[s.device] = s.node
            if old and old != s.node and old in self.data["committed"]:
                self.data["committed"].setdefault(s.node, self.data["committed"].pop(old))

    @property
    def version(self) -> str:
        return self.data["house"]

    @property
    def since(self) -> str:
        return self.data["since"]

    @property
    def channel(self) -> str:
        """The channel the house reads: the one picked, else the house version's own."""
        return self.data["channel"] or (channel_of(self.version) if self.version else "stable")

    def pick_channel(self, channel: str) -> None:
        if channel not in CHANNELS:
            raise ValueError(channel)
        self.data["channel"] = channel

    @property
    def night(self) -> bool:
        return bool(self.data["night"])

    def set_night(self, on: bool) -> None:
        self.data["night"] = on

    @property
    def llm(self) -> bool:
        return bool(self.data["llm"])

    def set_llm(self, on: bool) -> None:
        self.data["llm"] = on

    @property
    def before(self) -> str:
        return self.data["before"]

    def move(self, version: str, now: datetime) -> None:
        """Install: the house goes to version, up or down, and every Show follows."""
        if not valid(version):
            raise ValueError(version)
        if version != self.data["house"]:
            self.data["before"] = self.data["house"]
            self.data["house"], self.data["since"] = version, _iso(now)

    def forget(self, node: str) -> None:
        """A Show taken out of the house for good (the installer's --remove-show): nothing about it is
        kept."""
        self.data["committed"].pop(node, None)
        self.data["leaving"].pop(node, None)
        for device in [d for d, n in self.data["devices"].items() if n == node]:
            del self.data["devices"][device]
            self.data["music"].pop(device, None)
            self.data["switches"].pop(device, None)
            self.data["keys"].pop(device, None)
            self.data["off"].pop(device, None)

    def music_choice(self, device: str) -> str:
        return self.data["music"].get(device, "")

    def choose_music(self, device: str, player: str) -> None:
        """The Show's "Music plays on": a player, or "auto" (music.AUTO) to go back to the usual one."""
        if player and player != "auto":
            self.data["music"][device] = player
        else:
            self.data["music"].pop(device, None)

    def switch(self, device: str, key: str) -> bool | None:
        """A Show's switch as somebody set it; None while it is still at its default."""
        return self.data["switches"].get(device, {}).get(key)

    def set_switch(self, device: str, key: str, on: bool | None) -> None:
        """None forgets it, back to the default."""
        mine = self.data["switches"].setdefault(device, {})
        if on is None:
            mine.pop(key, None)
        else:
            mine[key] = on
        if not mine:
            del self.data["switches"][device]
