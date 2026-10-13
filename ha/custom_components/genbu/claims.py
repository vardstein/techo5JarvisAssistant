"""Catches the voice assistant saying it did something it never did (GENBU-139). Asked "Macht die Musik im
Schlafzimmer aus", the model once answered that the music was off without calling a single tool. What
was said can't be taken back, but it can be seen: when a language model ends a turn with a reply that
says something was switched, paused or set, and no tool ran in that turn, Genbu logs a warning and fires
genbu_unbacked_claim with what was asked and what was said. Home Assistant's own sentences and the local
ones are left out; they do what they say.

Home Assistant tells subscribers about every user turn, but not when a new conversation's model reply
lands, so the finished turn is read from the stored chat log once it is there."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback

try:  # Home Assistant 2025.8 and later
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS, async_subscribe_chat_logs
except ImportError:
    async_subscribe_chat_logs = None

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

EVENT = "genbu_unbacked_claim"
LOCAL_AGENTS = {"conversation.home_assistant", "homeassistant"}
# How often the stored log is looked at, and for how long: a local model can take a while.
STEP = 1.0
WAIT = 180

# Words that say something was done or is so: "Licht ist aus.", "Ich habe die Musik angehalten.",
# "Läuft.", "Paused.", "The light is on."
CLAIM = re.compile(
    r"\b(ist|sind) (jetzt |nun )?(aus|an|zu|offen|auf)\b"
    r"|\b(aus|an|ein|um)geschaltet\b"
    r"|\b(angehalten|pausiert|gestoppt|gestartet|gedimmt|geöffnet|geschlossen|hochgedreht|runtergedreht"
    r"|heruntergedreht|eingestellt|gestellt|erledigt)\b"
    r"|^läuft\b"
    r"|\b(turned|switched) (on|off)\b|\b(is|are) (now )?(on|off|open|closed)\b"
    r"|\b(paused|stopped|started|dimmed|done)\b",
    re.IGNORECASE)


def unbacked(contents: list, asked_at: Any) -> tuple[bool, tuple[str, str, str] | None]:
    """Whether the turn asked at asked_at is in contents yet, and if so (agent, asked, said) when it ended
    in a reply that claims something with no tool run in the turn."""
    start = next((i for i, c in enumerate(contents) if c.role == "user" and c.created == asked_at), None)
    if start is None:
        return False, None
    turn = contents[start + 1:]
    if any(c.role == "tool_result" or (c.role == "assistant" and c.tool_calls) for c in turn):
        return True, None
    said = next((c for c in reversed(turn) if c.role == "assistant" and c.content), None)
    if said is None or said.agent_id in LOCAL_AGENTS or not CLAIM.search(said.content.strip()):
        return True, None
    return True, (said.agent_id, contents[start].content, said.content)


@callback
def async_watch(hass: HomeAssistant) -> Callable[[], None]:
    """Watches every conversation; returns what stops it."""
    if async_subscribe_chat_logs is None:
        return lambda: None
    tasks: set[asyncio.Task] = set()

    @callback
    def seen(conversation_id: str, kind: str, data: dict[str, Any]) -> None:
        content = data.get("content") or {}
        if kind != "content_added" or content.get("role") != "user":
            return
        task = hass.async_create_background_task(_check(hass, conversation_id, content.get("created")),
                                                 f"{DOMAIN} claim check")
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    unsubscribe = async_subscribe_chat_logs(hass, seen)

    @callback
    def stop() -> None:
        unsubscribe()
        for task in list(tasks):
            task.cancel()
    return stop


async def _check(hass: HomeAssistant, conversation_id: str, asked_at: Any) -> None:
    waited = 0.0
    while waited < WAIT:
        await asyncio.sleep(STEP)
        waited += STEP
        log = (hass.data.get(DATA_CHAT_LOGS) or {}).get(conversation_id)
        if log is None:
            continue
        done, found = unbacked(log.content, asked_at)
        if not done:
            continue
        if found:
            agent, asked, said = found
            _LOGGER.warning("%s said %r to %r, but no tool ran in that turn", agent, said, asked)
            hass.bus.async_fire(EVENT, {"agent_id": agent, "conversation_id": conversation_id,
                                        "asked": asked, "said": said})
        return
