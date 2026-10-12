"""Genbu: one update point per house. Finds the Genbu Shows that Home Assistant knows through ESPHome,
keeps which of them are main update Shows, publishes the house version the others follow in
sensor.genbu_house_version, and asks in Repairs when a main update Show goes missing. The Genbu page in
the sidebar has a card per Show, made from its ESPHome entities (frontend/). See docs/update-leader.md."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from .const import DOMAIN, MANUFACTURERS, PANEL, PANEL_FILES, SERVICE_FORGET, STORE_KEY, STORE_VERSION
from .coordinator import GenbuHouse, unhide

PLATFORMS = [Platform.SENSOR, Platform.SELECT]
FORGET_SCHEMA = vol.Schema({vol.Required("node"): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _drop_copies(hass, entry)
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
    await _add_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.services.async_remove(DOMAIN, SERVICE_FORGET)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    frontend.async_remove_panel(hass, PANEL, warn_if_unknown=False)
    # Saved now, not in a few seconds, so removing Genbu right after starts from the same house.
    hub = entry.runtime_data
    await hub.store.async_save(hub.house.data)
    return True


async def _add_panel(hass: HomeAssistant) -> None:
    """The Genbu page in the sidebar: one card per Show, built in the browser from the Show's own ESPHome
    entities, so there is nothing to copy or keep in step. The version in the script's address makes
    browsers load it again after an update."""
    if not hass.data.get(f"{DOMAIN}_files"):
        # Static paths can't be taken back, so only once per Home Assistant run.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_FILES, str(Path(__file__).parent / "frontend"), False)])
        hass.data[f"{DOMAIN}_files"] = True
    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.async_remove_panel(hass, PANEL, warn_if_unknown=False)
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL, webcomponent_name="genbu-panel", sidebar_title="Genbu",
        sidebar_icon="mdi:tablet-dashboard", module_url=f"{PANEL_FILES}/genbu-panel.js?v={version}",
        config={"manufacturers": sorted(MANUFACTURERS)}, require_admin=False)


def _drop_copies(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Genbu 1.2.0 copied each Show's controls onto its genbu device. The copies are gone since 1.3.0
    (GENBU-114), so their leftover registry entries go too; the coordinator shows the originals again."""
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id.startswith(f"{entry.entry_id}_copy_"):
            reg.async_remove(ent.entity_id)


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
    # The ESPHome originals Genbu 1.2.0 hid come back, if they haven't yet. Set up again, it starts from what the installer or the
    # form says, not from the old house.
    store = Store(hass, STORE_VERSION, STORE_KEY)
    unhide(hass, (await store.async_load() or {}).get("hidden", {}))
    await store.async_remove()
