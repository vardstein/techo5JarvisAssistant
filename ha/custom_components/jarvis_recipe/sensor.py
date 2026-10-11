"""sensor.jarvis_recipe: the recipe Jarvis last looked up. Its state is the dish, its attributes the
recipe, which jarvis-recipe-card.js draws. It comes back after a restart, and the recipe stays out of
the recorder's history (it is big, and the last one is all anyone needs)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .const import NEW_RECIPE

FIELDS = ("image", "servings", "minutes", "ingredients", "steps", "source", "url")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add([RecipeSensor(entry)])


class RecipeSensor(SensorEntity, RestoreEntity):
    _attr_icon = "mdi:chef-hat"
    _attr_name = "Jarvis Rezept"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset(FIELDS)

    def __init__(self, entry: ConfigEntry) -> None:
        self.entity_id = "sensor.jarvis_recipe"
        self._attr_unique_id = f"{entry.entry_id}_recipe"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state not in ("unknown", "unavailable"):
            self._attr_native_value = last.state
            self._attr_extra_state_attributes = {k: last.attributes.get(k) for k in FIELDS}
        self.async_on_remove(async_dispatcher_connect(self.hass, NEW_RECIPE, self._new))

    @callback
    def _new(self, recipe: dict) -> None:
        self._attr_native_value = recipe["title"][:255]
        self._attr_extra_state_attributes = {k: recipe.get(k) for k in FIELDS}
        self.async_write_ha_state()
