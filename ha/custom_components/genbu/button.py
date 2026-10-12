"""The copies of a Show's buttons (stop or snooze the alarm, calls, restart) on its genbu device. See
entity.py."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State

from .entity import Copy, add_copies


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add_copies(hass, entry, add, "button", ButtonCopy)


class ButtonCopy(Copy, ButtonEntity):
    platform_domain = "button"

    def copy(self, state: State) -> None:
        pass  # a button has nothing to show but whether it can be pressed

    async def async_press(self) -> None:
        await self.tell("button", "press")
