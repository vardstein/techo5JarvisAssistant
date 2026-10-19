"""Genbu Assistant: one update point per house. Finds the Genbu Shows that Home Assistant knows through
ESPHome, publishes the house version they all follow in sensor.genbu_house_version, and offers the
release channel's next version on one update card, update.genbu_show_firmware (GENBU-119). The Genbu Control
Center in the sidebar has a card per Show, made from its ESPHome entities (frontend/). Genbu also serves
the Jarvis dashboard's cards and loads them on every page (ha-dashboard/README.md). And it gives the voice
assistant its tools, Show by Show (llm.py). See docs/update-leader.md and docs/genbu-assistant.md."""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from . import claims, delayed
from .const import (CARDS, CONF_AGENT, CONF_SEARXNG, DOMAIN, FRESH, MANUFACTURERS, NAME, OLD_NAME, OLD_RECIPES, PANEL, PANEL_FILES,
                    PANEL_TITLE, SERVICE_FORGET, SERVICE_INSTALL, SERVICE_MUSIC_ROUTE, SERVICE_SHOW_KEY, STORE_KEY,
                    STORE_VERSION)
from .coordinator import GenbuHouse, unhide
from .keycare import KeyRefused

PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.SWITCH, Platform.UPDATE]
FORGET_SCHEMA = vol.Schema({vol.Required("node"): cv.string})
INSTALL_SCHEMA = vol.Schema({vol.Optional("except_node", default=""): cv.string})
MUSIC_ROUTE_SCHEMA = vol.Schema({vol.Optional("show", default=""): cv.string,
                                 vol.Optional("player", default=""): cv.string})
SHOW_KEY_SCHEMA = vol.Schema({vol.Required("node"): cv.string, vol.Optional("url", default=""): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.title == OLD_NAME:  # set up before 1.4.0; a title somebody chose stays
        hass.config_entries.async_update_entry(entry, title=NAME)
    _take_recipe_search(hass, entry)
    _drop_copies(hass, entry)
    hub = GenbuHouse(hass, entry)
    hub.had_search = bool(entry.options.get(CONF_SEARXNG))
    hub.had_agent = entry.options.get(CONF_AGENT) or ""
    await hub.async_start()
    entry.runtime_data = hub
    entry.async_on_unload(hub.async_stop)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    entry.async_on_unload(claims.async_watch(hass))
    entry.async_on_unload(delayed.async_check(hass))
    await hass.config_entries.async_forward_entry_setups(entry, _platforms(hub.had_agent))
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
    if not hass.services.has_service(DOMAIN, SERVICE_MUSIC_ROUTE):
        async def music_route(call: ServiceCall) -> ServiceResponse:
            return _music_route(hass, call)
        hass.services.async_register(DOMAIN, SERVICE_MUSIC_ROUTE, music_route, schema=MUSIC_ROUTE_SCHEMA,
                                     supports_response=SupportsResponse.ONLY)
    if not hass.services.has_service(DOMAIN, SERVICE_SHOW_KEY):
        async def show_key(call: ServiceCall) -> ServiceResponse:
            return await _show_key(hass, call)
        # Admins only: a Show's key opens the house as wide as an admin's own.
        async_register_admin_service(hass, DOMAIN, SERVICE_SHOW_KEY, show_key, schema=SHOW_KEY_SCHEMA,
                                     supports_response=SupportsResponse.OPTIONAL)
    await _add_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.services.async_remove(DOMAIN, SERVICE_FORGET)
    hass.services.async_remove(DOMAIN, SERVICE_INSTALL)
    hass.services.async_remove(DOMAIN, SERVICE_MUSIC_ROUTE)
    hass.services.async_remove(DOMAIN, SERVICE_SHOW_KEY)
    if not await hass.config_entries.async_unload_platforms(entry, _platforms(entry.runtime_data.had_agent)):
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
    cards define themselves only once.

    The addresses are written into the page, and Home Assistant's service worker hands out the page it
    kept while it fetches the new one for next time. So after an update the first page, and any tab left
    open through the restart, still has the old cards. genbu-fresh.js comes along carrying a hash of all
    the files; it asks genbu/frontend for the current one and reloads the page when they differ."""
    if hass.data.get(f"{DOMAIN}_cards"):
        return
    folder = Path(__file__).parent / "frontend"

    def hashes() -> dict[str, str]:
        return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()[:12] for name in (*CARDS, FRESH)}

    found = await hass.async_add_executor_job(hashes)
    current = hashlib.sha256(" ".join(found[name] for name in (*CARDS, FRESH)).encode()).hexdigest()[:12]
    hass.data[f"{DOMAIN}_frontend"] = current
    if not hass.data.get(f"{DOMAIN}_ws"):
        websocket_api.async_register_command(hass, _ws_frontend)
        websocket_api.async_register_command(hass, _ws_shows)
        hass.data[f"{DOMAIN}_ws"] = True
    urls = [f"{PANEL_FILES}/{name}?v={found[name]}" for name in CARDS] + [f"{PANEL_FILES}/{FRESH}?v={current}"]
    for url in urls:
        frontend.add_extra_js_url(hass, url)
    hass.data[f"{DOMAIN}_cards"] = urls


@websocket_api.websocket_command({vol.Required("type"): "genbu/frontend"})
@callback
def _ws_frontend(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """The hash genbu-fresh.js compares its own with; None while Genbu is not set up."""
    connection.send_result(msg["id"], {"version": hass.data.get(f"{DOMAIN}_frontend")
                                       if hass.data.get(f"{DOMAIN}_cards") else None})


@websocket_api.websocket_command({vol.Required("type"): "genbu/shows"})
@websocket_api.async_response
async def _ws_shows(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """The Shows a dashboard card is about (GENBU-164). On a Show, whose dashboard is signed in as the Show's
    own user, only that Show; anywhere else every Show, and own is false. Each with its device id, name and
    voice satellite, which any user can see on the Shows' own entities already."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        connection.send_result(msg["id"], {"own": False, "shows": []})
        return
    hub = entry.runtime_data
    mine = await hub.keycare.show_of(connection.user.id, list(hub.shows))
    shows = [mine] if mine else [s for s in hub.shows if s.device]
    connection.send_result(msg["id"], {"own": mine is not None, "shows": [
        {"device": s.device, "name": s.name, "satellite": s.satellite} for s in shows]})


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
    gone, so the house keeps nothing about it."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        raise ServiceValidationError("Genbu is not running")
    node = call.data["node"].strip()
    if not node:
        raise ServiceValidationError("node is empty")
    return await entry.runtime_data.async_forget(node)


async def _install_updates(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """genbu.install_updates {except_node}: what a Show calls when somebody says yes to an update, and the
    voice kit's "install the updates". When the house's card has one waiting, the house moves and every
    Show follows. Without one, each Show still behind with an update of its own installs it, except
    except_node, the Show that asked and is installing already (a Show from before 1.4.0 does): a second
    install on top of its own would start the same download twice. Answers with who started and who is
    up to date; a Show that is offline is left out."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        raise ServiceValidationError("Genbu is not running")
    return await entry.runtime_data.async_install_updates(call.data.get("except_node", ""))


def _music_route(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """genbu.music_route {show, player}: where music asked for on a Show plays now, the speakers passed
    over because they are off, and whether to mention them (GENBU-161). show is the Show's device or
    satellite; player, a speaker somebody named. Only looks things up, which any user sees in the states
    anyway, so it is no admin action."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        raise ServiceValidationError("Genbu is not running")
    return entry.runtime_data.music_route(call.data.get("show", "").strip(), call.data.get("player", "").strip())


async def _show_key(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """genbu.show_key {node, url}: a Show gets a new Home Assistant key, for its user, and a user first
    when it has none; its other keys go once it stored the new one. What the installer calls, and what
    an admin does for a Show whose key was lost. url, when given, is where the Show finds Home Assistant
    from now on. Admins only (GENBU-196)."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    if entry is None:
        raise ServiceValidationError("Genbu is not running")
    hub = entry.runtime_data
    node = call.data["node"].strip().lower()
    show = next((s for s in hub.shows if s.node.lower() == node), None)
    if show is None:
        raise ServiceValidationError(f"no Genbu Show called {node} in ESPHome")
    try:
        return await hub.keycare.async_renew(show, call.data.get("url", "").strip().rstrip("/"))
    except KeyRefused as err:
        raise ServiceValidationError(f"{show.name} gets no new key: {REFUSALS.get(err.reason, err.reason)}") from err


# Why genbu.show_key gave no key, in words (the Repairs button has them in its translations).
REFUSALS = {
    "not_encrypted": "its ESPHome connection has no encryption key, and a key only goes over an encrypted one",
    "user_off": "its user is switched off; switch it on in Settings, People first",
    "not_a_show_user": "its user is an admin or may sign in from outside the home; a Show's user may neither",
    "not_stored": "the Show did not store it",
    "no_provider": "Home Assistant has no sign-in with usernames to make the Show's user with",
    "name_taken": "both sign-in names for its user are someone else's",
    "not_an_address": "the address is no http:// or https:// address",
    "cloud_address": "a Show's user may only sign in from home, so it can't use a Home Assistant Cloud address",
}


def _platforms(agent: str) -> list[Platform]:
    # The conversation agent only while one is picked to hand on to; it also keeps Assist's conversation
    # component from being set up for a house that doesn't use it. Unloading goes by the agent the entry
    # was set up with, as the options have changed by then.
    return [Platform.CONVERSATION, *PLATFORMS] if agent else PLATFORMS


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # A recipe search set up or taken away adds or drops the recipe sensor and the Recipes switches; an
    # agent picked or changed, Genbu's own conversation agent.
    hub = entry.runtime_data
    if bool(entry.options.get(CONF_SEARXNG)) != hub.had_search \
            or (entry.options.get(CONF_AGENT) or "") != hub.had_agent:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    await entry.runtime_data.async_refresh()


async def async_remove_config_entry_device(hass: HomeAssistant, config_entry: ConfigEntry,
                                          device_entry: dr.DeviceEntry) -> bool:
    """Delete on a Show's Genbu device: the Show leaves the house and is reset to setup (GENBU-223,
    removal.py). The Genbu Assistant device itself can't be deleted; removing the integration does that."""
    show = next((i for d, i in device_entry.identifiers if d == DOMAIN), None)
    if show is None or show == config_entry.entry_id or config_entry.state is not ConfigEntryState.LOADED:
        return False
    return await config_entry.runtime_data.removal.async_start(show)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # The ESPHome originals Genbu 1.2.0 hid come back, if they haven't yet. Set up again, it starts from
    # where the Shows are, not from the old house.
    store = Store(hass, STORE_VERSION, STORE_KEY)
    unhide(hass, (await store.async_load() or {}).get("hidden", {}))
    await store.async_remove()
