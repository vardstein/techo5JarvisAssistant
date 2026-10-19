"""Each Show's firmware update looks like a Genbu update in Settings (GENBU-232): the Genbu icon instead of
ESPHome's, while ESPHome still installs it."""

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.core_config import DATA_CUSTOMIZE
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_values import EntityValues
from pytest_homeassistant_custom_component.common import MockEntityPlatform

from custom_components.genbu.picture import ICON

from test_genbu_ha import add_show, setup

ESPHOME_ICON = "/api/brands/integration/esphome/icon.png"


class Firmware(UpdateEntity):
    """A Show's firmware update the way ESPHome's own is: ESPHome's icon, from Home Assistant."""

    _attr_installed_version = _attr_latest_version = "v1.0.3-dev.160"

    def __init__(self, node: str) -> None:
        self._attr_unique_id = f"aa:{node}-update-firmware"
        self._attr_device_info = DeviceInfo(identifiers={("esphome", node)})


async def esphome_firmware(hass: HomeAssistant, node: str) -> str:
    """A Show whose firmware update is a real entity, written by an ESPHome platform."""
    entity_id = add_show(hass, node, "v1.0.3-dev.160")
    hass.states.async_remove(entity_id)  # the entity writes it now
    platform = MockEntityPlatform(hass, domain="update", platform_name="esphome")
    platform.config_entry = hass.config_entries.async_entry_for_domain_unique_id("esphome", node)
    platform.async_prepare()
    await platform.async_add_entities([Firmware(node)])
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["entity_picture"] == ESPHOME_ICON
    return entity_id


async def test_a_show_shows_the_genbu_icon_until_genbu_goes(hass: HomeAssistant):
    kitchen = await esphome_firmware(hass, "kitchen")
    entry = await setup(hass)
    assert hass.states.get(kitchen).attributes["entity_picture"] == ICON
    assert hass.states.get(kitchen).attributes["title"] is None  # nothing else of ESPHome's is touched
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(kitchen).attributes["entity_picture"] == ESPHOME_ICON
    assert DATA_CUSTOMIZE not in hass.data  # there was none before either


async def test_only_the_shows(hass: HomeAssistant):
    add_show(hass, "kitchen", "v1.0.3-dev.160")
    add_show(hass, "stranger", "2025.1.0", manufacturer="Espressif")
    await setup(hass)
    customize = hass.data[DATA_CUSTOMIZE]
    assert customize.get("update.kitchen_firmware") == {"entity_picture": ICON}
    assert customize.get("update.stranger_firmware") == {}
    assert customize.get("light.kitchen") == {}


async def test_what_someone_customized_stays_and_wins(hass: HomeAssistant):
    hass.data[DATA_CUSTOMIZE] = EntityValues({"update.kitchen_firmware": {"entity_picture": "/local/mine.png",
                                                                          "friendly_name": "Kitchen"}})
    add_show(hass, "kitchen", "v1.0.3-dev.160")
    entry = await setup(hass)
    assert hass.data[DATA_CUSTOMIZE].get("update.kitchen_firmware")["entity_picture"] == "/local/mine.png"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert isinstance(hass.data[DATA_CUSTOMIZE], EntityValues)
    assert hass.data[DATA_CUSTOMIZE].get("update.kitchen_firmware")["friendly_name"] == "Kitchen"


async def test_a_customize_reload_is_dressed_again(hass: HomeAssistant):
    kitchen = await esphome_firmware(hass, "kitchen")
    entry = await setup(hass)
    hass.data[DATA_CUSTOMIZE] = EntityValues()  # what reloading the core configuration does
    await entry.runtime_data.async_refresh()
    assert hass.data[DATA_CUSTOMIZE].get(kitchen) == {"entity_picture": ICON}
    assert hass.states.get(kitchen).attributes["entity_picture"] == ICON
