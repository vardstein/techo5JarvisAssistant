"""select.genbu_main_update_show: which Show is the house's main update Show. Picking another one here
is how the main moves by hand; a second main is added in the integration's options.

And for each Show, "Music plays on": where music asked for at that Show plays. Automatic is the room's
speaker, else the Show itself, which is also where music goes while a speaker is offline (music.py); its
label says which and why. It
sits on the Show's small genbu device (coordinator.show_device)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, MAIN_SELECT
from .coordinator import show_device
from .house import Show
from .music import AUTO

# Automatic says what it does: the speaker in the Show's room, else the Show itself, and why. Where the
# music goes after that is the "Music falls back to" sensor's to say.
AUTOMATIC = {"de": "Automatisch", "en": "Automatic"}
WHY = {
    "de": {"room": "Lautsprecher im Raum {area} ({room})", "none": "kein Lautsprecher im Raum {area} ({own})",
           "unset": "kein Raum gesetzt ({own})", "offline": "{room} ist offline ({own})"},
    "en": {"room": "speaker in {area} ({room})", "none": "no speaker in {area} ({own})",
           "unset": "no room set ({own})", "offline": "{room} is offline ({own})"},
}
# A player that is offline says so in the list: picking it then plays on the next one in line.
GONE = {"de": "{name} (offline)", "en": "{name} (offline)"}


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
        word, why = AUTOMATIC.get(lang, AUTOMATIC["en"]), WHY.get(lang, WHY["en"])
        room, own = self.hub.automatic(show) if show else (None, None)
        where = ar.async_get(self.hass).async_get_area(show.area) if show and show.area else None
        area = where.name if where else ""
        if room and own and not room.available:
            text = why["offline"]
        elif room:
            text = why["room"]
        elif own:
            text = why["none"] if area else why["unset"]
        else:
            return word
        return f"{word}: " + text.format(area=area, room=room.name if room else "", own=own.name if own else "")

    @callback
    def _changed(self) -> None:
        show = next((s for s in self.hub.shows if s.device == self.device), None)
        self._attr_available = show is not None
        names = [p.name for p in self.hub.players]
        gone = GONE.get(self.hass.config.language[:2], GONE["en"])
        labels = {self._automatic(show): AUTO}
        for p in self.hub.players:
            label = p.name if names.count(p.name) == 1 else f"{p.name} ({p.entity})"
            labels.setdefault(label if p.available else gone.format(name=label), p.entity)
        self._labels = labels
        self._attr_options = list(labels)
        chosen = self.hub.house.music_choice(self.device)
        self._attr_current_option = next((o for o, p in labels.items() if p == chosen), self._attr_options[0])
        self._attr_extra_state_attributes = {"player": self.hub.music.get(self.device, "")}
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_choose_music(self.device, self._labels.get(option, AUTO))

