"""The genbu integration inside a real Home Assistant: finding the Shows, the house version sensor,
the select, the Repairs issue after a day and its fix flow, the pick after 7 days."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

DOMAIN = "genbu"


def add_show(hass: HomeAssistant, node: str, version: str, manufacturer: str = "OpenJade") -> str:
    """An ESPHome device the way echod shows up; returns its firmware update entity id."""
    entry = MockConfigEntry(domain="esphome", data={"device_name": node, "host": "10.0.0.9"},
                            unique_id=node, title=node)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("esphome", node)}, manufacturer=manufacturer,
        name=node.title(), model="Genbu Show")
    entities = er.async_get(hass)
    up = entities.async_get_or_create("update", "esphome", f"aa:{node}-update-firmware",
                                      config_entry=entry, device_id=device.id, suggested_object_id=f"{node}_firmware")
    done = entities.async_get_or_create("sensor", "esphome", f"aa:{node}-sensor-committed_version",
                                        config_entry=entry, device_id=device.id,
                                        suggested_object_id=f"{node}_committed_version")
    set_show(hass, up.entity_id, done.entity_id, version)
    return up.entity_id


def set_show(hass, up, done, version, online=True):
    hass.states.async_set(up, "off" if online else "unavailable", {"installed_version": version})
    hass.states.async_set(done, version if online else "unavailable")


async def setup(hass, main=None):
    await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(domain=DOMAIN, data={"main_show": main} if main else {}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(("before", "after"), [("Genbu", "Genbu Assistant"), ("Haus", "Haus")])
async def test_the_entry_takes_the_new_name(hass: HomeAssistant, before: str, after: str):
    """GENBU-120: an entry set up before 1.4.0 is called Genbu Assistant now; one somebody renamed keeps
    their name."""
    await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN, title=before)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == after


async def test_house_version_from_the_main(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.2-stable.48")
    add_show(hass, "speaker", "v9.9.9", manufacturer="Someone")
    await setup(hass, main="hall")
    state = hass.states.get("sensor.genbu_house_version")
    assert state.state == "v1.0.3-stable.52"
    assert state.attributes["main_shows"] == ["hall"]
    assert state.attributes["main_missing_since"] == ""
    assert set(state.attributes["shows"]) == {"hall", "kitchen"}
    select = hass.states.get("select.genbu_main_update_show")
    assert select.state == "hall"
    assert select.attributes["options"] == ["hall", "kitchen"]


async def test_select_moves_the_main(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.4-stable.53")
    await setup(hass, main="hall")
    await hass.services.async_call("select", "select_option", {
        "entity_id": "select.genbu_main_update_show", "option": "kitchen"}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.state, state.attributes["main_shows"]) == ("v1.0.4-stable.53", ["kitchen"])


async def test_no_main_asks_in_repairs(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-stable.52")
    await setup(hass)
    assert hass.states.get("sensor.genbu_house_version").state == "unknown"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "no_main")


async def test_missing_main_timeline(hass: HomeAssistant, freezer):
    up = add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.3-stable.52")
    await setup(hass, main="hall")
    set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
             "", online=False)
    await hass.async_block_till_done()
    issues = ir.async_get(hass)
    assert hass.states.get("sensor.genbu_house_version").attributes["main_missing_since"]
    assert issues.async_get_issue(DOMAIN, "main_missing_hall") is None

    freezer.tick(timedelta(hours=24, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, "main_missing_hall")
    assert issue and issue.is_fixable
    assert hass.states.get("sensor.genbu_house_version").state == "v1.0.3-stable.52"

    freezer.tick(timedelta(days=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.state, state.attributes["main_shows"]) == ("v1.0.3-stable.52", ["kitchen"])
    assert issues.async_get_issue(DOMAIN, "main_missing_hall") is None
    assert issues.async_get_issue(DOMAIN, "main_picked_kitchen")


async def test_fix_flow_picks_the_new_main(hass: HomeAssistant, freezer):
    from homeassistant.components.repairs import repairs_flow_manager

    up = add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.3-stable.52")
    await setup(hass, main="hall")
    set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
             "", online=False)
    freezer.tick(timedelta(hours=25))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # What the Repairs page drives (its HTTP view sits on the same manager).
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": "main_missing_hall"})
    assert result["type"] == "menu" and result["menu_options"] == ["pick", "wait", "gone"]
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "pick"})
    assert result["type"] == "form"
    result = await manager.async_configure(result["flow_id"], {"show": "kitchen"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").attributes["main_shows"] == ["kitchen"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, "main_missing_hall") is None


async def test_setup_form_lists_the_shows(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-stable.52")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"main_show": "hall"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get("select.genbu_main_update_show").state == "hall"


async def test_second_main_warns_and_no_keeps_one(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.3-stable.52")
    entry = await setup(hass, main="hall")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"extra_mains": ["kitchen"]})
    assert (result["type"], result["step_id"]) == ("menu", "second")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "second_no"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").attributes["main_shows"] == ["hall"]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"extra_mains": ["kitchen"]})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "second_yes"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").attributes["main_shows"] == ["hall", "kitchen"]


async def _gone_a_day(hass, freezer, notify=None):
    up = add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.3-stable.52")
    entry = await setup(hass, main="hall")
    if notify:
        hass.config_entries.async_update_entry(entry, options={"notify_service": notify})
        await hass.async_block_till_done()
    set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
             "", online=False)
    freezer.tick(timedelta(hours=25))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_fix_flow_wait_and_gone(hass: HomeAssistant, freezer):
    from homeassistant.components.repairs import repairs_flow_manager

    await _gone_a_day(hass, freezer)
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": "main_missing_hall"})
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "wait"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    # Waiting moves the pick a week on: day 8 picks nothing, and the question is back the day before.
    freezer.tick(timedelta(days=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").attributes["main_shows"] == ["hall"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, "main_missing_hall")

    result = await manager.async_init(DOMAIN, data={"issue_id": "main_missing_hall"})
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "gone"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.state, state.attributes["main_shows"]) == ("v1.0.3-stable.52", [])
    assert ir.async_get(hass).async_get_issue(DOMAIN, "no_main")


async def test_no_main_fix_flow(hass: HomeAssistant):
    from homeassistant.components.repairs import repairs_flow_manager

    add_show(hass, "hall", "v1.0.3-stable.52")
    await setup(hass)
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": "no_main"})
    assert result["type"] == "form" and result["step_id"] == "pick"
    result = await manager.async_configure(result["flow_id"], {"show": "hall"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").state == "v1.0.3-stable.52"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "no_main") is None


async def test_push_once_per_spell(hass: HomeAssistant, freezer):
    from pytest_homeassistant_custom_component.common import async_mock_service

    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    await _gone_a_day(hass, freezer, notify="notify.mobile_app_phone")
    assert len(calls) == 1
    assert "Hall" in calls[0].data["message"]
    assert calls[0].data["data"]["url"] == "/config/repairs"
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_a_renamed_main_stays_main(hass: HomeAssistant, freezer):
    add_show(hass, "hall", "v1.0.3-stable.52")
    await setup(hass, main="hall")
    esphome = hass.config_entries.async_entries("esphome")[0]
    hass.config_entries.async_update_entry(esphome, data={**esphome.data, "device_name": "flur"})
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.state, state.attributes["main_shows"]) == ("v1.0.3-stable.52", ["flur"])
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]


async def test_install_updates_starts_every_show_with_one(hass: HomeAssistant):
    """GENBU-125: yes on one Show updates the house. The Show that asked is left out, it installs already;
    one with nothing waiting and one offline are not touched."""
    from pytest_homeassistant_custom_component.common import async_mock_service

    kitchen = add_show(hass, "kitchen", "1.0.0")
    hall = add_show(hass, "hall", "1.0.0")
    add_show(hass, "bed", "1.1.0")
    attic = add_show(hass, "attic", "1.0.0")
    await setup(hass)
    installs = async_mock_service(hass, "update", "install")
    for up in (kitchen, hall):
        hass.states.async_set(up, "on", {"installed_version": "1.0.0", "latest_version": "1.1.0"})
    hass.states.async_set(attic, "unavailable", {"installed_version": "1.0.0"})

    answer = await hass.services.async_call("genbu", "install_updates", {"except_node": "Kitchen"},
                                            blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in installs] == [hall]
    assert answer == {"updating": ["Hall"], "current": ["Bed"]}

    # Nobody left out, as the voice kit calls it.
    answer = await hass.services.async_call("genbu", "install_updates", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert sorted(c.data["entity_id"] for c in installs[1:]) == sorted([kitchen, hall])
    assert sorted(answer["updating"]) == ["Hall", "Kitchen"]
