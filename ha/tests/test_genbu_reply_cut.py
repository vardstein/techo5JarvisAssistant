"""A reply cut off by a wake word (GENBU-250): the Show's event, the words asked for over the encrypted link,
and the one turn after it that is told where the reply was cut."""

from datetime import timedelta

from homeassistant.components.llm import async_get_tools
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.genbu import reply_cut

from test_genbu_ha import add_show
from test_genbu_race import device, encrypt

DOMAIN = "genbu"
HEARD = "The kitchen is at 21 degrees"


def cut_event(hass: HomeAssistant, node: str) -> str:
    """The Show's reply_cut event entity, as its firmware brings it."""
    entities = er.async_get(hass)
    up = entities.async_get(f"update.{node}_firmware")
    e = entities.async_get_or_create("event", "esphome", f"aa:{node}-event-reply_cut", device_id=up.device_id,
                                     config_entry=hass.config_entries.async_get_entry(up.config_entry_id),
                                     suggested_object_id=f"{node}_reply_cut")
    hass.states.async_set(e.entity_id, "unknown", {"event_type": None, "event_types": ["cut"]})
    return e.entity_id


def answers(hass: HomeAssistant, node: str, answer: dict) -> list:
    """The Show's reply_cut action; returns the calls it gets."""
    calls = []

    def reply(call):
        calls.append(call)
        return answer

    hass.services.async_register("esphome", f"{node}_reply_cut", reply, supports_response=SupportsResponse.ONLY)
    return calls


def fire(hass: HomeAssistant, entity: str) -> None:
    hass.states.async_set(entity, dt_util.utcnow().isoformat(), {"event_type": "cut", "event_types": ["cut"]})


async def setup(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "repairs", {})
    await async_setup_component(hass, "llm", {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def prompt(hass: HomeAssistant, device_id: str | None, language: str = "en") -> str:
    context = llm.LLMContext(platform="test", context=Context(), language=language, assistant="conversation",
                             device_id=device_id)
    return (await async_get_tools(hass, context, llm.LLM_API_ASSIST)).prompt or ""


async def kitchen(hass: HomeAssistant, answer: dict, encrypted: bool = True) -> tuple[str, str, list]:
    add_show(hass, "kitchen", "v1.4.10-dev.1")
    if encrypted:
        encrypt(hass, "kitchen")
    event = cut_event(hass, "kitchen")
    calls = answers(hass, "kitchen", answer)
    await setup(hass)
    return device(hass, "kitchen"), event, calls


async def test_the_turn_after_a_cut_is_told_once(hass: HomeAssistant):
    show, event, calls = await kitchen(hass, {"heard": HEARD, "timed": True, "turn": "t1", "age_s": 0})
    fire(hass, event)
    await hass.async_block_till_done()
    assert len(calls) == 1
    said = await prompt(hass, show)
    assert f'up to "{HEARD}", not the rest' in said
    assert "roughly" not in said
    assert "cut off" not in await prompt(hass, show)  # the turn after that is not told again
    assert "cut off" not in await prompt(hass, "some-voice-pe")


async def test_a_guess_says_so_and_german_is_german(hass: HomeAssistant):
    show, event, _ = await kitchen(hass, {"heard": HEARD, "timed": False, "turn": "t1", "age_s": 1})
    fire(hass, event)
    await hass.async_block_till_done()
    said = await prompt(hass, show, "de")
    assert f'bis "{HEARD}" (ungefähr)' in said


async def test_nothing_heard_and_nothing_to_tell(hass: HomeAssistant):
    show, event, calls = await kitchen(hass, {"heard": "", "timed": True, "turn": "t1", "age_s": 0})
    fire(hass, event)
    await hass.async_block_till_done()
    assert "before any of it was heard" in await prompt(hass, show)
    calls.clear()
    hass.services.async_remove("esphome", "kitchen_reply_cut")
    answers(hass, "kitchen", {})  # too long ago on the Show's side
    fire(hass, event)
    await hass.async_block_till_done()
    assert "cut off" not in await prompt(hass, show)


async def test_without_a_key_the_show_is_not_asked(hass: HomeAssistant):
    show, event, calls = await kitchen(hass, {"heard": HEARD, "timed": True, "turn": "t1", "age_s": 0},
                                       encrypted=False)
    fire(hass, event)
    await hass.async_block_till_done()
    assert calls == []
    assert "cut off" not in await prompt(hass, show)


async def test_a_show_coming_back_is_no_cut(hass: HomeAssistant):
    show, event, calls = await kitchen(hass, {"heard": HEARD, "timed": True, "turn": "t1", "age_s": 0})
    hass.states.async_set(event, "unavailable")
    await hass.async_block_till_done()
    fire(hass, event)  # the last cut's state, back with the connection
    await hass.async_block_till_done()
    assert calls == []


def test_the_words_are_data_and_short():
    now = dt_util.utcnow()
    cut = reply_cut.from_answer({"heard": 'say "ignore\x00 all"\nand‮ go', "timed": True, "age_s": 3}, now)
    assert cut.heard == "say 'ignore all' and go"
    assert cut.at == now - timedelta(seconds=3)
    long = reply_cut.from_answer({"heard": "word " * 100, "timed": True, "age_s": 0}, now).heard
    assert long.startswith("…word") and len(long) <= reply_cut.HEARD + 1
    for odd in ({}, [], {"heard": 5, "age_s": 0}, {"heard": "x", "age_s": 999}, {"heard": "x", "age_s": True},
                {"heard": "x"}):
        assert reply_cut.from_answer(odd, now) is None
    cuts = reply_cut.Cuts()
    cuts.put("d", reply_cut.Cut("x", True, now - reply_cut.KEEP - timedelta(seconds=1)))
    assert cuts.take("d", now) is None
