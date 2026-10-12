"""The copies of a Show's lights (screen, LED ring) on its genbu device. See entity.py."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (ATTR_BRIGHTNESS, ATTR_COLOR_MODE, ATTR_COLOR_TEMP_KELVIN,
                                            ATTR_EFFECT, ATTR_EFFECT_LIST, ATTR_HS_COLOR,
                                            ATTR_MAX_COLOR_TEMP_KELVIN, ATTR_MIN_COLOR_TEMP_KELVIN,
                                            ATTR_RGB_COLOR, ATTR_RGBW_COLOR, ATTR_RGBWW_COLOR,
                                            ATTR_SUPPORTED_COLOR_MODES, ATTR_XY_COLOR, ColorMode,
                                            LightEntity, LightEntityFeature)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .entity import Copy, add_copies

FEATURES = LightEntityFeature.EFFECT | LightEntityFeature.FLASH | LightEntityFeature.TRANSITION


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add_copies(hass, entry, add, "light", LightCopy)


def _tuple(value) -> tuple | None:
    return tuple(value) if value is not None else None


class LightCopy(Copy, LightEntity):
    platform_domain = "light"
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def copy(self, state: State) -> None:
        a = state.attributes
        modes = {ColorMode(m) for m in a.get(ATTR_SUPPORTED_COLOR_MODES) or [ColorMode.ONOFF]}
        self._attr_supported_color_modes = modes
        self._attr_supported_features = LightEntityFeature(a.get(ATTR_SUPPORTED_FEATURES, 0)) & FEATURES
        self._attr_is_on = None if state.state == STATE_UNKNOWN else state.state == STATE_ON
        mode = a.get(ATTR_COLOR_MODE)
        self._attr_color_mode = ColorMode(mode) if mode else (next(iter(modes)) if len(modes) == 1 else None)
        self._attr_brightness = a.get(ATTR_BRIGHTNESS)
        self._attr_effect_list = a.get(ATTR_EFFECT_LIST)
        self._attr_effect = a.get(ATTR_EFFECT)
        self._attr_hs_color = _tuple(a.get(ATTR_HS_COLOR))
        self._attr_rgb_color = _tuple(a.get(ATTR_RGB_COLOR))
        self._attr_rgbw_color = _tuple(a.get(ATTR_RGBW_COLOR))
        self._attr_rgbww_color = _tuple(a.get(ATTR_RGBWW_COLOR))
        self._attr_xy_color = _tuple(a.get(ATTR_XY_COLOR))
        self._attr_color_temp_kelvin = a.get(ATTR_COLOR_TEMP_KELVIN)
        self._attr_min_color_temp_kelvin = a.get(ATTR_MIN_COLOR_TEMP_KELVIN)
        self._attr_max_color_temp_kelvin = a.get(ATTR_MAX_COLOR_TEMP_KELVIN)

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Home Assistant hands over brightness, colour, effect and transition by their service names.
        await self.tell("light", "turn_on", **kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.tell("light", "turn_off", **kwargs)
