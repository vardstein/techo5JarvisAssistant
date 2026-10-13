"""sensor.genbu_house_version: the version the house runs, the one every Show moves to. The Shows read it
and three of its attributes over their ESPHome link. main_shows is always empty since 1.4.0 (GENBU-119):
every Show follows, none decides; it is left out while the house has no version yet, so the Shows stay
as they are. main_missing_since is always empty. update_waiting is the house update the card offers and
nobody skipped, which a Show asks about out loud.

music_players is where music asked for at each Show plays, by the Show's device id and by its
assist_satellite, for the music automation and scripts the installer makes.

And for each Show, "Music falls back to": where its music goes when the player it plays on now is
offline. On the Show's genbu device, next to its "Music plays on" select.

sensor.genbu_recipe, while a recipe search is set up: the recipe the assistant last found. Its state is the
dish, its attributes the recipe, which jarvis-recipe-card.js draws. It comes back after a restart, and the
recipe stays out of the recorder's history (it is big, and the last one is all anyone needs)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CHANGED, CONF_SEARXNG, HOUSE_ENTITY, NEW_RECIPE, RECIPE_ENTITY
from .coordinator import show_device
from .house import Show


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    hub = entry.runtime_data
    add([HouseVersion(entry)])
    if entry.options.get(CONF_SEARXNG):
        add([RecipeSensor(entry)])
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
        # No version yet (no Show has committed one): the Shows stay where they are.
        self._attr_native_value = house.version or None
        mains = {"main_shows": []} if house.version else {}
        self._attr_extra_state_attributes = {
            **mains,
            "main_missing_since": "",
            "update_waiting": self.hub.waiting,
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



RECIPE_FIELDS = ("image", "servings", "minutes", "ingredients", "steps", "source", "url")


class RecipeSensor(SensorEntity, RestoreEntity):
    _attr_icon = "mdi:chef-hat"
    _attr_name = "Genbu recipe"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset(RECIPE_FIELDS)

    def __init__(self, entry: ConfigEntry) -> None:
        self.entity_id = RECIPE_ENTITY
        self._attr_unique_id = f"{entry.entry_id}_recipe"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state not in ("unknown", "unavailable"):
            self._attr_native_value = last.state
            self._attr_extra_state_attributes = {k: last.attributes.get(k) for k in RECIPE_FIELDS}
        self.async_on_remove(async_dispatcher_connect(self.hass, NEW_RECIPE, self._new))

    @callback
    def _new(self, recipe: dict) -> None:
        self._attr_native_value = recipe["title"][:255]
        self._attr_extra_state_attributes = {k: recipe.get(k) for k in RECIPE_FIELDS}
        self.async_write_ha_state()
