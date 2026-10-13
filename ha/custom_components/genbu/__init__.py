"""Genbu Assistant: one update point per house. Finds the Genbu Shows that Home Assistant knows through
ESPHome, keeps which of them are main update Shows, publishes the house version the others follow in
sensor.genbu_house_version, and asks in Repairs when a main update Show goes missing. The Genbu Control
Center in the sidebar has a card per Show, made from its ESPHome entities (frontend/). Genbu also serves
the Jarvis dashboard's cards and loads them on every page (ha-dashboard/README.md). And it gives the voice
assistant its tools, Show by Show (llm.py). See docs/update-leader.md and docs/genbu-assistant.md."""

from __future__ import annotations

import hashlib
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

from .const import (CARDS, CONF_SEARXNG, DOMAIN, MANUFACTURERS, NAME, OLD_NAME, OLD_RECIPES, PANEL, PANEL_FILES,
                    PANEL_TITLE, SERVICE_FORGET, SERVICE_INSTALL, STORE_KEY, STORE_VERSION)
from .coordinator import GenbuHouse, install_waiting, unhide

PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.SWITCH]
FORGET_SCHEMA = vol.Schema({vol.Required("node"): cv.string})
INSTALL_SCHEMA = vol.Schema({vol.Optional("except_node", default=""): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.title == OLD_NAME:  # set up before 1.4.0; a title somebody chose stays
        hass.config_entries.async_update_entry(entry, title=NAME)
    _take_recipe_search(hass, entry)
    _drop_copies(hass, entry)
    hub = GenbuHouse(hass, entry)
    hub.had_search = bool(entry.options.get(CONF_SEARXNG))
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
    if not hass.services.has_service(DOMAIN, SERVICE_INSTALL):
        async def install(call: ServiceCall) -> ServiceResponse:
            return await _install_updates(hass, call)
        hass.services.async_register(DOMAIN, SERVICE_INSTALL, install, schema=INSTALL_SCHEMA,
                                     supports_response=SupportsResponse.OPTIONAL)
    await _add_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.services.async_remove(DOMAIN, SERVICE_FORGET)
    hass.services.async_remove(DOMAIN, SERVICE_INSTALL)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    frontend.async_remove_panel(hass, PANEL, warn_if_unknown=False)
    for url in hass.data.pop(f"{DOMAIN}_cards", []):
        frontend.remove_extra_js_url(hass, url)
    # Saved now, not in a few seconds, so removing Genbu right after starts from the same house.
    hub = entry.runtime_data
    await hub.store.async_save(hub.house.data)
    return True


async def _add_panel(hass: HomeAssistant) -> None:
    """The Genbu Control Center in the sidebar: one card per Show, built in the browser from the Show's own
    ESPHome entities, so there is nothing to copy or keep in step. The version in the script's address
    makes browsers load it again after an update."""
    if not hass.data.get(f"{DOMAIN}_files"):
        # Static paths can't be taken back, so only once per Home Assistant run.
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_FILES, str(Path(__file__).parent / "frontend"), False)])
        hass.data[f"{DOMAIN}_files"] = True
    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.async_remove_panel(hass, PANEL, warn_if_unknown=False)
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL, webcomponent_name="genbu-panel", sidebar_title=PANEL_TITLE,
        sidebar_icon="mdi:tablet-dashboard", module_url=f"{PANEL_FILES}/genbu-panel.js?v={version}",
        config={"manufacturers": sorted(MANUFACTURERS), "title": PANEL_TITLE}, require_admin=False)
    await _add_cards(hass)


async def _add_cards(hass: HomeAssistant) -> None:
    """The dashboard's cards, loaded on every page from where the panel's script is. Each address carries
    a hash of its file rather than Genbu's version, so a browser that kept a card for a month (Home
    Assistant tells it to) loads the new one whenever the file changes, version bumped or not. A copy
    still loaded from /config/www as a resource does nothing: Genbu's card is defined first, and the
    cards define themselves only once."""
    if hass.data.get(f"{DOMAIN}_cards"):
        return
    folder = Path(__file__).parent / "frontend"

    def hashes() -> dict[str, str]:
        return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()[:12] for name in CARDS}

    urls = [f"{PANEL_FILES}/{name}?v={v}" for name, v in (await hass.async_add_executor_job(hashes)).items()]
    for url in urls:
        frontend.add_extra_js_url(hass, url)
    hass.data[f"{DOMAIN}_cards"] = urls


def _take_recipe_search(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The recipe search moved here from Genbu Rezepte in 1.4.0: its SearXNG address comes along, once, as
    long as Genbu Assistant has never been given one."""
    if CONF_SEARXNG in entry.options:
        return
    old = next((e.data.get(CONF_SEARXNG) for e in hass.config_entries.async_entries(OLD_RECIPES)
                if e.data.get(CONF_SEARXNG)), None)
    if old:
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SEARXNG: str(old).rstrip("/")})


def _drop_copies(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Genbu 1.2.0 copied each Show's controls onto its genbu device. The copies are gone since 1.3.0
    (GENBU-114), so their leftover registry entries go too; the coordinator shows the originals again."""
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id.startswith(f"{entry.entry_id}_copy_"):
            reg.async_remove(ent.entity_id)


async def _forget_show(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """genbu.forget_show {node}: what the installer's --remove-show calls once the Show's ESPHome entry is
    gone, so the house keeps nothing about it. Answers with the main update Shows left."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        raise ServiceValidationError("Genbu is not running")
    node = call.data["node"].strip()
    if not node:
        raise ServiceValidationError("node is empty")
    return await entry.runtime_data.async_forget(node)


async def _install_updates(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """genbu.install_updates {except_node}: what a Show calls when somebody says yes to its update, and
    the voice kit's "install the updates". Every Show with an update waiting installs it, even when the
    one that asked has none. except_node is the Show that asked and is installing already: a second
    install on top of its own would start the same download twice. Answers with who started and who is
    up to date; a Show that is offline has no update to install and is left out."""
    return await install_waiting(hass, call.data.get("except_node", ""))


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # A recipe search set up or taken away adds or drops the recipe sensor and the Recipes switches.
    if bool(entry.options.get(CONF_SEARXNG)) != entry.runtime_data.had_search:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    await entry.runtime_data.async_refresh()


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # The ESPHome originals Genbu 1.2.0 hid come back, if they haven't yet. Set up again, it starts from what the installer or the
    # form says, not from the old house.
    store = Store(hass, STORE_VERSION, STORE_KEY)
    unhide(hass, (await store.async_load() or {}).get("hidden", {}))
    await store.async_remove()
