"""Genbu Assistant as a conversation agent (GENBU-253): it stands in front of the agent picked in Genbu's
options and answers what fragment.py calls a fragment itself, with "Das habe ich nicht verstanden." or "I
didn't catch that.". Everything else goes on to that agent unchanged, in the same conversation, so its
reply still streams and its tools still run.

When that agent's reply says it switched something it was asked to and no tool ran in the turn, Genbu says
"Ich habe nichts geschaltet." or "I didn't switch anything." after it (GENBU-255, claims.py). A short reply
isn't spoken until the turn ends, so the correction is said in its place. A longer one has already been read
out while the model was writing it; the correction is then said after it.

It is there only while an agent is picked, and does something only when an Assist pipeline uses it. Typed
words are never a fragment: only a voice device's speech-to-text makes them."""

from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_SUPPORTED_FEATURES, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from . import claims, fragment
from .const import CONF_AGENT, DOMAIN, NAME
from .coordinator import house_device

_LOGGER = logging.getLogger(__name__)

# The pipeline reads a reply out while it is written once it is longer than this (STREAM_RESPONSE_CHARS
# in assist_pipeline), and otherwise says the turn's speech at the end.
STREAMED = 60


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add([GenbuAgent(entry)])  # __init__.py sets this platform up only while an agent is picked


def is_genbu(hass: HomeAssistant, agent: str) -> bool:
    """Whether agent is Genbu's own, which can't be the one it hands on to."""
    found = er.async_get(hass).async_get(agent)
    return found is not None and found.platform == DOMAIN


def _lang(language: str | None) -> str | None:
    language = (language or "").lower()
    return next((lang for lang in fragment.REPLY if language.startswith(lang)), None)


class GenbuAgent(conversation.ConversationEntity):
    _attr_name = NAME  # a name of its own, which the pipeline's agent list shows

    def __init__(self, entry: ConfigEntry) -> None:
        self.agent = entry.options[CONF_AGENT]
        self._attr_unique_id = f"{entry.entry_id}_agent"
        self._attr_device_info = house_device(entry)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    @property
    def supports_streaming(self) -> bool:
        info = conversation.async_get_agent_info(self.hass, self.agent)
        return bool(info and info.supports_streaming)

    @property
    def supported_features(self) -> conversation.ConversationEntityFeature:
        # The pipeline reads CONTROL from here to know the agent controls the house itself.
        state = self.hass.states.get(self.agent)
        return conversation.ConversationEntityFeature(
            (state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0) if state else 0)

    def _rooms(self) -> set[str]:
        return {a.name.casefold() for a in ar.async_get(self.hass).async_list_areas()}

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        lang = _lang(user_input.language)
        if (lang and (user_input.device_id or user_input.satellite_id)
                and fragment.is_fragment(user_input.text, fragment.asked(chat_log.content), self._rooms())):
            _LOGGER.debug("answered %r itself, as a fragment", user_input.text)
            reply = fragment.REPLY[lang]
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=self.entity_id, content=reply))
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech(reply)
            return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
        if is_genbu(self.hass, self.agent) or conversation.async_get_agent_info(self.hass, self.agent) is None:
            raise HomeAssistantError(f"Genbu Assistant can't hand on to {self.agent}; pick another agent in "
                                     "its options")
        result = await conversation.async_converse(
            self.hass, user_input.text, chat_log.conversation_id, user_input.context,
            language=user_input.language, agent_id=self.agent, device_id=user_input.device_id,
            satellite_id=user_input.satellite_id, extra_system_prompt=user_input.extra_system_prompt)
        asked = max((i for i, c in enumerate(chat_log.content) if c.role == "user"), default=-1)
        said = claims.to_correct(user_input.text, chat_log.content[asked + 1:])
        if lang and said:
            await self._correct(chat_log, result, said.content, claims.CORRECTION[lang])
        return result

    async def _correct(self, chat_log: conversation.ChatLog, result: conversation.ConversationResult,
                       said: str, reply: str) -> None:
        _LOGGER.debug("took back %r", said)
        if self.supports_streaming and len(said) > STREAMED:
            # Already being read out: the correction goes the same way, after it.
            async def stream() -> AsyncIterator[conversation.AssistantContentDeltaDict]:
                yield {"role": "assistant"}
                yield {"content": reply}

            async for _ in chat_log.async_add_delta_content_stream(self.entity_id, stream()):
                pass
            result.response.async_set_speech(f"{said} {reply}")
        else:
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=self.entity_id, content=reply))
            result.response.async_set_speech(reply)
