"""For each Show, "Recipes" and "Updates by voice": whether the voice assistant gets the recipe search and
the firmware tool when it is asked at that Show (llm.py). Recipes is there only while a recipe search is set
up. They sit on the Show's small genbu device (coordinator.show_device), and Genbu keeps them, not the
recorder, so llm.py reads them without a state lookup."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, CONF_SEARXNG, RECIPES, VOICE_UPDATES
from .coordinator import show_device
from .house import Show

ICONS = {RECIPES: "mdi:chef-hat", VOICE_UPDATES: "mdi:account-voice"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    hub = entry.runtime_data
    keys = [RECIPES, VOICE_UPDATES] if entry.options.get(CONF_SEARXNG) else [VOICE_UPDATES]
    made: set[str] = set()

    @callback
    def new_shows() -> None:
        # A Show that turns up later gets its switches on the next look.
        fresh = [s for s in hub.shows if s.device and s.device not in made]
        made.update(s.device for s in fresh)
        if fresh:
            add([ShowSwitch(entry, s, key) for s in fresh for key in keys])

    new_shows()
    entry.async_on_unload(async_dispatcher_connect(hass, CHANGED, new_shows))


class ShowSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, show: Show, key: str) -> None:
        self.hub = entry.runtime_data
        self.device, self.key = show.device, key
        self._attr_translation_key = key
        self._attr_icon = ICONS[key]
        self._attr_unique_id = f"{entry.entry_id}_{key}_{show.device}"
        self._attr_device_info = show_device(show)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        show = self.hub.show(self.device)
        self._attr_available = show is not None
        if show is not None:
            self._attr_is_on = self.hub.switched_on(show, self.key)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_switch(self.device, self.key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_switch(self.device, self.key, False)
