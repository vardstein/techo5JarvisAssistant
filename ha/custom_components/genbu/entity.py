"""The copies of a Show's controls on its genbu device (controls.py). A copy follows its ESPHome original's
state and passes on what is done to it, the way Home Assistant's own "Change device type of a switch"
helper does, and is unavailable while the original is. Assist keeps talking to the originals: a copy is
kept from Assist when it is first made, so a Show's screen isn't there twice. Exposing one by hand
afterwards sticks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.homeassistant.exposed_entities import KNOWN_ASSISTANTS, async_expose_entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.event import async_track_state_change_event

from .const import CHANGED
from .controls import Control, object_id
from .coordinator import show_device
from .house import Show


def add_copies(hass: HomeAssistant, entry: ConfigEntry, add, platform: str,
               make: Callable[[ConfigEntry, Show, Control], Entity]) -> None:
    """Adds the platform's copies for each Show, and for a Show or a control that turns up later."""
    hub = entry.runtime_data
    made: set[tuple[str, str]] = set()

    @callback
    def look() -> None:
        fresh = []
        for show in hub.shows:
            for control, _ in hub.controls.get(show.device, {}).values():
                if control.platform == platform and (show.device, control.key) not in made:
                    made.add((show.device, control.key))
                    fresh.append(make(entry, show, control))
        if fresh:
            add(fresh)

    look()
    entry.async_on_unload(async_dispatcher_connect(hass, CHANGED, look))


class Copy(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    platform_domain = ""  # light, switch, ...: set by each kind of copy

    def __init__(self, entry: ConfigEntry, show: Show, control: Control) -> None:
        self.hub = entry.runtime_data
        self.device = show.device
        self.control = control
        self.entity_id = f"{self.platform_domain}.{object_id(show.node, control)}"
        self._attr_unique_id = f"{entry.entry_id}_copy_{show.device}_{control.key}"
        self._attr_device_info = show_device(show)
        self._attr_entity_category = EntityCategory(control.category) if control.category else None
        if control.special:
            self._attr_translation_key = control.special
        else:
            self._attr_name = control.name
        self.source: str | None = None
        self._unfollow: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        self._keep_from_assist()
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self.async_on_remove(self._stop_following)
        self._changed()

    @callback
    def _keep_from_assist(self) -> None:
        e = er.async_get(self.hass).async_get(self.entity_id)
        if e is None:
            return
        for assistant in KNOWN_ASSISTANTS:
            if "should_expose" not in e.options.get(assistant, {}):
                async_expose_entity(self.hass, assistant, self.entity_id, False)

    @callback
    def _changed(self) -> None:
        # The original can be renamed or come and go; the house looks it up again on each look.
        source = self.hub.source(self.device, self.control.key)
        if source != self.source:
            self._stop_following()
            self.source = source
            if source:
                self._unfollow = async_track_state_change_event(self.hass, source, self._followed)
        self._follow()

    @callback
    def _stop_following(self) -> None:
        if self._unfollow:
            self._unfollow()
            self._unfollow = None

    @callback
    def _followed(self, _event: Event) -> None:
        self._follow()

    @callback
    def _follow(self) -> None:
        state = self.hass.states.get(self.source) if self.source else None
        self._attr_available = state is not None and state.state != STATE_UNAVAILABLE
        if self._attr_available:
            if not self.control.special:
                self._attr_icon = state.attributes.get("icon")
            self.copy(state)
        self.async_write_ha_state()

    def copy(self, state: State) -> None:
        """Takes what the copy shows from the original's state."""
        raise NotImplementedError

    async def tell(self, domain: str, service: str, **data: Any) -> None:
        """Does it on the original, as whoever did it on the copy."""
        await self.hass.services.async_call(domain, service, {ATTR_ENTITY_ID: self.source, **data},
                                            blocking=True, context=self._context)
