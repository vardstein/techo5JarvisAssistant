"""GENBU-253: a lone word from a voice device is answered by Genbu's own agent, and nothing else is."""

from homeassistant.components import conversation
from homeassistant.components.conversation.const import DATA_COMPONENT
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS, AssistantContent, UserContent
from homeassistant.const import MATCH_ALL
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.genbu import fragment

DOMAIN = "genbu"
AGENT = "conversation.genbu_assistant"
MODEL = "conversation.model"
SHOW = "show-device"


def test_one_word_alone_is_a_fragment():
    for said in ("Nein?", "Fiktisch.", "Deckel.", "  ", "…", "21"):
        assert fragment.is_fragment(said, asked=False), said
    for said in ("Stopp!", "Wetter?", "Danke.", "lights", "Mach das Licht an.", "Deckel zu", "Ja, bitte"):
        assert not fragment.is_fragment(said, asked=False), said
    assert not fragment.is_fragment("Nein.", asked=True)
    assert not fragment.is_fragment("Küche", asked=False, rooms={"küche"})


def test_asked_is_the_last_reply_before_the_words():
    model = AssistantContent(agent_id=MODEL, content="Soll ich das Update installieren? ")
    assert fragment.asked([UserContent(content="Update?"), model, UserContent(content="Ja")])
    assert not fragment.asked([model, UserContent(content="Ja"), AssistantContent(agent_id=MODEL, content="Gut."),
                               UserContent(content="Nein")])
    assert not fragment.asked([UserContent(content="Nein")])


class Model(conversation.ConversationEntity):
    """A streaming agent that controls the house and says back what it got."""

    _attr_name = "Model"
    _attr_unique_id = "model"
    _attr_supports_streaming = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL
    supported_languages = MATCH_ALL

    def __init__(self) -> None:
        self.heard: list[str] = []
        self.reply = "Gemacht."

    async def _async_handle_message(self, user_input, chat_log):
        self.heard.append(user_input.text)
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=user_input.agent_id, content=self.reply))
        return conversation.async_get_result_from_chat_log(user_input, chat_log)


async def setup(hass: HomeAssistant, options: dict | None = None) -> tuple[MockConfigEntry, Model]:
    model = Model()
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "conversation", {})
    await hass.data[DATA_COMPONENT].async_add_entities([model])
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN,
                            options={"conversation_agent": MODEL} if options is None else options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, model


async def say(hass: HomeAssistant, text: str, conversation_id: str | None = None, device: str | None = SHOW,
              language: str = "de") -> conversation.ConversationResult:
    return await conversation.async_converse(hass, text, conversation_id, Context(), language=language,
                                             agent_id=AGENT, device_id=device)


def speech(result: conversation.ConversationResult) -> str:
    return result.response.speech["plain"]["speech"]


async def test_a_fragment_never_reaches_the_model(hass: HomeAssistant):
    _, model = await setup(hass)
    result = await say(hass, "Fiktisch.")
    assert speech(result) == "Das habe ich nicht verstanden."
    assert not result.continue_conversation
    assert speech(await say(hass, "Deckel.", language="en")) == "I didn't catch that."
    assert model.heard == []
    log = hass.data[DATA_CHAT_LOGS][result.conversation_id].content
    assert [(c.role, c.content) for c in log[-2:]] == [("user", "Fiktisch."),
                                                       ("assistant", "Das habe ich nicht verstanden.")]


async def test_everything_else_goes_on_in_the_same_conversation(hass: HomeAssistant):
    _, model = await setup(hass)
    ar.async_get(hass).async_create("Küche")
    first = await say(hass, "Mach das Licht an.")
    assert speech(first) == "Gemacht."
    model.reply = "Soll ich das Update installieren?"
    await say(hass, "Ist ein Update da?", first.conversation_id)
    model.reply = "Gemacht."
    assert speech(await say(hass, "Nein.", first.conversation_id)) == "Gemacht."  # the answer to a question
    await say(hass, "Küche", first.conversation_id)
    await say(hass, "Fiktisch.", device=None)  # typed
    await say(hass, "Fiktisch.", language="fr")  # no reply in that language
    assert model.heard == ["Mach das Licht an.", "Ist ein Update da?", "Nein.", "Küche", "Fiktisch.", "Fiktisch."]
    log = hass.data[DATA_CHAT_LOGS][first.conversation_id].content
    assert [c.role for c in log[1:]] == ["user", "assistant"] * 4  # each turn's words once
    assert log[2].agent_id == MODEL


async def test_it_streams_and_controls_like_the_model(hass: HomeAssistant):
    await setup(hass)
    info = conversation.async_get_agent_info(hass, AGENT)
    assert (info.name, info.supports_streaming) == ("Genbu Assistant", True)
    assert hass.states.get(AGENT).attributes["supported_features"] == conversation.ConversationEntityFeature.CONTROL


async def test_no_agent_picked_no_agent(hass: HomeAssistant):
    await setup(hass, options={})
    assert conversation.async_get_agent_info(hass, AGENT) is None


async def test_the_options_pick_it_and_never_itself(hass: HomeAssistant):
    entry, _ = await setup(hass, options={})
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"conversation_agent": MODEL})
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options["conversation_agent"] == MODEL
    assert hass.states.get(AGENT) is not None
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"conversation_agent": AGENT})
    assert result["errors"] == {"conversation_agent": "own_agent"}
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    assert "conversation_agent" not in entry.options
    assert conversation.async_get_agent_info(hass, AGENT) is None
