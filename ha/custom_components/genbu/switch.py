"""For each Show, "Recipes" and "Updates by voice": whether the voice assistant gets the recipe search and
the firmware tool when it is asked at that Show (llm.py). Recipes is there only while a recipe search is set
up. They sit on the Show's small genbu device (coordinator.show_device), and Genbu keeps them, not the
recorder, so llm.py reads them without a state lookup.

And switch.genbu_install_at_night on the Genbu Assistant device: when on, an update waiting on the house's
card installs by itself at 03:00, and the Shows follow while nobody uses them. Off until somebody turns it
on.

And switch.genbu_llm_assistant next to it, "Assistant uses an LLM": whether the house's assistant is a
language model. The prompt lines only a language model can use (the LLM extras, GENBU-240) go out only while
it is on; Home Assistant's own agent never reads them, so it changes nothing there. Off until somebody says
yes, in the installer or here."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CHANGED, CONF_SEARXNG, LLM_SWITCH, NIGHT_SWITCH, RECIPES, VOICE_UPDATES
from .coordinator import house_device, show_device
from .house import Show

ICONS = {RECIPES: "mdi:chef-hat", VOICE_UPDATES: "mdi:account-voice"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    hub = entry.runtime_data
    add([InstallAtNight(entry), UsesLlm(entry)])
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


class HouseSwitch(SwitchEntity):
    """A switch for the whole house, on the Genbu Assistant device."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    key = ""

    def __init__(self, entry: ConfigEntry, entity_id: str) -> None:
        self.entity_id = entity_id
        self._attr_translation_key = self.key
        self._attr_unique_id = f"{entry.entry_id}_{self.key}"
        self._attr_device_info = house_device(entry)
        self.hub = entry.runtime_data

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        self._attr_is_on = self._read()
        self.async_write_ha_state()

    def _read(self) -> bool:
        raise NotImplementedError

    async def _set(self, on: bool) -> None:
        raise NotImplementedError

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class InstallAtNight(HouseSwitch):
    key = "install_at_night"
    _attr_icon = "mdi:weather-night"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, NIGHT_SWITCH)

    def _read(self) -> bool:
        return self.hub.house.night

    async def _set(self, on: bool) -> None:
        await self.hub.async_set_night(on, await self.hub.async_who(self._context))


class UsesLlm(HouseSwitch):
    key = "llm_assistant"
    _attr_icon = "mdi:robot-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, LLM_SWITCH)

    def _read(self) -> bool:
        return self.hub.house.llm

    async def _set(self, on: bool) -> None:
        await self.hub.async_set_llm(on)
