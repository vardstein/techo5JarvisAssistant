"""The copies of a Show's controls on its genbu device (GENBU-114) inside a real Home Assistant: which
controls get one, that they follow and pass on, that the everyday originals are hidden once with their
Assist exposure kept, and that they come back when the option is off or Genbu is removed."""

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

DOMAIN = "genbu"
MAC = "AA:BB:CC:00:00:01"

# (sub-device, kind, Name, HA domain, category, state, attributes), the way echod and ESPHome show them.
CONTROLS = [
    (0, "light", "Screen", "light", None, "on",
     {"supported_color_modes": ["brightness"], "color_mode": "brightness", "brightness": 15,
      "supported_features": 40, "icon": "mdi:monitor"}),
    (0, "media_player", "Speaker", "media_player", None, "idle", {"volume_level": 0.8}),
    (0, "switch", "Night mode", "switch", None, "off", {"icon": "mdi:weather-night"}),
    (0, "text_sensor", "Next alarm", "sensor", None, "07:30", {}),
    (0, "button", "Snooze alarm", "button", None, "unknown", {}),
    (0, "number", "Microphone gain", "number", "config", "20.0",
     {"min": 0.0, "max": 59.0, "step": 1.0, "mode": "box", "unit_of_measurement": "dB"}),
    (0, "text_sensor", "IP address", "sensor", "diagnostic", "10.0.0.9", {}),
    (0, "camera", "Camera", "camera", None, "idle", {}),
    (0, "update", "Firmware", "update", None, "on",
     {"installed_version": "v1.0.3-dev.60", "latest_version": "v1.0.3-dev.67"}),
    (2, "switch", "Microphone mute", "switch", None, "on", {}),
    (3, "select", "White noise layer 1", "select", None, "Rain", {"options": ["None", "Rain", "Ocean"]}),
]


def add_show(hass: HomeAssistant, node: str = "hall") -> dict[str, str]:
    """A Show with its sub-devices; returns the original entity ids by Name."""
    entry = MockConfigEntry(domain="esphome", data={"device_name": node, "host": "10.0.0.9"},
                            unique_id=node, title=node)
    entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    main = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("esphome", node)},
                                       manufacturer="OpenJade", name=node.title(), model="Genbu Show")
    subs = {0: main.id}
    for idx, name in ((1, "ring"), (2, "microphone"), (3, "playback")):
        subs[idx] = devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("esphome", f"{node}-{name}")},
            name=f"{node.title()} {name}", via_device_id=main.id).id
    ids = {}
    for idx, kind, name, domain, category, state, attrs in CONTROLS:
        e = entities.async_get_or_create(domain, "esphome", f"{MAC}/{idx}/{kind}/{name}", config_entry=entry,
                                         device_id=subs[idx],
                                         entity_category=EntityCategory(category) if category else None,
                                         suggested_object_id=f"{node}_{name.lower().replace(' ', '_')}")
        hass.states.async_set(e.entity_id, state, attrs)
        ids[name] = e.entity_id
    e = entities.async_get_or_create("select", "esphome", f"{MAC}-pipeline", config_entry=entry,
                                     device_id=main.id, entity_category=EntityCategory.CONFIG,
                                     suggested_object_id=f"{node}_assistant")
    hass.states.async_set(e.entity_id, "preferred", {"options": ["preferred", "Genbu"]})
    ids["pipeline"] = e.entity_id
    return ids


async def setup(hass, options=None):
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(domain=DOMAIN, data={"main_show": "hall"}, options=options or {}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def copy(hass, entity_id):
    return hass.data[DATA_INSTANCES][entity_id.split(".")[0]].get_entity(entity_id)


async def test_copies_sit_on_the_genbu_device(hass: HomeAssistant):
    ids = add_show(hass)
    entry = await setup(hass)
    entities, devices = er.async_get(hass), dr.async_get(hass)
    genbu = next(d for d in dr.async_entries_for_config_entry(devices, entry.entry_id)
                 if d.model == "Genbu Show settings")
    on_genbu = {e.entity_id for e in er.async_entries_for_device(entities, genbu.id)}
    assert {"light.genbu_hall_screen", "number.genbu_hall_volume", "switch.genbu_hall_night_mode",
            "sensor.genbu_hall_next_alarm", "button.genbu_hall_snooze_alarm",
            "number.genbu_hall_microphone_gain", "sensor.genbu_hall_firmware",
            "switch.genbu_hall_microphone_mute", "select.genbu_hall_white_noise_layer_1",
            "select.genbu_hall_assistant"} <= on_genbu
    # Diagnostics, the camera and the update itself stay on the ESPHome device only.
    assert not any("ip_address" in e or "camera" in e for e in on_genbu)
    assert len([e for e in entities.entities.values() if e.domain == "update"]) == 1
    assert entities.async_get("number.genbu_hall_microphone_gain").entity_category == "config"
    assert entities.async_get("select.genbu_hall_assistant").translation_key == "assistant"
    screen = hass.states.get("light.genbu_hall_screen")
    assert (screen.state, screen.attributes["brightness"]) == ("on", 15)
    assert hass.states.get("number.genbu_hall_volume").state == "80"
    assert hass.states.get("select.genbu_hall_white_noise_layer_1").state == "Rain"
    assert hass.states.get("sensor.genbu_hall_next_alarm").state == "07:30"
    firmware = hass.states.get("sensor.genbu_hall_firmware")
    assert (firmware.state, firmware.attributes["latest_version"]) == ("v1.0.3-dev.60", "v1.0.3-dev.67")
    assert ids  # the originals are still there


async def test_copies_follow_the_original(hass: HomeAssistant):
    ids = add_show(hass)
    await setup(hass)
    hass.states.async_set(ids["Night mode"], "on")
    hass.states.async_set(ids["Speaker"], "idle", {"volume_level": 0.35})
    await hass.async_block_till_done()
    assert hass.states.get("switch.genbu_hall_night_mode").state == "on"
    assert hass.states.get("number.genbu_hall_volume").state == "35"
    hass.states.async_set(ids["Screen"], "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("light.genbu_hall_screen").state == "unavailable"


async def test_copies_pass_on_to_the_original(hass: HomeAssistant):
    ids = add_show(hass)
    await setup(hass)
    lights = async_mock_service(hass, "light", "turn_on")
    volume = async_mock_service(hass, "media_player", "volume_set")
    switches = async_mock_service(hass, "switch", "turn_off")
    selects = async_mock_service(hass, "select", "select_option")
    buttons = async_mock_service(hass, "button", "press")
    await copy(hass, "light.genbu_hall_screen").async_turn_on(brightness=100)
    await copy(hass, "number.genbu_hall_volume").async_set_native_value(50)
    await copy(hass, "switch.genbu_hall_microphone_mute").async_turn_off()
    await copy(hass, "select.genbu_hall_white_noise_layer_1").async_select_option("Ocean")
    await copy(hass, "button.genbu_hall_snooze_alarm").async_press()
    assert dict(lights[0].data) == {"entity_id": ids["Screen"], "brightness": 100}
    assert dict(volume[0].data) == {"entity_id": ids["Speaker"], "volume_level": 0.5}
    assert switches[0].data["entity_id"] == ids["Microphone mute"]
    assert dict(selects[0].data) == {"entity_id": ids["White noise layer 1"], "option": "Ocean"}
    assert buttons[0].data["entity_id"] == ids["Snooze alarm"]


async def test_everyday_originals_are_hidden_and_stay_with_assist(hass: HomeAssistant):
    ids = add_show(hass)
    await setup(hass)
    entities = er.async_get(hass)
    hidden = {name for name, eid in ids.items() if entities.async_get(eid).hidden_by}
    assert hidden == {"Screen", "Night mode", "Next alarm", "Snooze alarm", "Microphone mute",
                      "White noise layer 1"}
    # The screen was exposed before it was hidden and still is; its copy is not.
    assert async_should_expose(hass, "conversation", ids["Screen"]) is True
    assert async_should_expose(hass, "conversation", "light.genbu_hall_screen") is False
    assert async_should_expose(hass, "conversation", "switch.genbu_hall_night_mode") is False


async def test_shown_by_hand_stays_shown(hass: HomeAssistant):
    ids = add_show(hass)
    entry = await setup(hass)
    entities = er.async_get(hass)
    entities.async_update_entity(ids["Screen"], hidden_by=None)
    await entry.runtime_data.async_refresh()
    assert entities.async_get(ids["Screen"]).hidden_by is None
    assert entities.async_get(ids["Night mode"]).hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_option_off_and_removal_show_them_again(hass: HomeAssistant):
    ids = add_show(hass)
    entry = await setup(hass)
    entities = er.async_get(hass)
    hass.config_entries.async_update_entry(entry, options={"hide_originals": False})
    await hass.async_block_till_done()
    assert not any(entities.async_get(eid).hidden_by for eid in ids.values())
    hass.config_entries.async_update_entry(entry, options={"hide_originals": True})
    await hass.async_block_till_done()
    assert entities.async_get(ids["Screen"]).hidden_by is er.RegistryEntryHider.INTEGRATION
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not any(entities.async_get(eid).hidden_by for eid in ids.values())


async def test_a_renamed_original_is_followed(hass: HomeAssistant):
    ids = add_show(hass)
    entry = await setup(hass)
    er.async_get(hass).async_update_entity(ids["Night mode"], new_entity_id="switch.hall_quiet")
    hass.states.async_set("switch.hall_quiet", "on")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("switch.genbu_hall_night_mode").state == "on"


@pytest.mark.parametrize("hide", [True, False])
async def test_options_form_keeps_the_hide_choice(hass: HomeAssistant, hide):
    add_show(hass)
    entry = await setup(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"hide_originals": hide})
    assert result["data"]["hide_originals"] is hide
