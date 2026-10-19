"""GENBU-255: under Genbu Assistant, a reply that says something was switched when no tool ran is taken back."""

from homeassistant.components import conversation
from homeassistant.components.conversation.const import DATA_COMPONENT
from homeassistant.components.conversation.chat_log import (DATA_CHAT_LOGS, AssistantContent, ToolResultContent,
                                                            async_get_chat_log)
from homeassistant.const import MATCH_ALL
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session, llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.genbu import claims

DOMAIN = "genbu"
AGENT = "conversation.genbu_assistant"
MODEL = "conversation.model"
SHOW = "show-device"


class Model(conversation.ConversationEntity):
    """A streaming agent that controls the house, answering with reply, after a tool when tool is set."""

    _attr_name = "Model"
    _attr_unique_id = "model"
    _attr_supports_streaming = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL
    supported_languages = MATCH_ALL

    def __init__(self) -> None:
        self.reply = ""
        self.tool = False

    async def _async_handle_message(self, user_input, chat_log):
        if self.tool:
            call = llm.ToolInput(tool_name="HassTurnOn", tool_args={"name": "Bett"}, external=True)
            chat_log.async_add_assistant_content_without_tools(AssistantContent(agent_id=MODEL, tool_calls=[call]))
            chat_log.async_add_assistant_content_without_tools(
                ToolResultContent(agent_id=MODEL, tool_call_id=call.id, tool_name="HassTurnOn",
                                  result=llm.ToolResult(data={"success": True})))
        chat_log.async_add_assistant_content_without_tools(AssistantContent(agent_id=MODEL, content=self.reply))
        return conversation.async_get_result_from_chat_log(user_input, chat_log)


async def setup(hass: HomeAssistant) -> Model:
    model = Model()
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "conversation", {})
    await hass.data[DATA_COMPONENT].async_add_entities([model])
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN, options={"conversation_agent": MODEL})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return model


async def say(hass: HomeAssistant, text: str, language: str = "de") -> conversation.ConversationResult:
    return await conversation.async_converse(hass, text, None, Context(), language=language, agent_id=AGENT,
                                             device_id=SHOW)


def speech(result: conversation.ConversationResult) -> str:
    return result.response.speech["plain"]["speech"]


def last(hass: HomeAssistant, result: conversation.ConversationResult) -> list[tuple[str, str | None]]:
    return [(c.role, c.content) for c in hass.data[DATA_CHAT_LOGS][result.conversation_id].content[-3:]]


def test_what_counts_as_a_switch_command():
    for asked in ("Bett einschalten.", "Mach das Licht im Bad an, bitte nicht", "Kannst du die Musik ausmachen?",
                  "Licht an", "Schalte das Radio aus", "Turn off the light", "Please open the blinds"):
        assert claims.COMMAND.search(asked) and not claims.QUESTION.match(asked), asked
    for asked in ("Ist das Licht an?", "Wie spät ist es?", "Stell dir vor", "Is the light on?", "Erzähl was"):
        assert not claims.COMMAND.search(asked) or claims.QUESTION.match(asked), asked


async def test_a_switch_it_never_ran_is_taken_back(hass: HomeAssistant):
    model = await setup(hass)
    # Both from the ticket: no tool ran, and nothing changed.
    model.reply = "Ich habe das Licht in deinem Schlafzimmer eingeschaltet."
    result = await say(hass, "Bett einschalten.")
    assert speech(result) == "Ich habe nichts geschaltet."
    assert last(hass, result) == [("user", "Bett einschalten."), ("assistant", model.reply),
                                  ("assistant", "Ich habe nichts geschaltet.")]
    model.reply = "Licht ist im Bad an."
    assert speech(await say(hass, "Mach das Licht im Bad an, bitte nicht")) == "Ich habe nichts geschaltet."
    model.reply = "The light is now on."
    assert speech(await say(hass, "Turn on the light", language="en")) == "I didn't switch anything."


async def test_a_switch_that_ran_questions_and_honest_replies_are_left_alone(hass: HomeAssistant):
    model = await setup(hass)
    model.tool = True
    model.reply = "Das Bett ist jetzt an."
    assert speech(await say(hass, "Bett einschalten.")) == model.reply
    model.tool = False
    for asked, model.reply in (("Ist das Licht im Bad an?", "Das Licht im Bad ist an."),
                               ("Bett einschalten.", "Welches Bett meinst du?"),
                               ("Bett einschalten.", "Das Bett kann ich leider nicht einschalten."),
                               ("Mach die Musik aus.", "Okay.")):
        result = await say(hass, asked)
        assert speech(result) == model.reply, asked
        assert last(hass, result)[-1] == ("assistant", model.reply)


async def test_a_long_reply_already_read_out_gets_the_correction_after_it(hass: HomeAssistant):
    model = await setup(hass)
    model.reply = "Ich habe das Licht in deinem Schlafzimmer und im Flur für dich eingeschaltet."
    heard = []
    with (chat_session.async_get_chat_session(hass) as session,
          async_get_chat_log(hass, session, chat_log_delta_listener=lambda _, delta: heard.append(delta))):
        result = await conversation.async_converse(hass, "Mach das Licht an", session.conversation_id, Context(),
                                                   language="de", agent_id=AGENT, device_id=SHOW)
    assert speech(result) == f"{model.reply} Ich habe nichts geschaltet."
    assert heard[-2:] == [{"role": "assistant"}, {"content": "Ich habe nichts geschaltet."}]


async def test_the_event_says_it_was_taken_back(hass: HomeAssistant, monkeypatch):
    monkeypatch.setattr(claims, "STEP", 0.01)
    monkeypatch.setattr(claims, "WAIT", 1)
    fired = []
    hass.bus.async_listen(claims.EVENT, lambda e: fired.append(e.data))
    model = await setup(hass)
    model.reply = "Ich habe das Licht eingeschaltet."
    await say(hass, "Bett einschalten.")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [(e["agent_id"], e["said"], e["corrected"]) for e in fired] == [(MODEL, model.reply, True)]
