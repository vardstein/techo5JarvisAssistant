"""GENBU-254: a delayed command Home Assistant can't run is refused under a model agent too, as under its own."""

from homeassistant.components import conversation
from homeassistant.components.conversation.const import DATA_COMPONENT
from homeassistant.components.intent.timers import TIMER_DATA, StartTimerIntentHandler, async_register_timer_handler
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session, intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.genbu.delayed import CheckedStartTimer

DOMAIN = "genbu"
MODEL = "conversation.model"
SHOW = "show-device"


class Model(conversation.ConversationEntity):
    """A model agent that controls the house, as the pipeline's agent."""

    _attr_name = "Model"
    _attr_unique_id = "model"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL
    supported_languages = "*"


async def setup(hass: HomeAssistant, genbu: bool = True) -> MockConfigEntry | None:
    hass.config.language = "de"
    for component in ("homeassistant", "intent", "conversation"):
        assert await async_setup_component(hass, component, {})
    await hass.data[DATA_COMPONENT].async_add_entities([Model()])
    async_register_timer_handler(hass, SHOW, lambda *_: None)
    if not genbu:
        return None
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def say(hass: HomeAssistant, text: str) -> str:
    """text as the pipeline handles it locally first, with "Prefer handling commands locally" on."""
    user_input = conversation.ConversationInput(text=text, context=Context(), conversation_id=None, device_id=SHOW,
                                                satellite_id=None, language="de", agent_id=MODEL)
    with (chat_session.async_get_chat_session(hass) as session,
          conversation.async_get_chat_log(hass, session, user_input) as chat_log):
        response = await conversation.async_handle_intents(hass, user_input, chat_log)
    assert response is not None, text
    return response.speech["plain"]["speech"]


def commands(hass: HomeAssistant) -> list[str | None]:
    return [t.conversation_command for t in hass.data[TIMER_DATA].timers.values()]


def handler(hass: HomeAssistant) -> intent.IntentHandler:
    return next(h for h in intent.async_get(hass) if h.intent_type == intent.INTENT_START_TIMER)


async def test_without_genbu_the_misheard_timer_is_a_delayed_command(hass: HomeAssistant):
    await setup(hass, genbu=False)
    await say(hass, "Ich stelle einen Deckel für in 10 Minuten.")
    assert commands(hass) == ["Ich stelle einen Deckel für"]  # what happened on 2026-10-19: nothing rang


async def test_a_command_home_assistant_cant_run_is_refused(hass: HomeAssistant):
    await setup(hass)
    assert isinstance(handler(hass), CheckedStartTimer)
    assert await say(hass, "Ich stelle einen Deckel für in 10 Minuten.") == "Entschuldigung, das habe ich nicht verstanden"
    assert commands(hass) == []


async def test_timers_and_commands_it_can_run_still_start(hass: HomeAssistant):
    await setup(hass)
    await say(hass, "Stelle einen Timer für 10 Minuten")
    await say(hass, "Wie spät ist es in 10 Minuten")
    assert commands(hass) == [None, "Wie spät ist es"]


async def test_unloading_puts_home_assistants_handler_back(hass: HomeAssistant):
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert type(handler(hass)) is StartTimerIntentHandler
    await say(hass, "Ich stelle einen Deckel für in 10 Minuten.")
    assert commands(hass) == ["Ich stelle einen Deckel für"]
