"""The wake race inside a real Home Assistant (GENBU-224): the lease every look, the Shows' events, the
verdicts, and what is not let in."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import EventOrigin, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.genbu import race

from test_genbu_ha import add_show

DOMAIN = "genbu"
WAKE = "esphome.genbu_wake"


def encrypt(hass: HomeAssistant, node: str) -> None:
    entry: ConfigEntry = hass.config_entries.async_entry_for_domain_unique_id("esphome", node)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "noise_psk": "not-a-real-key"})


def actions(hass: HomeAssistant, node: str) -> dict[str, list]:
    """The Show's wake_race and wake_verdict, as its firmware brings them; returns the calls each gets."""
    calls = {"wake_race": [], "wake_verdict": []}
    for name, got in calls.items():
        hass.services.async_register("esphome", f"{node}_{name}", lambda call, got=got: got.append(dict(call.data)))
    return calls


def device(hass: HomeAssistant, node: str) -> str:
    return er.async_get(hass).async_get(f"update.{node}_firmware").device_id


async def setup(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def heard(hass: HomeAssistant, node: str, level: float, origin: EventOrigin = EventOrigin.local, **extra):
    hass.bus.async_fire(WAKE, {"device_id": device(hass, node), "wake": f"w-{node}", "phrase": "okay_genbu",
                               "slot": "1", "level_db": str(level), "floor_db": "-60.0", "score": "0.9",
                               "cutoff": "0.5", "clipped": "false", **extra}, origin)


async def house(hass: HomeAssistant, *nodes: str, encrypted=True) -> tuple[MockConfigEntry, dict]:
    calls = {}
    for node in nodes:
        add_show(hass, node, "v1.0.3-dev.160")
        if encrypted:
            encrypt(hass, node)
        calls[node] = actions(hass, node)
    entry = await setup(hass)
    return entry, calls


async def test_every_look_renews_the_lease(hass: HomeAssistant):
    entry, calls = await house(hass, "kitchen", "hall")
    assert set(entry.runtime_data.racers) == {device(hass, "kitchen"), device(hass, "hall")}
    leased = len(calls["kitchen"]["wake_race"])
    assert leased >= 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1, seconds=1))
    await hass.async_block_till_done()
    assert len(calls["kitchen"]["wake_race"]) > leased


async def test_without_a_key_nothing_races(hass: HomeAssistant):
    entry, calls = await house(hass, "kitchen", encrypted=False)
    assert entry.runtime_data.racers == {}
    assert calls["kitchen"]["wake_race"] == []
    heard(hass, "kitchen", -20.0)
    await hass.async_block_till_done()
    assert calls["kitchen"]["wake_verdict"] == []


async def test_the_loudest_answers_once_all_have_said(hass: HomeAssistant):
    _, calls = await house(hass, "kitchen", "hall")
    heard(hass, "hall", -35.0)
    heard(hass, "kitchen", -20.0)
    await hass.async_block_till_done()  # both said: no window to wait out
    assert calls["kitchen"]["wake_verdict"] == [{"wake": "w-kitchen", "won": True}]
    assert calls["hall"]["wake_verdict"] == [{"wake": "w-hall", "won": False}]


async def test_the_window_closes_without_the_rest(hass: HomeAssistant):
    _, calls = await house(hass, "kitchen", "hall")
    heard(hass, "kitchen", -30.0)
    await hass.async_block_till_done()
    assert calls["kitchen"]["wake_verdict"] == []
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=race.WINDOW * 2))
    await hass.async_block_till_done()
    assert calls["kitchen"]["wake_verdict"] == [{"wake": "w-kitchen", "won": True}]
    heard(hass, "hall", -10.0)  # too late, louder: it loses and the log says it would have won
    await hass.async_block_till_done()
    assert calls["hall"]["wake_verdict"] == [{"wake": "w-hall", "won": False}]


async def test_one_fired_through_the_api_is_not_a_show(hass: HomeAssistant):
    _, calls = await house(hass, "kitchen")
    heard(hass, "kitchen", -20.0, origin=EventOrigin.remote)
    await hass.async_block_till_done()
    assert calls["kitchen"]["wake_verdict"] == []


async def test_an_unknown_device_is_ignored(hass: HomeAssistant):
    _, calls = await house(hass, "kitchen")
    hass.bus.async_fire(WAKE, {"device_id": "someone-else", "wake": "w-x", "phrase": "okay_genbu"})
    await hass.async_block_till_done()
    assert calls["kitchen"]["wake_verdict"] == []


async def test_odd_levels_cannot_win(hass: HomeAssistant, caplog):
    _, calls = await house(hass, "kitchen", "hall")
    heard(hass, "hall", 40.0)  # more than a microphone gives: clamped to 0 dBFS
    heard(hass, "kitchen", -20.0, clipped="true")  # the same 0 dBFS, and honest
    await hass.async_block_till_done()
    assert "said levels no microphone gives" in caplog.text
    assert len(calls["kitchen"]["wake_verdict"]) == len(calls["hall"]["wake_verdict"]) == 1
