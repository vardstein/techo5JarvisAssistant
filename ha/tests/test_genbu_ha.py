"""The genbu integration inside a real Home Assistant: finding the Shows, the house version sensor, and the
house's update card with its release channel and Install at night (GENBU-119)."""

from datetime import timedelta

import pytest
from conftest import RELEASES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.genbu.house import manifest_url, tag_url

DOMAIN = "genbu"
UPDATE = "update.genbu_show_firmware"
CHANNEL = "select.genbu_release_channel"
NIGHT = "switch.genbu_install_at_night"


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
    entities.async_get_or_create("sensor", "esphome", f"aa:{node}-sensor-update_status", config_entry=entry,
                                 device_id=device.id, suggested_object_id=f"{node}_update_status")
    set_show(hass, up.entity_id, done.entity_id, version)
    return up.entity_id


def status_of(up: str) -> str:
    """The Show's update_status sensor, by its firmware update entity."""
    return up.replace("update.", "sensor.").replace("_firmware", "_update_status")


def set_show(hass, up, done, version, online=True):
    hass.states.async_set(up, "off" if online else "unavailable", {"installed_version": version})
    hass.states.async_set(done, version if online else "unavailable")



async def setup(hass):
    await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def release(version, channel="stable", **more):
    """Puts a release on a channel, the way GitHub would answer."""
    RELEASES[manifest_url(channel)] = {"version": version, "title": f"Genbu {version}",
                                       "notes": f"What {version} brings", **more}


def tag(version):
    """A release that is there to go back to."""
    RELEASES[tag_url(version)] = {"version": version}


async def read(hass, entry):
    await entry.runtime_data.async_read_release()
    await hass.async_block_till_done()


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


async def test_a_new_house_starts_at_its_newest_show(hass: HomeAssistant):
    """GENBU-119: no main update Show to pick. The house starts where its newest Show is, so nothing moves."""
    add_show(hass, "hall", "v1.0.3-stable.52")
    add_show(hass, "kitchen", "v1.0.2-stable.48")
    add_show(hass, "speaker", "v9.9.9", manufacturer="Someone")
    await setup(hass)
    state = hass.states.get("sensor.genbu_house_version")
    assert state.state == "v1.0.3-stable.52"
    assert state.attributes["main_shows"] == []
    assert state.attributes["main_missing_since"] == ""
    assert (state.attributes["update_waiting"], state.attributes["update_asks"]) == ("", "")
    assert set(state.attributes["shows"]) == {"hall", "kitchen"}
    assert hass.states.get("select.genbu_main_update_show") is None
    card = hass.states.get(UPDATE)
    assert (card.state, card.attributes["installed_version"]) == ("off", "v1.0.3-stable.52")
    assert hass.states.get(CHANNEL).state == "stable"
    assert hass.states.get(NIGHT).state == "off"


async def test_no_shows_no_version(hass: HomeAssistant):
    await setup(hass)
    assert hass.states.get("sensor.genbu_house_version").state == "unknown"
    assert hass.states.get(UPDATE).state == "unknown"


async def test_the_card_offers_the_channel_release(hass: HomeAssistant, hass_ws_client):
    add_show(hass, "hall", "v1.0.3")
    add_show(hass, "attic", "v1.0.2")  # behind the house: it moves first, and does not ask
    entry = await setup(hass)
    release("v1.0.4", release_url="https://example.org/v1.0.4")
    await read(hass, entry)
    card = hass.states.get(UPDATE)
    assert (card.state, card.attributes["latest_version"]) == ("on", "v1.0.4")
    assert card.attributes["release_summary"] == "Genbu v1.0.4"
    assert card.attributes["release_url"] == "https://example.org/v1.0.4"
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.attributes["update_waiting"], state.attributes["update_asks"]) == ("v1.0.4", "hall")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "update/release_notes", "entity_id": UPDATE})
    # What the release brings, then every Show (GENBU-249).
    notes = (await client.receive_json())["result"]
    assert notes.startswith("What v1.0.4 brings\n\n---\n\n**1 of 2 Shows on v1.0.3**")
    assert "- **Attic** · v1.0.2 · waiting" in notes and "- **Hall** · v1.0.3 · up to date" in notes

    # An older one on the channel is no update.
    release("v1.0.2")
    await read(hass, entry)
    assert hass.states.get(UPDATE).state == "off"


async def test_a_new_release_shows_within_the_hour_or_on_check(hass: HomeAssistant, freezer):
    """GENBU-258: the channel is read every hour, and Check for updates reads it at once."""
    add_show(hass, "hall", "v1.0.3")
    await setup(hass)
    release("v1.0.4")
    freezer.tick(timedelta(minutes=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(UPDATE).attributes["latest_version"] == "v1.0.4"

    release("v1.0.5")
    await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call("homeassistant", "update_entity", {"entity_id": UPDATE}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(UPDATE).attributes["latest_version"] == "v1.0.5"


async def test_a_shows_check_for_updates_reads_the_channel_too(hass: HomeAssistant, freezer):
    """GENBU-157: a following Show's check only asks the house, so its press has the house read the channel."""
    up = add_show(hass, "hall", "v1.0.3")
    entities = er.async_get(hass)
    show = entities.async_get(up)
    button = entities.async_get_or_create("button", "esphome", "aa:hall-button-check_for_updates",
                                          config_entry=hass.config_entries.async_get_entry(show.config_entry_id),
                                          device_id=show.device_id, suggested_object_id="hall_check_for_updates")
    hass.states.async_set(button.entity_id, "unavailable")
    await setup(hass)

    def press(state=None):
        hass.states.async_set(button.entity_id, state or dt_util.utcnow().isoformat())

    def latest():
        return hass.states.get(UPDATE).attributes["latest_version"]

    release("v1.0.4")
    press("2026-10-01T08:00:00+00:00")  # the Show connecting again with its last press: not a press
    await hass.async_block_till_done()
    assert latest() == "v1.0.3"
    press()
    await hass.async_block_till_done()
    assert latest() == "v1.0.4"
    release("v1.0.5")
    freezer.tick(timedelta(seconds=20))
    press()  # within the minute: nothing more
    await hass.async_block_till_done()
    assert latest() == "v1.0.4"
    freezer.tick(timedelta(seconds=41))
    press()
    await hass.async_block_till_done()
    assert latest() == "v1.0.5"


async def test_install_moves_the_house_and_the_shows_follow(hass: HomeAssistant):
    up = add_show(hass, "hall", "v1.0.3")
    kitchen = add_show(hass, "kitchen", "v1.0.3")
    entry = await setup(hass)
    release("v1.0.4")
    await read(hass, entry)
    await hass.services.async_call("update", "install", {"entity_id": UPDATE}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.state, state.attributes["update_waiting"]) == ("v1.0.4", "")
    card = hass.states.get(UPDATE)
    # On, from the oldest Show still on its way, with how far they are (GENBU-249).
    assert (card.state, card.attributes["installed_version"], card.attributes["latest_version"]) == (
        "on", "v1.0.3", "v1.0.4")
    assert card.attributes["in_progress"] is True and card.attributes["update_percentage"] == 0
    set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"), "v1.0.4")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(UPDATE).attributes["update_percentage"] == 50
    assert entry.runtime_data.house.before == "v1.0.3"
    set_show(hass, kitchen, kitchen.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
             "v1.0.4")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    card = hass.states.get(UPDATE)
    assert (card.state, card.attributes["installed_version"], card.attributes["in_progress"]) == (
        "off", "v1.0.4", False)


async def test_the_list_says_where_each_show_is(hass: HomeAssistant, hass_ws_client):
    """GENBU-249: downloading with how far, installing, waiting and why, offline."""
    hall = add_show(hass, "hall", "v1.0.3")
    kitchen = add_show(hass, "kitchen", "v1.0.3")
    desk = add_show(hass, "desk", "v1.0.3")
    attic = add_show(hass, "attic", "v1.0.3")
    entry = await setup(hass)
    release("v1.0.4")
    await read(hass, entry)
    await hass.services.async_call("update", "install", {"entity_id": UPDATE}, blocking=True)
    await hass.async_block_till_done()
    hass.states.async_set(hall, "on", {"installed_version": "v1.0.3", "latest_version": "v1.0.4",
                                       "in_progress": True, "update_percentage": 41.6})
    hass.states.async_set(kitchen, "on", {"installed_version": "v1.0.3", "latest_version": "v1.0.4",
                                          "in_progress": True, "update_percentage": 100})
    hass.states.async_set(status_of(desk), "Moving to v1.0.4 once the music stops")
    set_show(hass, attic, attic.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
             "v1.0.3", online=False)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "update/release_notes", "entity_id": UPDATE})
    assert (await client.receive_json())["result"] == "\n".join([
        "**0 of 3 Shows on v1.0.4**", "",
        "- **Attic** · offline",
        "- **Desk** · v1.0.3 · waiting: Moving to v1.0.4 once the music stops",
        "- **Hall** · v1.0.3 · downloading 42%",
        "- **Kitchen** · v1.0.3 · installing"])


async def test_install_with_a_backup(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import async_mock_service

    add_show(hass, "hall", "v1.0.3")
    entry = await setup(hass)
    backups = async_mock_service(hass, "backup", "create_automatic")
    release("v1.0.4")
    await read(hass, entry)
    await hass.services.async_call("update", "install", {"entity_id": UPDATE, "backup": True}, blocking=True)
    assert len(backups) == 1
    assert hass.states.get("sensor.genbu_house_version").state == "v1.0.4"


async def test_going_back_skips_what_was_left(hass: HomeAssistant):
    """A version picked by hand moves the house back; the release left behind stays skipped, so neither the
    night nor the Shows bring it back. A newer release is offered again."""
    up = add_show(hass, "hall", "v1.0.3")
    entry = await setup(hass)
    release("v1.0.4")
    await read(hass, entry)
    await hass.services.async_call("update", "install", {"entity_id": UPDATE}, blocking=True)
    await hass.async_block_till_done()
    # Like any update card, it takes no other install while the Shows are on their way.
    tag("v1.0.3")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("update", "install", {"entity_id": UPDATE, "version": "v1.0.3"},
                                       blocking=True)
    set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"), "v1.0.4")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(UPDATE).attributes["in_progress"] is False

    # Not a release: nothing moves.
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("update", "install", {"entity_id": UPDATE, "version": "v1.0.1"},
                                       blocking=True)
    await hass.services.async_call("update", "install", {"entity_id": UPDATE, "version": "v1.0.3"},
                                   blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.genbu_house_version")
    assert (state.state, state.attributes["update_waiting"]) == ("v1.0.3", "")
    card = hass.states.get(UPDATE)
    assert (card.state, card.attributes["skipped_version"]) == ("off", "v1.0.4")

    release("v1.0.5")
    await read(hass, entry)
    assert hass.states.get(UPDATE).state == "on"
    assert hass.states.get("sensor.genbu_house_version").attributes["update_waiting"] == "v1.0.5"


async def test_skip_on_the_card(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3")
    entry = await setup(hass)
    release("v1.0.4")
    await read(hass, entry)
    await hass.services.async_call("update", "skip", {"entity_id": UPDATE}, blocking=True)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").attributes["update_waiting"] == ""
    # Nothing waiting: the voice's install starts no house update.
    assert (await entry.runtime_data.async_install_updates())["updating"] == []
    assert entry.runtime_data.house.version == "v1.0.3"


async def test_the_channel_select_reads_the_other_channel(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-stable.52")
    entry = await setup(hass)
    release("v1.0.4-staging.60", channel="staging")
    await read(hass, entry)
    assert hass.states.get(UPDATE).state == "off"
    await hass.services.async_call("select", "select_option", {"entity_id": CHANNEL, "option": "staging"},
                                   blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(CHANNEL).state == "staging"
    assert hass.states.get(UPDATE).attributes["latest_version"] == "v1.0.4-staging.60"


async def test_a_dev_house_reads_dev(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3-dev.70")
    await setup(hass)
    assert hass.states.get(CHANNEL).state == "dev"


async def test_install_at_night(hass: HomeAssistant, freezer):
    add_show(hass, "hall", "v1.0.3")
    entry = await setup(hass)
    release("v1.0.4")

    async def three_oclock():
        now = dt_util.now()
        at = now.replace(hour=3, minute=0, second=0, microsecond=0)
        freezer.move_to(at if at > now else at + timedelta(days=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    await three_oclock()
    assert entry.runtime_data.house.version == "v1.0.3"  # off unless somebody switches it on

    await hass.services.async_call("switch", "turn_on", {"entity_id": NIGHT}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(NIGHT).state == "on"
    await three_oclock()
    assert entry.runtime_data.house.version == "v1.0.4"


async def test_install_updates_moves_the_house(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import async_mock_service

    add_show(hass, "hall", "v1.0.3")
    add_show(hass, "kitchen", "v1.0.3")
    entry = await setup(hass)
    installs = async_mock_service(hass, "update", "install")
    release("v1.0.4")
    await read(hass, entry)
    answer = await hass.services.async_call("genbu", "install_updates", {"except_node": "hall"},
                                            blocking=True, return_response=True)
    assert sorted(answer["updating"]) == ["Hall", "Kitchen"] and answer["current"] == []
    assert entry.runtime_data.house.version == "v1.0.4"
    assert installs == []  # the Shows follow the house by themselves


async def test_what_1_3_left_is_cleared(hass: HomeAssistant, hass_storage):
    """The main update Shows of before 1.4.0: their keys leave the store, their issues leave Repairs."""
    from custom_components.genbu.const import STORE_KEY, STORE_VERSION

    hass_storage[STORE_KEY] = {"version": STORE_VERSION, "key": STORE_KEY, "data": {
        "mains": ["hall"], "missing_since": {}, "wait_until": {}, "told": [], "seeded": True,
        "committed": {"hall": "v1.0.3"}}}
    add_show(hass, "hall", "v1.0.3")
    await async_setup_component(hass, "repairs", {})
    ir.async_create_issue(hass, DOMAIN, "no_main", is_fixable=True, severity=ir.IssueSeverity.WARNING,
                          translation_key="no_main")
    entry = MockConfigEntry(domain=DOMAIN, data={"main_show": "hall"}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("select", DOMAIN, f"{entry.entry_id}_main_update_show",
                                           config_entry=entry, suggested_object_id="genbu_main_update_show")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]
    assert er.async_get(hass).async_get("select.genbu_main_update_show") is None
    data = entry.runtime_data.house.data
    assert not {"mains", "missing_since", "wait_until", "told", "seeded"} & set(data)
    assert data["committed"] == {"hall": "v1.0.3"} and data["house"] == "v1.0.3"


async def test_the_setup_form_asks_nothing(hass: HomeAssistant):
    add_show(hass, "hall", "v1.0.3")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert (result["type"], result["data"]) == ("create_entry", {})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.genbu_house_version").state == "v1.0.3"


async def test_options_the_recipe_search_and_the_agent(hass: HomeAssistant):
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert (result["type"], result["step_id"]) == ("form", "init")
    assert [str(k) for k in result["data_schema"].schema] == ["searxng_url", "conversation_agent"]



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
