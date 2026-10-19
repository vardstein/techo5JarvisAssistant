"""What the voice assistant isn't asked to answer (GENBU-253): one word on its own, like "Nein?", "Fiktisch."
or "Deckel.", that a Show's speech-to-text made of the TV or of half a sentence. The prompt asked the model to
say "Das habe ich nicht verstanden." to these, and qwen3.5:4b did about half the time; the rest it argued
with. Genbu's agent (conversation.py) answers them in code, and the model never sees them.

It is careful the other way round: two words or more always go on, and so does one word that answers a
question the assistant just asked ("Ja", "Nein", "Zwei"), one that asks for something on its own ("Stopp",
"Wetter", "Danke") and the name of a room. Which single words people really say is a guess until the
conversation traces show it; REQUESTS is where they go."""

from __future__ import annotations

import re

WORD = re.compile(r"\w+")

# One word that is a request by itself, as the speech-to-text writes it, folded to lower case.
REQUESTS = frozenset({
    # German
    "stopp", "stop", "halt", "pause", "weiter", "lauter", "leiser", "abbrechen", "danke", "hilfe",
    "hallo", "hi", "hey", "moin", "servus", "morgen", "tschüss", "wetter", "uhrzeit", "zeit", "datum",
    "timer", "wecker", "musik", "licht", "nachrichten", "temperatur", "kalender", "rezept", "update",
    "version",
    # English
    "resume", "continue", "louder", "quieter", "cancel", "thanks", "help", "hello", "morning", "bye",
    "weather", "time", "date", "alarm", "music", "light", "lights", "news", "temperature", "calendar",
    "recipe",
})

REPLY = {"de": "Das habe ich nicht verstanden.", "en": "I didn't catch that."}


def words(text: str) -> list[str]:
    return WORD.findall(str(text).casefold())


def is_fragment(text: str, asked: bool, rooms: set[str] = frozenset()) -> bool:
    """Whether text is a fragment to answer with REPLY. asked: the assistant's last reply ended in a
    question, so a single word may be the answer to it. rooms: the house's area names, folded."""
    said = words(text)
    if not said:
        return True
    if asked or len(said) > 1:
        return False
    return said[0] not in REQUESTS and said[0] not in rooms


def asked(contents: list) -> bool:
    """Whether the assistant's last reply before this turn's words ended in a question."""
    for content in reversed(contents[:-1] if contents and contents[-1].role == "user" else contents):
        if content.role == "assistant" and content.content:
            return content.content.rstrip().endswith("?")
        if content.role == "user":
            return False
    return False
