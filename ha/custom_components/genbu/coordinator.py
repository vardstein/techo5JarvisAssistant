"""GenbuHouse: looks at the Genbu Shows every minute and whenever one of them changes, feeds that to
house.House, and keeps the house version: the one every Show follows, which only an install on the house's
update card (update.py), the night install or the voice moves (GENBU-119). It reads the release channel's
manifest every hour, and on Check for updates, for what that card offers.

Each look also works out where each Show's music plays (music.py), so a Show moved to another room
plays there without anything being set up again, and asks a Show turned away by Home Assistant why
(keycare.py). It puts the Genbu icon on each Show's firmware update (picture.py). And it keeps each Show's Recipes and Updates by voice,
which decide what the voice assistant is offered on that Show (llm.py)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

import aiohttp

from homeassistant.auth import EVENT_USER_ADDED, EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventOrigin, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import picture, race, reply_cut
from .const import (CHANGED, CONF_SEARXNG, DOMAIN, MANUFACTURERS, NAME, NIGHT_AT, PANEL, RECIPES, SHOW_RECIPE,
                    RACED, WAKE_EVENT, WAKE_RACE, WAKE_VERDICT,
                    STORE_KEY, STORE_VERSION, UPDATE_ENTITY, VOICE_UPDATES)
from .house import House, Show, manifest_url, newer, tag_url, valid
from .keycare import ISSUE, KeyCare
from .music import Player, falls_back_to, own_player, plays_on, room_player, route
from .recipe import show_action

LOGGER = logging.getLogger(__name__)
LOOK_EVERY = timedelta(minutes=1)
READ_EVERY = timedelta(hours=1)  # the channel's manifest; 6 hours kept a new release off the card for half a day (GENBU-258)
SETTLE = timedelta(minutes=30)  # how long after an install the card shows how far the Shows are
TELL_EVERY = timedelta(hours=1)  # how often a Show mentions a speaker that is off (GENBU-161)


def find_shows(hass: HomeAssistant) -> list[Show]:
    """Every Genbu Show the ESPHome integration knows, with the entities the house reads."""
    devices, entities = dr.async_get(hass), er.async_get(hass)
    shows: dict[str, Show] = {}
    for entry in hass.config_entries.async_entries("esphome"):
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
            if device.manufacturer not in MANUFACTURERS or device.via_device_id:
                continue
            node = entry.data.get("device_name") or _slug(device.name or "")
            firmware = committed = status = satellite = key = cut = None
            for e in er.async_entries_for_device(entities, device.id):
                if e.domain == "assist_satellite" and not e.disabled_by:
                    satellite = e.entity_id
                elif e.domain == "sensor" and e.unique_id.endswith("-home_assistant_key") and not e.disabled_by:
                    key = e.entity_id
                elif e.domain == "event" and e.unique_id.endswith("-reply_cut") and not e.disabled_by:
                    cut = e.entity_id
                elif e.domain == "update" and (e.unique_id.endswith("-firmware") or e.entity_id.endswith("_firmware")):
                    firmware = e.entity_id
                elif e.domain == "sensor" and (e.unique_id.endswith("-committed_version")
                                               or e.entity_id.endswith("_committed_version")):
                    committed = e.entity_id
                elif e.domain == "sensor" and (e.unique_id.endswith("-update_status")
                                               or e.entity_id.endswith("_update_status")):
                    status = e.entity_id
            if not node or not firmware:
                continue
            up = hass.states.get(firmware)
            done = hass.states.get(committed) if committed else None
            line = hass.states.get(status) if status else None
            at = up.attributes.get("update_percentage") if up else None
            shows[node] = Show(
                node=node,
                name=device.name_by_user or device.name or node,
                installed=(up.attributes.get("installed_version") or "") if up else "",
                committed=done.state if done else "",
                online=bool(up) and up.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
                watch=(firmware, committed, status),
                device=device.id,
                area=device.area_id or "",
                satellite=satellite or "",
                mac=next((v for k, v in device.connections if k == dr.CONNECTION_NETWORK_MAC), ""),
                key=key or "",
                encrypted=bool(entry.data.get("noise_psk")),
                model=device.model or "",
                status=line.state if line and line.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN) else "",
                updating=bool(up and up.attributes.get("in_progress")),
                at=round(at) if isinstance(at, (int, float)) else None,
                cut=cut or "",
            )
    return sorted(shows.values(), key=lambda s: s.node)


async def install_waiting(hass: HomeAssistant, skip: str = "") -> dict[str, list[str]]:
    """Starts the waiting update on every Show that has one, except the Show named skip (it is installing
    already). Answers with who started and who is up to date; a Show that is offline is left out."""
    skip = skip.strip().lower()
    updating, current = [], []
    for show in find_shows(hass):
        if skip and show.node.lower() == skip:
            continue
        state = hass.states.get(show.watch[0]) if show.watch else None
        if state is None or state.state not in ("on", "off"):
            continue
        if state.state == "on":
            await hass.services.async_call("update", "install", {"entity_id": show.watch[0]}, blocking=False)
            updating.append(show.name)
        else:
            current.append(show.name)
    return {"updating": updating, "current": current}


def _macs(hass: HomeAssistant) -> dict[str, str]:
    """The MAC Music Assistant has for each of its players' devices, by player id, which is the
    unique id of its media player. Home Assistant's device for a player doesn't carry it."""
    macs = {}
    for entry in hass.config_entries.async_entries("music_assistant"):
        mass = getattr(getattr(entry, "runtime_data", None), "mass", None)
        for p in getattr(getattr(mass, "players", None), "players", None) or []:
            if mac := getattr(getattr(p, "device_info", None), "mac_address", None):
                macs[p.player_id] = mac
    return macs


def find_players(hass: HomeAssistant) -> list[Player]:
    """Music Assistant's media players, with the room and maker each one has in Home Assistant."""
    devices, entities = dr.async_get(hass), er.async_get(hass)
    macs = _macs(hass)
    players = []
    for e in entities.entities.values():
        if e.platform != "music_assistant" or e.domain != "media_player" or e.disabled_by:
            continue
        device = devices.async_get(e.device_id) if e.device_id else None
        state = hass.states.get(e.entity_id)
        players.append(Player(
            entity=e.entity_id,
            name=(state.name if state else None) or e.name or e.original_name or e.entity_id,
            area=e.area_id or (device.area_id if device else None) or "",
            maker=(device.manufacturer if device else None) or "",
            device_name=(device.name if device else None) or "",
            available=bool(state) and state.state != STATE_UNAVAILABLE,
            mac=macs.get(e.unique_id, ""),
        ))
    return sorted(players, key=lambda p: p.entity)


def unhide(hass: HomeAssistant, hidden: dict[str, bool]) -> None:
    """Shows the ESPHome entities Genbu 1.2.0 hid behind its copies again, unless someone else hid them
    since. By registry id, so a renamed one is found too."""
    entities = er.async_get(hass)
    for rid, ours in hidden.items():
        e = entities.async_get(rid)
        if ours and e is not None and e.hidden_by is er.RegistryEntryHider.INTEGRATION:
            entities.async_update_entity(e.entity_id, hidden_by=None)


def _offline(state) -> bool:
    return state is None or state.state == STATE_UNAVAILABLE


def show_device(show: Show) -> DeviceInfo:
    """The small genbu device a Show's settings sit on. Home Assistant lets a device belong to one
    integration only, so it is a device of its own, connected via the Show's ESPHome device."""
    return DeviceInfo(identifiers={(DOMAIN, show.device)}, name=show.name, model="Genbu Show settings",
                      via_device_id=show.device)


def house_device(entry: ConfigEntry) -> DeviceInfo:
    """The device the house's own controls sit on: the update card, the channel, the night install."""
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=NAME, manufacturer="OpenJade",
                      model="Genbu Shows", entry_type=dr.DeviceEntryType.SERVICE,
                      configuration_url=f"homeassistant://{PANEL}")  # "Visit" opens the Control Center


class GenbuHouse:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.store: Store[dict] = Store(hass, STORE_VERSION, STORE_KEY)
        self.house = House()
        self.shows: list[Show] = []
        self.players: list[Player] = []
        self.music: dict[str, str] = {}  # where each Show's music plays, by device id
        self.fallback: dict[str, str] = {}  # where it goes when that player is offline, by device id
        # When each Show last said a speaker was off and it played elsewhere, by (device id, player).
        self.told: dict[tuple[str, str], datetime] = {}
        self.cooking: set[str] = set()  # the Shows looking a recipe up right now, by device id
        self.had_search = False  # whether a recipe search was set up when the entry was
        self.had_agent = ""  # the agent Genbu's own hands on to, when the entry was set up (GENBU-253)
        self.release: dict = {}  # the channel's newest manifest, {} until it was read
        self.installed_at: datetime | None = None  # the last install, for how far the Shows are
        self.keycare = KeyCare(hass, self.house, self._save)
        self.referee = race.Referee()
        self.racers: dict[str, str] = {}  # the Shows that race their wake words: device id → wake_verdict
        self._closing: dict[str, asyncio.TimerHandle] = {}  # the races waiting out their window, by phrase
        self.raced: dict = {}  # the last race, as sensor.genbu_wake_race shows it
        self.cuts = reply_cut.Cuts()  # the replies a wake word cut off, for the turn after (GENBU-250)
        self._cut_by: dict[str, str] = {}  # the Shows' reply_cut events: entity id → device id
        self._watched: set[str] = set()
        self._player_ids: set[str] = set()
        self._stops: list = []
        self._debounce = Debouncer(hass, LOGGER, cooldown=2, immediate=True, function=self.async_refresh)

    async def async_start(self) -> None:
        self.house = House(await self.store.async_load() or {})
        self.keycare = KeyCare(self.hass, self.house, self._save)
        self._save()  # without the main update Shows of before 1.4.0, if there were any
        if hidden := self.house.data.pop("hidden", None):
            # Left by 1.2.0, whose copies are gone (GENBU-114): the Shows' own entities come back.
            unhide(self.hass, hidden)
        for domain, issue in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and not issue.startswith(ISSUE):  # the main update Show's of before 1.4.0
                ir.async_delete_issue(self.hass, DOMAIN, issue)
        entities = er.async_get(self.hass)
        if old := entities.async_get_entity_id("select", DOMAIN, f"{self.entry.entry_id}_main_update_show"):
            entities.async_remove(old)  # and its select
        self._stops.append(async_track_time_interval(self.hass, self._tick, LOOK_EVERY))
        self._stops.append(async_track_time_interval(self.hass, self._read, READ_EVERY))
        hour, minute = NIGHT_AT
        self._stops.append(async_track_time_change(self.hass, self._night, hour=hour, minute=minute, second=0))
        self._stops.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed,
                                                      event_filter=self._watching))
        self._stops.append(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._changed,
                                                      event_filter=self._not_ours))
        # A player or a Show moved to another room, or a new player.
        self._stops.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._changed,
                                                      event_filter=self._player_or_satellite))
        # A user switched on, changed or deleted: the Shows turned away are asked again now.
        for event in (EVENT_USER_ADDED, EVENT_USER_UPDATED, EVENT_USER_REMOVED):
            self._stops.append(self.hass.bus.async_listen(event, self._user_changed))
        self._stops.append(self.hass.bus.async_listen(WAKE_EVENT, self._wake))
        self._stops.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._reply_cut,
                                                      event_filter=self._a_cut))
        await self.async_refresh()
        # Not waited for: a slow GitHub must not hold Home Assistant's start up.
        self.entry.async_create_background_task(self.hass, self.async_read_release(), f"{DOMAIN} release")

    @callback
    def async_stop(self) -> None:
        for stop in self._stops:
            stop()
        self._stops.clear()
        self._debounce.async_cancel()
        for handle in self._closing.values():
            handle.cancel()
        self._closing.clear()
        picture.undress(self.hass)

    @callback
    def _watching(self, data) -> bool:
        if data["entity_id"] in self._watched:
            return True
        # A speaker going offline or coming back moves the music at once, not on the next look.
        return data["entity_id"] in self._player_ids and _offline(data["old_state"]) != _offline(data["new_state"])

    @callback
    def _not_ours(self, data) -> bool:
        # The genbu devices the music selects sit on would only start another look.
        device = dr.async_get(self.hass).async_get(data["device_id"])
        return device is None or device.config_entry_id != self.entry.entry_id

    @callback
    def _player_or_satellite(self, data) -> bool:
        return data["entity_id"].split(".", 1)[0] in ("media_player", "assist_satellite")

    @callback
    def _changed(self, _event: Event) -> None:
        self.hass.async_create_task(self._debounce.async_call())

    @callback
    def _user_changed(self, event: Event) -> None:
        self.keycare.again()
        self._changed(event)

    async def _tick(self, _now: datetime) -> None:
        await self.async_refresh()

    def name(self, node: str) -> str:
        return next((s.name for s in self.shows if s.node == node), node)

    # The house's update.

    @property
    def offered(self) -> str:
        """The version the channel has, when it is newer than the house; else ""."""
        latest = str(self.release.get("version") or "")
        return latest if valid(latest) and self.house.version and newer(latest, self.house.version) else ""

    @property
    def waiting(self) -> str:
        """The update waiting for the house: offered, unless somebody skipped it on the card."""
        offered = self.offered
        state = self.hass.states.get(UPDATE_ENTITY)
        # The skipped version, not the card's state: that one may not be written yet when this is read.
        if offered and state is not None and state.attributes.get("skipped_version") == offered:
            return ""
        return offered

    @property
    def asker(self) -> str:
        """The one Show that asks out loud about the waiting update, so it is not asked in every room at
        once (GENBU-119): the first online Show by node name that runs the house version; "" with
        nothing waiting."""
        if not self.waiting:
            return ""
        ready = sorted(s.node for s in self.shows if s.online and s.installed == self.house.version)
        return ready[0] if ready else ""

    def progress(self) -> int | None:
        """How many of the Shows that are online run the house version, in percent, for a while after an
        install; None otherwise."""
        if self.installed_at is None or dt_util.utcnow() - self.installed_at > SETTLE:
            return None
        online = [s for s in self.shows if s.online]
        if not online:
            return None
        done = sum(1 for s in online if s.installed == self.house.version)
        if done == len(online):
            self.installed_at = None
            return None
        return round(100 * done / len(online))

    async def _read(self, _now: datetime) -> None:
        await self.async_read_release()

    async def async_read_release(self) -> None:
        """Reads the channel's newest manifest; what was read before stays when GitHub doesn't answer."""
        try:
            self.release = await self._manifest(manifest_url(self.house.channel))
        except HomeAssistantError as err:
            LOGGER.info("Could not read the %s channel: %s", self.house.channel, err)
        async_dispatcher_send(self.hass, CHANGED)

    async def _manifest(self, url: str) -> dict:
        try:
            async with asyncio.timeout(30):
                resp = await async_get_clientsession(self.hass).get(url)
                if resp.status == 404:
                    raise HomeAssistantError(f"no release there ({url})")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"{url}: {err}") from err
        if not isinstance(data, dict) or not valid(str(data.get("version") or "")):
            raise HomeAssistantError(f"{url} is no Genbu manifest")
        return data

    async def async_install_house(self, version: str | None = None, backup: bool = False) -> str:
        """Moves the house to version, or to the one waiting; every Show follows. Going to a version the
        channel doesn't offer (back, or a build of its own) first checks that the release is there.
        Answers with the version the house moved to, "" when there was nothing to install."""
        version = (version or "").strip()
        if version and not valid(version):
            raise HomeAssistantError(f"{version} is no version")
        target = version or self.offered
        if not target:
            return ""
        if target != str(self.release.get("version") or ""):
            await self._manifest(tag_url(target))
        if backup:
            await self.store.async_save(self.house.data)
            if self.hass.services.has_service("backup", "create_automatic"):
                await self.hass.services.async_call("backup", "create_automatic", blocking=True)
        self.house.move(target, dt_util.utcnow())
        self.installed_at = dt_util.utcnow()
        LOGGER.info("The house moves to %s; the Shows follow", target)
        await self.store.async_save(self.house.data)
        await self.async_refresh()
        return target

    async def async_pick_channel(self, channel: str) -> None:
        self.house.pick_channel(channel)
        self.release = {}
        self._save()
        await self.async_read_release()

    async def async_set_night(self, on: bool) -> None:
        self.house.set_night(on)
        self._save()
        async_dispatcher_send(self.hass, CHANGED)

    async def _night(self, _now: datetime) -> None:
        if not self.house.night:
            return
        await self.async_read_release()
        if self.waiting:
            await self.async_install_house()

    async def async_install_updates(self, skip: str = "") -> dict[str, list[str]]:
        """genbu.install_updates and the voice: a waiting house update moves the house, and every Show
        follows it; without one, any Show still behind the house is started on its own update."""
        if waiting := self.waiting:
            await self.async_install_house(waiting)
            names = [s.name for s in self.shows if s.online]
            return {"updating": names, "current": []}
        return await install_waiting(self.hass, skip)

    # What the services and the selects do.

    async def async_forget(self, node: str) -> dict:
        """genbu.forget_show: the Show is gone for good."""
        self.house.forget(node)
        self._save()
        await self.async_refresh()
        return {}

    async def async_choose_music(self, device: str, player: str) -> None:
        self.house.choose_music(device, player)
        self._save()
        await self.async_refresh()

    @property
    def searxng(self) -> str:
        return str(self.entry.options.get(CONF_SEARXNG) or "").rstrip("/")

    def show(self, device: str | None) -> Show | None:
        return next((s for s in self.shows if device and s.device == device), None)

    def recipe_page(self, show: Show) -> bool:
        """Whether the Show can put the recipe page up: its firmware has the action for it."""
        actions = list(self.hass.services.async_services_for_domain("esphome"))
        return show_action(actions, [show.name, show.node], True, SHOW_RECIPE) is not None

    def switched_on(self, show: Show, key: str) -> bool:
        """A Show's Recipes or Updates by voice. Until somebody switches it, Updates by voice is on, and
        Recipes is on where there is a recipe search and the Show has the recipe page."""
        on = self.house.switch(show.device, key)
        if on is not None:
            return on
        if key == RECIPES:
            return bool(self.searxng) and self.recipe_page(show)
        return key == VOICE_UPDATES

    async def async_switch(self, device: str, key: str, on: bool) -> None:
        self.house.set_switch(device, key, on)
        self._save()
        async_dispatcher_send(self.hass, CHANGED)

    def usual_player(self, show: Show) -> str:
        """Where the Show's music goes when nothing is picked for it."""
        return plays_on("", show.area, [show.name, show.node], self.players, show.mac)

    def automatic(self, show: Show) -> tuple[Player | None, Player | None]:
        """What Automatic is made of for this Show: the room's speaker and the Show's own player."""
        by = {p.entity: p for p in self.players}
        return (by.get(room_player(show.area, self.players)),
                by.get(own_player([show.name, show.node], self.players, show.mac)))

    def music_route(self, asker: str = "", player: str = "") -> dict:
        """genbu.music_route: where music asked for on a Show (asker, its device or satellite) plays now,
        as facts, with tell when the voice should mention the speakers passed over: the first time, and
        then at most once an hour (GENBU-161). A named player works without a Show, which then has no
        Show to offer instead. {} with neither."""
        show = next((s for s in self.shows if asker and asker in (s.device, s.satellite)), None)
        if show is None and not player:
            return {}
        if show is None:
            return {**route("", "", [], self.players, asked=player), "tell": False}
        facts = route(self.house.music_choice(show.device), show.area, [show.name, show.node], self.players,
                      show.mac, player)
        now, tell = dt_util.utcnow(), False
        for p in facts["skipped"]:
            key = (show.device, p["player"])
            if key not in self.told or now - self.told[key] >= TELL_EVERY:
                self.told[key], tell = now, True
        facts["tell"] = tell
        return facts

    async def async_refresh(self) -> None:
        self.shows = find_shows(self.hass)
        self.players = find_players(self.hass)
        # A speaker that is back is mentioned again the next time it is off.
        up = {p.entity for p in self.players if p.available}
        self.told = {k: t for k, t in self.told.items() if k[1] not in up}
        self.music = {s.device: plays_on(self.house.music_choice(s.device), s.area, [s.name, s.node],
                                         self.players, s.mac) for s in self.shows if s.device}
        self.fallback = {s.device: falls_back_to(self.house.music_choice(s.device), s.area, [s.name, s.node],
                                                 self.players, s.mac) for s in self.shows if s.device}
        # The house's card too: skipping its update there tells the Shows to stop asking.
        self._watched = ({e for s in self.shows for e in s.watch if e} | {s.key for s in self.shows if s.key}
                         | {UPDATE_ENTITY})
        self._player_ids = {p.entity for p in self.players}
        # Asked over the encrypted link only, which the Show checks too.
        self._cut_by = {s.cut: s.device for s in self.shows if s.cut and s.encrypted and s.device}
        changed = self._tidy()
        changed = self.house.see(self.shows, dt_util.utcnow()) or changed
        if changed:
            self._save()
        async_dispatcher_send(self.hass, CHANGED)
        picture.dress(self.hass, {s.watch[0] for s in self.shows if s.watch and s.watch[0]})
        # Not waited for: asking a Show that hangs must not hold the look up.
        self.entry.async_create_background_task(self.hass, self.keycare.async_look(list(self.shows)),
                                                f"{DOMAIN} keys")
        self._race_again()

    # A reply cut off by a wake word (GENBU-250, reply_cut.py).

    @callback
    def _a_cut(self, data) -> bool:
        """A Show's reply_cut event fired. Not the state it comes back with when the Show or Home Assistant
        starts: that is an old cut."""
        old, new = data["old_state"], data["new_state"]
        return (data["entity_id"] in self._cut_by and old is not None and new is not None
                and old.state != STATE_UNAVAILABLE and new.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                and new.state != old.state and new.attributes.get("event_type") == reply_cut.EVENT_TYPE)

    @callback
    def _reply_cut(self, event: Event) -> None:
        device = self._cut_by.get(event.data["entity_id"])
        if device:
            self.entry.async_create_background_task(self.hass, self._fetch_cut(device), f"{DOMAIN} reply cut")

    async def _fetch_cut(self, device: str) -> None:
        """Asks the Show for the words heard and keeps them for its next turn."""
        show = self.show(device)
        if show is None or not show.encrypted:
            return
        action = show_action(list(self.hass.services.async_services_for_domain("esphome")), [show.name, show.node],
                             True, reply_cut.ACTION)
        if action is None:
            return
        try:
            async with asyncio.timeout(5):
                answer = await self.hass.services.async_call("esphome", action, {}, blocking=True,
                                                             return_response=True)
        except (HomeAssistantError, TimeoutError) as exc:
            LOGGER.debug("%s did not answer: %s", action, exc)
            return
        if (cut := reply_cut.from_answer(answer, dt_util.utcnow())) is not None:
            self.cuts.put(device, cut)
            LOGGER.debug("%s: reply cut off after %d characters", show.name, len(cut.heard))

    def take_cut(self, device: str | None) -> reply_cut.Cut | None:
        """The cut waiting for this device's turn, once."""
        return self.cuts.take(device, dt_util.utcnow())

    # The wake race (GENBU-224, race.py).

    def _race_again(self) -> None:
        """Renews the lease of every Show online that can race, which lasts it three minutes: a Show this
        no longer reaches stops waiting for verdicts by itself. Over the encrypted link only, which the
        Show checks too."""
        actions = list(self.hass.services.async_services_for_domain("esphome"))
        self.racers, leases = {}, []
        for s in self.shows:
            if not (s.online and s.encrypted and s.device):
                continue
            verdict = show_action(actions, [s.name, s.node], True, WAKE_VERDICT)
            lease = show_action(actions, [s.name, s.node], True, WAKE_RACE)
            if verdict and lease:
                self.racers[s.device] = verdict
                leases.append(lease)
        if leases:
            self.entry.async_create_background_task(self.hass, self._lease(leases), f"{DOMAIN} wake race")

    async def _lease(self, leases: list[str]) -> None:
        await asyncio.gather(*(self._esphome(a, {}) for a in leases))

    async def _esphome(self, action: str, data: dict) -> None:
        try:
            await self.hass.services.async_call("esphome", action, data, blocking=False)
        except HomeAssistantError as exc:
            LOGGER.debug("%s did not go out: %s", action, exc)

    @callback
    def _wake(self, event: Event) -> None:
        """A Show heard its wake word and waits for its verdict."""
        # The ESPHome integration fires a Show's events here; one fired through the API is somebody else.
        if event.origin is not EventOrigin.local:
            return
        show = self.show(event.data.get("device_id"))
        if show is None or not show.encrypted:
            return
        h = race.heard(show.device, show.name, event.data, show.model)
        if h is None:
            return
        what = self.referee.hear(h, set(self.racers), self.hass.loop.time())
        if what == "late":
            won = self.referee.would_have_won(h)
            LOGGER.info("wake race %r: %s heard it late, would have %s: %s", h.phrase, h.name,
                        "won" if won else "lost", race.describe(h))
            if self.raced.get("phrase") == h.phrase:
                self.raced = {**self.raced, "late": [*self.raced.get("late", []),
                                                     {"show": h.name, "would_have_won": won}]}
                async_dispatcher_send(self.hass, RACED)
            self._verdict(h, False)
        elif what == "open":
            self._closing[h.phrase] = self.hass.loop.call_later(race.WINDOW, self._close, h.phrase)
        elif what == "close":
            self._close(h.phrase)

    @callback
    def _close(self, phrase: str) -> None:
        if (handle := self._closing.pop(phrase, None)) is not None:
            handle.cancel()
        v = self.referee.close(phrase, self.hass.loop.time())
        if v is None:
            return
        self._verdict(v.winner, True)
        for h in v.losers:
            self._verdict(h, False)
        last = self.referee.races[phrase].last
        LOGGER.info("wake race %r: %s answers%s, after %.0f ms. %s", phrase, v.winner.name,
                    f" (unsure: {v.unsure})" if v.unsure else "", v.waited * 1000,
                    "; ".join(f"{race.describe(h, last)} {v.shares.get(h.wake, 0):.0%}"
                              for h in [v.winner, *v.losers]))
        self.raced = {
            "winner": v.winner.name, "phrase": phrase, "at": dt_util.utcnow().isoformat(),
            "waited_ms": round(v.waited * 1000), "unsure": v.unsure, "still_talking": show.name if (show := self.show(last)) else "",
            "shows": {h.name: {"share": round(v.shares.get(h.wake, 0) * 100), "points": round(race.points(
                h, h.show == last), 1), "level_db": round(h.level_db, 1), "over_noise_db": round(h.over_noise, 1),
                "sure": round(h.certainty, 2), "clipped": h.clipped, "odd": h.odd} for h in [v.winner, *v.losers]},
            "late": [],
        }
        async_dispatcher_send(self.hass, RACED)
        for h in [v.winner, *v.losers]:
            if h.odd:
                LOGGER.warning("wake race: %s said levels no microphone gives, taken as the lowest", h.name)
        if v.flagged is not None:
            LOGGER.warning("wake race: %s answered, but %s heard the voice %.0f dB further over its noise",
                           v.winner.name, v.flagged.name, v.flagged.over_noise - v.winner.over_noise)

    @callback
    def _verdict(self, h: race.Heard, won: bool) -> None:
        show = self.show(h.show)
        action = self.racers.get(h.show) or (show_action(
            list(self.hass.services.async_services_for_domain("esphome")), [show.name, show.node], True,
            WAKE_VERDICT) if show else None)
        if not action:
            LOGGER.warning("wake race: no way to tell %s whether it answers; it opens its run by itself", h.name)
            return
        self.hass.async_create_task(self._esphome(action, {"wake": h.wake, "won": won}), eager_start=True)

    def _tidy(self) -> bool:
        """Keeps the genbu device of each Show's music select in step with the Show: its name follows a
        rename, and once the Show's own device is gone from Home Assistant, it goes too, with what was
        picked for it. True when a pick was forgotten."""
        devices = dr.async_get(self.hass)
        names = {s.device: s.name for s in self.shows}
        forgot = False
        for device in dr.async_entries_for_config_entry(devices, self.entry.entry_id):
            show = next((i for d, i in device.identifiers if d == DOMAIN), None)
            if show is None or show == self.entry.entry_id:  # the house's own device
                continue
            if devices.async_get(show) is None:
                devices.async_remove_device(device.id)
                forgot = forgot or bool(self.house.music_choice(show))
                self.house.choose_music(show, "")
                self.house.data["switches"].pop(show, None)
            elif show in names and device.name != names[show]:
                devices.async_update_device(device.id, name=names[show])
        return forgot

    def _save(self) -> None:
        self.store.async_delay_save(lambda: self.house.data, 5)


def _slug(name: str) -> str:
    """The node name echod gives itself from its display name (layout.Slug)."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

