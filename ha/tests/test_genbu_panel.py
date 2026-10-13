"""The Genbu Control Center in the sidebar (GENBU-114, named in GENBU-120) inside a real Home Assistant:
it is there while Genbu runs, its script is served, and it goes when Genbu is unloaded. Also what 1.3.0 tidies up after 1.2.0, whose
copies of the controls it replaces: their registry entries go and the hidden originals come back."""

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, DATA_PANELS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "genbu"


def add_show(hass: HomeAssistant, node: str = "hall") -> str:
    """A Show with a screen light; returns the light's entity id."""
    entry = MockConfigEntry(domain="esphome", data={"device_name": node, "host": "10.0.0.9"},
                            unique_id=node, title=node)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("esphome", node)}, manufacturer="OpenJade",
        name=node.title(), model="Genbu Show")
    light = er.async_get(hass).async_get_or_create(
        "light", "esphome", "AA:BB:CC:00:00:01/0/light/Screen", config_entry=entry, device_id=device.id,
        suggested_object_id=f"{node}_screen")
    hass.states.async_set(light.entity_id, "on")
    return light.entity_id


async def setup(hass: HomeAssistant, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    await async_setup_component(hass, "repairs", {})
    entry = entry or MockConfigEntry(domain=DOMAIN, data={"main_show": "hall"}, unique_id=DOMAIN)
    if not hass.config_entries.async_get_entry(entry.entry_id):
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_page_is_in_the_sidebar(hass: HomeAssistant, hass_client):
    add_show(hass)
    await setup(hass)
    panel = hass.data[DATA_PANELS]["genbu"]
    assert panel.sidebar_title == "Genbu Control Center"
    assert panel.config["title"] == "Genbu Control Center"
    assert panel.sidebar_icon == "mdi:tablet-dashboard"
    assert not panel.require_admin
    custom = panel.config["_panel_custom"]
    assert custom["name"] == "genbu-panel"
    assert custom["module_url"].startswith("/genbu_panel/genbu-panel.js?v=")
    assert panel.config["manufacturers"] == ["OpenJade"]

    client = await hass_client()
    resp = await client.get("/genbu_panel/genbu-panel.js")
    assert resp.status == 200
    assert 'customElements.define("genbu-panel"' in await resp.text()


CARDS = ("jarvis-calendar-card", "jarvis-live-timer-card", "jarvis-recipe-card", "jarvis-room-environment-card",
         "jarvis-weather-card")


def card_urls(hass: HomeAssistant) -> list[str]:
    return sorted(u for u in hass.data[DATA_EXTRA_MODULE_URL].urls if "/jarvis-" in u)


async def test_dashboard_cards_load_on_every_page(hass: HomeAssistant, hass_client):
    await setup(hass)
    urls = card_urls(hass)
    assert [u.split("?")[0] for u in urls] == [f"/genbu_panel/{c}.js" for c in CARDS]
    assert all(len(u.split("?v=")[1]) == 12 for u in urls)  # the file's hash, new whenever the file is
    client = await hass_client()
    for card in CARDS:
        resp = await client.get(f"/genbu_panel/{card}.js")
        assert resp.status == 200
        assert card in await resp.text()
    # The calendar card shows a list of calendars, which the installer sets (docs/calendars.md).
    assert "entities" in await (await client.get("/genbu_panel/jarvis-calendar-card.js")).text()


async def test_page_goes_with_genbu_and_comes_back(hass: HomeAssistant):
    add_show(hass)
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "genbu" not in hass.data[DATA_PANELS]
    assert not card_urls(hass)
    # Set up again in the same run: the files are already served, the page comes back.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "genbu" in hass.data[DATA_PANELS]
    assert len(card_urls(hass)) == len(CARDS)


async def test_after_1_2_0_copies_go_and_originals_come_back(hass: HomeAssistant):
    light = add_show(hass)
    entities = er.async_get(hass)
    entities.async_update_entity(light, hidden_by=er.RegistryEntryHider.INTEGRATION)
    reg_id = entities.async_get(light).id
    entry = MockConfigEntry(domain=DOMAIN, data={"main_show": "hall"}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    copy = entities.async_get_or_create("light", DOMAIN, f"{entry.entry_id}_copy_somedevice_screen",
                                        config_entry=entry)
    kept = entities.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_house_version",
                                        config_entry=entry)
    await Store(hass, 1, "genbu.house").async_save({"hidden": {reg_id: True}})

    await setup(hass, entry)
    assert entities.async_get(copy.entity_id) is None
    assert entities.async_get(kept.entity_id) is not None
    assert entities.async_get(light).hidden_by is None
    assert "hidden" not in entry.runtime_data.house.data
