"""update.genbu_show_firmware: the house's one update card (GENBU-119). Installed is the house version, the
one every Show follows; latest is what the release channel has. Install moves the house, and each Show
updates itself to it (the Shows read sensor.genbu_house_version). A version picked by hand goes back or
to another build the same way, and going back skips the release left behind. Skip works as on any card:
the Shows stop asking about that version.

It sits on the Genbu Assistant device, with the release channel and Install at night."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import CHANGED, UPDATE_ENTITY
from .coordinator import house_device
from .house import newer


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add) -> None:
    add([HouseUpdate(entry)])


class HouseUpdate(UpdateEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "show_firmware"
    _attr_should_poll = False
    _attr_supported_features = (UpdateEntityFeature.INSTALL | UpdateEntityFeature.SPECIFIC_VERSION
                                | UpdateEntityFeature.RELEASE_NOTES | UpdateEntityFeature.PROGRESS
                                | UpdateEntityFeature.BACKUP)

    def __init__(self, entry: ConfigEntry) -> None:
        self.entity_id = UPDATE_ENTITY
        self._attr_unique_id = f"{entry.entry_id}_show_firmware"
        self._attr_device_info = house_device(entry)
        self.hub = entry.runtime_data

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, CHANGED, self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        house, release = self.hub.house, self.hub.release
        self._attr_installed_version = house.version or None
        self._attr_latest_version = self.hub.offered or house.version or None
        self._attr_title = "Genbu Show"
        self._attr_release_summary = None
        if self.hub.offered:
            self._attr_release_summary = str(release.get("title") or "")[:255] or None
        self._attr_release_url = str(release.get("release_url") or "") or None
        self.async_write_ha_state()

    # Worked out on each write, not kept: Home Assistant sets in_progress back once async_install is done,
    # and the Shows only start then.
    @property
    def in_progress(self) -> bool:
        return self.hub.progress() is not None

    @property
    def update_percentage(self) -> int | None:
        return self.hub.progress()

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # The Shows' own order, so a dev build's run number counts as on the Show.
        return newer(latest_version, installed_version)

    async def async_update(self) -> None:
        # Settings → Updates → Check for updates (homeassistant.update_entity): read the channel now,
        # not on the next hourly read.
        await self.hub.async_read_release()

    async def async_release_notes(self) -> str | None:
        if not self.hub.offered:
            return None
        return str(self.hub.release.get("notes") or "") or None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        await self.hub.async_install_house(version, backup)
        if self.hub.offered:
            # Gone back past what the channel has: that one stays skipped, so neither the night install
            # nor the Shows bring it back. A newer release is offered again.
            self._changed()
            await self.async_skip()
            async_dispatcher_send(self.hass, CHANGED)  # the sensor's update_waiting
