"""select.genbu_main_update_show: which Show is the house's main update Show. Picking another one here
is how the main moves by hand; a second main is added in the integration's options.

And for each Show, "Music plays on": where music asked for at that Show plays. Automatic is the room's
speaker, else the Show itself, which is also where music goes while a speaker is offline (music.py). It
sits on the Show's small genbu device (coordinator.show_device)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, MAIN_SELECT
from .coordinator import show_device
from .house import Show
from .music import AUTO

AUTOMATIC = {"de": "Automatisch", "en": "Automatic"}
# Automatic names the Show as the fallback, and says when the room's speaker is offline.
ELSE = {"de": "{room}, sonst {own}", "en": "{room}, else {own}"}
OFFLINE = {"de": "{own}, solange {room} offline ist", "en": "{own} while {room} is offline"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    hub = entry.runtime_data
    add([MainShow(entry)])
    made: set[str] = set()

    @callback
    def new_shows() -> None:
        # A Show that turns up later gets its own select on the next look.
        fresh = [s for s in hub.shows if s.device and s.device not in made]
        made.update(s.device for s in fresh)
        if fresh:
            add([MusicPlaysOn(entry, s) for s in fresh])

    new_shows()
    entry.async_on_unload(async_dispatcher_connect(hass, CHANGED, new_shows))


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


class MusicPlaysOn(SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "music_plays_on"
    _attr_icon = "mdi:speaker"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, show: Show) -> None:
        self.hub = entry.runtime_data
        self.device = show.device
        self._attr_unique_id = f"{entry.entry_id}_music_{show.device}"
        self._attr_device_info = show_device(show)
        self._attr_options = []
        self._labels: dict[str, str] = {}  # option -> player, AUTO for automatic

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    def _automatic(self, show) -> str:
        lang = self.hass.config.language[:2]
        word = AUTOMATIC.get(lang, AUTOMATIC["en"])
        room, own = self.hub.automatic(show) if show else (None, None)
        if room and own:
            text = (ELSE if room.available else OFFLINE).get(lang, ELSE["en"] if room.available else OFFLINE["en"])
            name = text.format(room=room.name, own=own.name)
        else:
            name = (room or own).name if room or own else ""
        return f"{word} ({name})" if name else word

    @callback
    def _changed(self) -> None:
        show = next((s for s in self.hub.shows if s.device == self.device), None)
        self._attr_available = show is not None
        names = [p.name for p in self.hub.players]
        labels = {self._automatic(show): AUTO}
        for p in self.hub.players:
            label = p.name if names.count(p.name) == 1 else f"{p.name} ({p.entity})"
            labels.setdefault(label, p.entity)
        self._labels = labels
        self._attr_options = list(labels)
        chosen = self.hub.house.music_choice(self.device)
        self._attr_current_option = next((o for o, p in labels.items() if p == chosen), self._attr_options[0])
        self._attr_extra_state_attributes = {"player": self.hub.music.get(self.device, "")}
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_choose_music(self.device, self._labels.get(option, AUTO))
