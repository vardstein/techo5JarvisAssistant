"""Genbu: one update point per house. Finds the Genbu Shows that Home Assistant knows through ESPHome,
keeps which of them are main update Shows, publishes the house version the others follow in
sensor.genbu_house_version, and asks in Repairs when a main update Show goes missing. See
docs/update-leader.md."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from .const import DOMAIN, SERVICE_FORGET, STORE_KEY, STORE_VERSION
from .coordinator import GenbuHouse

PLATFORMS = [Platform.SENSOR, Platform.SELECT]
FORGET_SCHEMA = vol.Schema({vol.Required("node"): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = GenbuHouse(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub
    entry.async_on_unload(hub.async_stop)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not hass.services.has_service(DOMAIN, SERVICE_FORGET):
        async def forget(call: ServiceCall) -> ServiceResponse:
            return await _forget_show(hass, call)
        hass.services.async_register(DOMAIN, SERVICE_FORGET, forget, schema=FORGET_SCHEMA,
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.services.async_remove(DOMAIN, SERVICE_FORGET)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _forget_show(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """genbu.forget_show {node}: what the installer's remove-show calls once the Show's ESPHome entry is
    gone, so the house keeps nothing about it. Answers with the main update Shows left."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        raise ServiceValidationError("Genbu is not running")
    node = call.data["node"].strip()
    if not node:
        raise ServiceValidationError("node is empty")
    return await entry.runtime_data.async_forget(node)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await entry.runtime_data.async_refresh()


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Set up again, it starts from what the installer or the form says, not from the old house.
    await Store(hass, STORE_VERSION, STORE_KEY).async_remove()
