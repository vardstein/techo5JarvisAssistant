"""Where a Show's music plays, without Home Assistant. Each Show has its own answer, so music asked for
in the bedroom plays in the bedroom: what was picked for it in its "Music plays on" select, else the
room's own speaker, else the Show itself. Never a speaker in another room unless it was picked. One
that is offline is passed over, so the Show itself is always there to fall back on while it is up.

The glue in coordinator.py feeds it the Music Assistant players and the Shows as Home Assistant has
them now, so a Show moved to another room plays there from the next look on."""

from __future__ import annotations

import re
from dataclasses import dataclass

AUTO = "auto"  # what the select stores when nothing was picked

# What a Show reports as its maker to Music Assistant over Sendspin (echod's layout.Manufacturer).
# Players with these makers are Shows, never "the room's speaker".
SHOW_MAKERS = {"OpenJade", "TECHO5"}


@dataclass
class Player:
    """A Music Assistant media player as Home Assistant has it."""

    entity: str
    name: str = ""  # its friendly name
    area: str = ""  # its own area, else its device's
    maker: str = ""  # its device's manufacturer
    device_name: str = ""  # its device's name, which for a Show is the name echod gave Sendspin
    available: bool = True
    mac: str = ""  # the MAC Music Assistant has for its device, where it knows one

    @property
    def show(self) -> bool:
        return self.maker in SHOW_MAKERS


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def own_player(names: list[str], players: list[Player], mac: str = "") -> str:
    """The Show's own Music Assistant player. Music Assistant makes up its own id for it, so it is
    found by the Show's MAC, which echod gives Sendspin and a rename doesn't change. Where Music
    Assistant knows no MAC, by the name the Show gave it, or by its entity id being the Show's node
    name; a renamed Show gives Sendspin and ESPHome its new name, so those two miss it after a rename."""
    shows = sorted((p for p in players if p.show), key=lambda p: p.entity)
    if mac and (same := next((p.entity for p in shows if _flat(p.mac) == _flat(mac)), "")):
        return same
    want = {_flat(n) for n in names if n}
    return next((p.entity for p in shows
                 if _flat(p.device_name) in want or _flat(p.entity.split(".", 1)[-1]) in want), "")


def room_player(area: str, players: list[Player]) -> str:
    """The room's own speaker: a Music Assistant player in the area that is not a Show, one that is
    there before one that is not."""
    here = sorted((not p.available, p.entity) for p in players if area and p.area == area and not p.show)
    return here[0][1] if here else ""


def _line(chosen: str, area: str, names: list[str], players: list[Player], mac: str = "") -> list[str]:
    """The players this Show's music may go to, in order: the picked one, the room's, the Show's own."""
    picked = chosen if chosen and chosen != AUTO and chosen in {p.entity for p in players} else ""
    line: list[str] = []
    for e in (picked, room_player(area, players), own_player(names, players, mac)):
        if e and e not in line:
            line.append(e)
    return line


def plays_on(chosen: str, area: str, names: list[str], players: list[Player], mac: str = "") -> str:
    """Where this Show's music goes: the picked player, else the room's speaker, else the Show itself,
    the first of them that is not offline. When all of them are, the first; empty when there is none."""
    up = {p.entity for p in players if p.available}
    line = _line(chosen, area, names, players, mac)
    return next((e for e in line if e in up), line[0] if line else "")


def falls_back_to(chosen: str, area: str, names: list[str], players: list[Player], mac: str = "") -> str:
    """Where the music goes when the player it plays on now goes offline: the next one in line that is
    up, else the Show itself, which is the end of the line. Empty when the Show has no player."""
    up = {p.entity for p in players if p.available}
    line = _line(chosen, area, names, players, mac)
    now = plays_on(chosen, area, names, players, mac)
    after = line[line.index(now) + 1:] if now in line else []
    return next((e for e in after if e in up), own_player(names, players, mac))
