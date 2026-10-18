"""Which Show answers when several hear the same wake word (GENBU-224, wiki 734).

Home Assistant's own rule is first come: the first run with a wake phrase keeps it for 2 s and every other
one is refused, so whichever Show's model fired first answers, not the one being spoken to. A Show the
house has told to race (its wake_race action, every look) does not open its run at once. It says how well
it heard the word (esphome.genbu_wake) and waits for wake_verdict. This picks the one the voice was
loudest at and tells each one whether it won.

The window is 80 ms from the first Show that heard it: one openWakeWord step, the most two Shows hearing
the same word at the same moment can be apart. It closes as soon as every Show that can race has said, so
a house with one Show waits for nothing. A Show heard later is told it lost and logged with whether it
would have won, which is what the window gets tuned from.

Nothing in here waits or talks to Home Assistant: the coordinator does, and calls this with the time."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

WINDOW = 0.080  # seconds from the first Show that heard a wake word to the verdict
FORGET = 2.0  # after the verdict, how long a Show that heard the same phrase is late rather than new
STILL_TALKING = 30.0  # how long the Show that won keeps its bonus
STILL_TALKING_DB = 3.0
OVER_NOISE = 0.5  # weight of how far the voice stood out of the room's noise
CERTAINTY = 0.25  # weight of how far the model went over its cutoff, which is 0 to 6 dB
CERTAINTY_DB = 6.0
BARELY_HEARD_DB = 3.0  # less over its noise than this: it only answers when nobody else heard it
TIE_DB = 1.5
FLAG_DB = 10.0  # a winner another Show heard this much further over the noise is flagged
SILENCE_DB = -96.0  # the quietest a 16-bit microphone can say, and so the lowest level taken
# Per board, measured once with the same voice at the same distance (wiki 734). None measured yet.
BOARD_DB: dict[str, float] = {}


@dataclass
class Heard:
    """One Show's hearing of one wake word, as it said it, within what a microphone can say."""
    show: str  # Home Assistant's device id
    name: str
    wake: str  # the Show's id for this wake, which its verdict names
    phrase: str
    level_db: float
    floor_db: float
    certainty: float  # 0 to 1: how far the model went over its cutoff
    clipped: bool
    odd: bool = False  # something it said was out of range or missing, and was clamped
    model: str = ""

    @property
    def over_noise(self) -> float:
        return self.level_db - self.floor_db


def heard(show: str, name: str, data: dict, model: str = "") -> Heard | None:
    """A Show's esphome.genbu_wake, clamped to what a microphone can physically say: a Show that claims
    more than that does not win by it, and is marked odd. None without a wake id to answer."""
    wake = str(data.get("wake") or "")
    if not wake:
        return None
    odd = False

    def number(key: str, low: float, high: float) -> float:
        nonlocal odd
        try:
            v = float(data.get(key, ""))
        except (TypeError, ValueError):
            odd = True
            return low
        if math.isnan(v) or not low <= v <= high:
            odd = True
            return low if math.isnan(v) else min(max(v, low), high)
        return v

    level, floor = number("level_db", SILENCE_DB, 0), number("floor_db", SILENCE_DB, 0)
    score, cutoff = number("score", 0, 1), number("cutoff", 0, 1)
    clipped = str(data.get("clipped", "")).lower() == "true"
    if clipped:
        level = 0.0  # it ran out of room, so the voice was at least this loud
    certainty = min(max((score - cutoff) / (1 - cutoff), 0.0), 1.0) if cutoff < 1 else 0.0
    return Heard(show=show, name=name, wake=wake, phrase=str(data.get("phrase") or ""), level_db=level,
                 floor_db=min(floor, level), certainty=certainty, clipped=clipped, odd=odd, model=model)


def points(h: Heard, still_talking: bool = False) -> float:
    """The Show's score in dB: the voice's level, half how far it stood out of the noise, a quarter of
    how sure the model was, the board's offset, and the bonus for the Show still being talked to."""
    return (h.level_db + OVER_NOISE * h.over_noise + CERTAINTY * CERTAINTY_DB * h.certainty
            + BOARD_DB.get(h.model, 0.0) + (STILL_TALKING_DB if still_talking else 0.0))


def describe(h: Heard, last: str = "") -> str:
    """One Show's part of a race, for the log the race gets tuned from."""
    notes = "".join(n for n, on in ((", clipped", h.clipped), (", odd", h.odd), (", still talking", h.show == last))
                    if on)
    return (f"{h.name} {points(h, h.show == last):.1f} (level {h.level_db:.1f} dBFS, {h.over_noise:.1f} dB over "
            f"its noise, sure {h.certainty:.2f}{notes})")


def pick(everyone: list[Heard], last: str = "") -> Heard:
    """The Show that answers: the best score, from the Shows that heard it clearly when any did. Within
    TIE_DB of the best: the one last used, then the one further over its noise, then by name."""
    clear = [h for h in everyone if h.over_noise >= BARELY_HEARD_DB] or everyone
    score = {h.wake: points(h, h.show == last) for h in clear}
    best = max(score.values())
    close = [h for h in clear if best - score[h.wake] <= TIE_DB]
    return min(close, key=lambda h: (h.show != last, -h.over_noise, h.name.lower(), h.show))


@dataclass
class Race:
    phrase: str
    opened: float
    everyone: list[Heard] = field(default_factory=list)
    winner: Heard | None = None
    closed: float = 0.0
    last: str = ""  # the Show still being talked to when it closed


@dataclass
class Verdict:
    winner: Heard
    losers: list[Heard]
    waited: float  # seconds from the first Show that heard it
    flagged: Heard | None = None  # a Show that heard it far more clearly than the winner


class Referee:
    """The races being run, by wake phrase: Home Assistant's own rule is per phrase too."""

    def __init__(self) -> None:
        self.races: dict[str, Race] = {}
        self.last: tuple[str, float] = ("", -math.inf)  # the Show that last won, and when

    def still_talking(self, now: float) -> str:
        show, at = self.last
        return show if now - at <= STILL_TALKING else ""

    def hear(self, h: Heard, racers: set[str], now: float) -> str:
        """Takes a Show's hearing. "open": the first, a race starts and closes in WINDOW. "close": every
        Show that can race has said, close it now. "wait": more may come. "late": the verdict is out;
        the Show has lost (would_have_won says whether it would have won)."""
        for phrase, r in list(self.races.items()):
            if r.winner is not None and now - r.closed > FORGET:
                del self.races[phrase]
        r = self.races.get(h.phrase)
        if r is not None and r.winner is not None:
            r.everyone.append(h)
            return "late"
        new = r is None
        if new:
            r = self.races[h.phrase] = Race(phrase=h.phrase, opened=now)
        if all(e.show != h.show for e in r.everyone):
            r.everyone.append(h)
        if racers <= {e.show for e in r.everyone}:
            return "close"
        return "open" if new else "wait"

    def close(self, phrase: str, now: float) -> Verdict | None:
        """The verdict, once; None when there is no race open for the phrase."""
        r = self.races.get(phrase)
        if r is None or r.winner is not None:
            return None
        r.last = self.still_talking(now)
        r.winner = pick(r.everyone, r.last)
        r.closed = now
        self.last = (r.winner.show, now)
        flagged = max((h for h in r.everyone if h is not r.winner), key=lambda h: h.over_noise, default=None)
        if flagged is not None and flagged.over_noise - r.winner.over_noise < FLAG_DB:
            flagged = None
        return Verdict(winner=r.winner, losers=[h for h in r.everyone if h is not r.winner],
                       waited=now - r.opened, flagged=flagged)

    def would_have_won(self, h: Heard) -> bool:
        """Whether a late Show would have answered, had it been in time."""
        r = self.races.get(h.phrase)
        if r is None or r.winner is None:
            return False
        return pick([e for e in r.everyone if e is not h] + [h], r.last) is h
