"""select.genbu_main_update_show: which Show is the house's main update Show. Picking another one here
is how the main moves by hand; a second main is added in the integration's options."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, MAIN_SELECT


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add([MainShow(entry)])


class MainShow(SelectEntity):
    _attr_icon = "mdi:crown-outline"
    _attr_name = "Genbu main update Show"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self.entity_id = MAIN_SELECT
        self._attr_unique_id = f"{entry.entry_id}_main_update_show"
        self.hub = entry.runtime_data
        self._attr_options = []

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        # By ESPHome node name, the name a Show finds itself by in main_shows. A main that HA lost
        # stays in the list so the select still says who it is.
        mains = self.hub.house.mains
        self._attr_options = sorted({s.node for s in self.hub.shows} | set(mains))
        self._attr_current_option = mains[0] if mains else None
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_make_main(option)
