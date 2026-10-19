"""Delayed commands Home Assistant can't run are refused, whatever agent the pipeline uses (GENBU-254).

"Mach das Licht aus in 10 Minuten" starts a timer that runs "Mach das Licht aus" when it ends. Home Assistant
takes any words in front of "in 10 Minuten" as that command, so a timer Whisper misheard, "Ich stelle einen
Deckel für in 10 Minuten.", became a delayed command: no timer on the Show, nothing rang, and ten minutes
later the words went to the agent with nobody listening. Home Assistant checks the command first, and refuses
it with "Das habe ich nicht verstanden", but only when its own agent runs the pipeline. With a model agent
and "Prefer handling commands locally" the timer sentence is still handled locally, under the model's name,
so the check is skipped.

Genbu puts in Home Assistant's own timer handler with that one check changed: the command is checked against
Home Assistant's own sentences for every agent, as it is for its own. A command only the model understands
is refused too; it would run with nobody hearing the answer anyway. The original handler goes back when Genbu
is unloaded."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components.intent.timers import StartTimerIntentHandler
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent

_LOGGER = logging.getLogger(__name__)


class CheckedStartTimer(StartTimerIntentHandler):
    """Home Assistant's handler, checking a delayed command for every agent."""

    async def _validate_conversation_command(self, intent_obj: intent.Intent, conversation_command: str) -> bool:
        asked_by = intent_obj.conversation_agent_id
        intent_obj.conversation_agent_id = None  # Home Assistant's own agent, which it checks against
        try:
            return await super()._validate_conversation_command(intent_obj, conversation_command)
        finally:
            intent_obj.conversation_agent_id = asked_by


@callback
def async_check(hass: HomeAssistant) -> Callable[[], None]:
    """Put CheckedStartTimer in place of Home Assistant's timer handler; returns what puts it back."""
    original = next((h for h in intent.async_get(hass) if h.intent_type == intent.INTENT_START_TIMER), None)
    if type(original) is not StartTimerIntentHandler:
        # No voice assistant, or another integration replaced it: leave it alone.
        _LOGGER.debug("timer handler left as it is: %s", original)
        return lambda: None
    checked = CheckedStartTimer()
    intent.async_register(hass, checked)

    @callback
    def put_back() -> None:
        if next((h for h in intent.async_get(hass) if h.intent_type == intent.INTENT_START_TIMER), None) is checked:
            intent.async_register(hass, original)

    return put_back
