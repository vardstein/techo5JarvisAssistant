"""The copies of a Show's numbers (its settings) on its genbu device, and its volume: the speaker stays
a media player on the ESPHome device, its volume is a slider here. See entity.py."""

from __future__ import annotations

from homeassistant.components.media_player import ATTR_MEDIA_VOLUME_LEVEL
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, PERCENTAGE
from homeassistant.core import HomeAssistant, State

from .controls import Control
from .entity import Copy, add_copies
from .house import Show


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    def make(entry: ConfigEntry, show: Show, control: Control) -> Copy:
        return Volume(entry, show, control) if control.special == "volume" else NumberCopy(entry, show, control)
    add_copies(hass, entry, add, "number", make)


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NumberCopy(Copy, NumberEntity):
    platform_domain = "number"

    def copy(self, state: State) -> None:
        a = state.attributes
        self._attr_native_min_value = _float(a.get("min")) or 0.0
        self._attr_native_max_value = _float(a.get("max")) or 100.0
        self._attr_native_step = _float(a.get("step")) or 1.0
        self._attr_mode = NumberMode(a.get("mode") or NumberMode.AUTO)
        self._attr_native_unit_of_measurement = a.get(ATTR_UNIT_OF_MEASUREMENT)
        self._attr_native_value = _float(state.state)

    async def async_set_native_value(self, value: float) -> None:
        await self.tell("number", "set_value", value=value)


class Volume(Copy, NumberEntity):
    platform_domain = "number"
    _attr_icon = "mdi:volume-high"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def copy(self, state: State) -> None:
        level = _float(state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL))
        self._attr_native_value = round(level * 100) if level is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.tell("media_player", "volume_set", **{ATTR_MEDIA_VOLUME_LEVEL: value / 100})
