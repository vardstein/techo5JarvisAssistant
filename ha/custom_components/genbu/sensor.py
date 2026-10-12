"""sensor.genbu_house_version: the version the house runs, the one every following Show moves to. The
Shows read it and two of its attributes over their ESPHome link: main_shows (am I a main update Show?)
and main_missing_since (for the line on their update page).

music_players is where music asked for at each Show plays, by the Show's device id and by its
assist_satellite, for the music automation and scripts the installer makes.

And for each Show, "Music falls back to": where its music goes when the player it plays on now is
offline. On the Show's genbu device, next to its "Music plays on" select.

The copies of the Show's text sensors (next alarm, radio, last heard) sit there too, and which firmware
it runs. See entity.py."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, HOUSE_ENTITY
from .controls import Control
from .coordinator import show_device
from .entity import Copy, add_copies
from .house import Show


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    hub = entry.runtime_data
    add([HouseVersion(entry)])
    made: set[str] = set()

    @callback
    def new_shows() -> None:
        # A Show that turns up later gets its own sensor on the next look.
        fresh = [s for s in hub.shows if s.device and s.device not in made]
        made.update(s.device for s in fresh)
        if fresh:
            add([MusicFallsBackTo(entry, s) for s in fresh])

    new_shows()
    entry.async_on_unload(async_dispatcher_connect(hass, CHANGED, new_shows))

    def make(entry: ConfigEntry, show: Show, control: Control) -> Copy:
        return Firmware(entry, show, control) if control.special == "firmware" else SensorCopy(entry, show, control)
    add_copies(hass, entry, add, "sensor", make)


class HouseVersion(SensorEntity):
    _attr_icon = "mdi:home-import-outline"
    _attr_name = "Genbu Show version"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"shows", "music_players"})

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
            "music_players": {key: player for s in self.hub.shows
                              if (player := self.hub.music.get(s.device))
                              for key in (s.device, s.satellite) if key},
        }
        self.async_write_ha_state()


class MusicFallsBackTo(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "music_falls_back_to"
    _attr_icon = "mdi:speaker-multiple"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, show: Show) -> None:
        self.hub = entry.runtime_data
        self.device = show.device
        self._attr_unique_id = f"{entry.entry_id}_music_fallback_{show.device}"
        self._attr_device_info = show_device(show)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        self._attr_available = any(s.device == self.device for s in self.hub.shows)
        player = self.hub.fallback.get(self.device, "")
        names = {p.entity: p.name for p in self.hub.players}
        self._attr_native_value = names.get(player) or None
        self._attr_extra_state_attributes = {"player": player}
        self.async_write_ha_state()


class SensorCopy(Copy, SensorEntity):
    platform_domain = "sensor"

    def copy(self, state: State) -> None:
        self._attr_native_value = None if state.state == STATE_UNKNOWN else state.state


class Firmware(Copy, SensorEntity):
    """The version the Show runs. The update itself stays on the ESPHome device, so it is offered once."""
    platform_domain = "sensor"
    _attr_icon = "mdi:package-variant-closed"

    def copy(self, state: State) -> None:
        a = state.attributes
        self._attr_native_value = a.get("installed_version")
        self._attr_extra_state_attributes = {"latest_version": a.get("latest_version"),
                                             "release_url": a.get("release_url")}
