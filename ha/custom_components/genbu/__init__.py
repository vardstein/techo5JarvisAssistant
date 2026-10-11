"""Genbu: one update point per house. Finds the Genbu Shows that Home Assistant knows through ESPHome,
keeps which of them are main update Shows, publishes the house version the others follow in
sensor.genbu_house_version, and asks in Repairs when a main update Show goes missing. See
docs/update-leader.md."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORE_KEY, STORE_VERSION
from .coordinator import GenbuHouse

PLATFORMS = [Platform.SENSOR, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = GenbuHouse(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub
    entry.async_on_unload(hub.async_stop)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await entry.runtime_data.async_refresh()


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Set up again, it starts from what the installer or the form says, not from the old house.
    await Store(hass, STORE_VERSION, STORE_KEY).async_remove()
