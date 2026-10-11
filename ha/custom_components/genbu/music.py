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

    @property
    def show(self) -> bool:
        return self.maker in SHOW_MAKERS


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def own_player(names: list[str], players: list[Player]) -> str:
    """The Show's own Music Assistant player. Music Assistant makes up its own id for it, so it is
    found by the name the Show gave it, or by its entity id being the Show's node name."""
    want = {_flat(n) for n in names if n}
    for p in sorted(players, key=lambda p: p.entity):
        if p.show and (_flat(p.device_name) in want or _flat(p.entity.split(".", 1)[-1]) in want):
            return p.entity
    return ""


def room_player(area: str, players: list[Player]) -> str:
    """The room's own speaker: a Music Assistant player in the area that is not a Show, one that is
    there before one that is not."""
    here = sorted((not p.available, p.entity) for p in players if area and p.area == area and not p.show)
    return here[0][1] if here else ""


def plays_on(chosen: str, area: str, names: list[str], players: list[Player]) -> str:
    """Where this Show's music goes: the picked player, else the room's speaker, else the Show itself,
    the first of them that is not offline. When all of them are, the first; empty when there is none."""
    by = {p.entity: p for p in players}
    picked = chosen if chosen and chosen != AUTO and chosen in by else ""
    line = [e for e in (picked, room_player(area, players), own_player(names, players)) if e]
    return next((e for e in line if by[e].available), line[0] if line else "")
