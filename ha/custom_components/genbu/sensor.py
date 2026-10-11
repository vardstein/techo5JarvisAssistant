"""sensor.genbu_house_version: the version the house runs, the one every following Show moves to. The
Shows read it and two of its attributes over their ESPHome link: main_shows (am I a main update Show?)
and main_missing_since (for the line on their update page)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, HOUSE_ENTITY


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add([HouseVersion(entry)])


class HouseVersion(SensorEntity):
    _attr_icon = "mdi:home-import-outline"
    _attr_name = "Genbu house version"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"shows"})

    def __init__(self, entry: ConfigEntry) -> None:
        self.entity_id = HOUSE_ENTITY
        self._attr_unique_id = f"{entry.entry_id}_house_version"
        self.hub = entry.runtime_data

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        house = self.hub.house
        # No version yet (no main update Show has committed one): the Shows stay where they are.
        self._attr_native_value = house.version or None
        self._attr_extra_state_attributes = {
            "main_shows": house.mains,
            "main_missing_since": house.missing_since(),
            "since": house.since,
            "shows": {s.node: {"name": s.name, "installed": s.installed, "committed": s.committed,
                               "online": s.online} for s in self.hub.shows},
        }
        self.async_write_ha_state()
