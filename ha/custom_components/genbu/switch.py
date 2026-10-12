"""The copies of a Show's switches (night mode, microphone mute and its settings) on its genbu device.
See entity.py."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from .entity import Copy, add_copies


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add_copies(hass, entry, add, "switch", SwitchCopy)


class SwitchCopy(Copy, SwitchEntity):
    platform_domain = "switch"

    def copy(self, state: State) -> None:
        self._attr_is_on = None if state.state == STATE_UNKNOWN else state.state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.tell("switch", "turn_on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.tell("switch", "turn_off")
