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
    entry = entry or MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
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
    assert panel.require_admin  # GENBU-262: the panel is for admins; the dashboard cards stay for everyone
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
        text = await resp.text()
        assert card in text
        # Loaded before Home Assistant's app swaps in its own registry, so each card defines itself
        # again once <home-assistant> is there; without it the calendar said "Custom element doesn't exist".
        assert 'customElements.whenDefined("home-assistant").then(defineCard)' in text
        assert f'window.customElements.define("{card}"' in text
    # The calendar card shows a list of calendars, which the installer sets (docs/calendars.md).
    assert "entities" in await (await client.get("/genbu_panel/jarvis-calendar-card.js")).text()


async def test_pages_with_old_cards_can_tell(hass: HomeAssistant, hass_client, hass_ws_client):
    """GENBU-147: Home Assistant's service worker hands out the page it kept, cards and all, so
    genbu-fresh.js comes with a hash of all the cards and compares it with genbu/frontend's."""
    entry = await setup(hass)
    fresh = [u for u in hass.data[DATA_EXTRA_MODULE_URL].urls if "/genbu-fresh.js" in u]
    assert len(fresh) == 1
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "genbu/frontend"})
    msg = await ws.receive_json()
    assert msg["success"] and msg["result"]["version"] == fresh[0].split("?v=")[1]
    text = await (await (await hass_client()).get("/genbu_panel/genbu-fresh.js")).text()
    assert '{ type: "genbu/frontend" }' in text and "location.reload()" in text
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not [u for u in hass.data[DATA_EXTRA_MODULE_URL].urls if "/genbu-fresh.js" in u]
    await ws.send_json({"id": 2, "type": "genbu/frontend"})
    assert (await ws.receive_json())["result"] == {"version": None}


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
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
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


async def test_house_device_visit_opens_the_page(hass: HomeAssistant):
    """GENBU-121: the integration's device page has a Visit link to the Control Center."""
    entry = await setup(hass)
    device = next(iter(dr.async_get(hass).async_get_devices(identifiers={(DOMAIN, entry.entry_id)})))
    assert device.configuration_url == "homeassistant://genbu"
    assert device.model == "Genbu Shows"  # the page tells the house card from the Shows by this


async def test_the_timer_card_learns_which_show_it_is_on(hass: HomeAssistant, hass_ws_client, hass_admin_user):
    """GENBU-164: one dashboard for every Show; signed in as a Show's own user, genbu/shows names only that
    Show, so its timer card shows only its timers. Anyone else gets every Show, and own is false."""
    for node in ("hall", "attic"):
        entry = MockConfigEntry(domain="esphome", data={"device_name": node, "host": "10.0.0.9"},
                                unique_id=node, title=node)
        entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("esphome", node)}, manufacturer="OpenJade",
            name=node.title(), model="Genbu Show")
        for domain, kind in (("update", "firmware"), ("assist_satellite", "assist_satellite")):
            er.async_get(hass).async_get_or_create(domain, "esphome", f"aa:{node}-{domain}-{kind}",
                                                   config_entry=entry, device_id=device.id,
                                                   suggested_object_id=f"{node}_{kind}")
    entry = await setup(hass)
    hub = entry.runtime_data
    hall = next(s for s in hub.shows if s.node == "hall")
    assert hall.satellite.startswith("assist_satellite.")
    show_user = await hass.auth.async_create_user("Hall Show", local_only=True)
    hub.keycare.recorded[hall.device] = {"user": show_user.id}

    async def ask(user):
        refresh = await hass.auth.async_create_refresh_token(
            user, client_name="Genbu test", token_type="long_lived_access_token")
        client = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
        await client.send_json({"id": 1, "type": "genbu/shows"})
        answer = await client.receive_json()
        assert answer["success"], answer
        return answer["result"]

    mine = await ask(show_user)
    assert mine == {"own": True, "shows": [{"device": hall.device, "name": hall.name, "satellite": hall.satellite}]}

    everyone = await ask(hass_admin_user)
    assert everyone["own"] is False
    assert sorted(s["device"] for s in everyone["shows"]) == sorted(s.device for s in hub.shows)

    # Not an admin either, and not a Show's: still every Show, nothing more than their entities say.
    other = await hass.auth.async_create_user("Someone")
    assert (await ask(other))["own"] is False
