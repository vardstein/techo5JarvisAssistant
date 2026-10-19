"""Each Show's firmware update looks like a Genbu update in Home Assistant's Settings, not an ESPHome one
(GENBU-232). ESPHome still installs it; only the picture changes.

Home Assistant gives an update entity the icon of the integration that made it, and the ESPHome protocol
has no way to name another. A picture set through customize wins over that, so this puts the Genbu icon in
there for the Shows' firmware updates, the way a customize entry in configuration.yaml would, and takes it
out again when the integration stops. A picture someone set in customize themselves still wins."""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_PICTURE
from homeassistant.core import HomeAssistant, callback
from homeassistant.core_config import DATA_CUSTOMIZE
from homeassistant.helpers.entity_platform import async_get_platforms

ICON = "/api/brands/integration/genbu/icon.png"


class Dressed:
    """Home Assistant's customize, with the Genbu icon on the entities in it."""

    def __init__(self, inner) -> None:
        self.inner = inner  # the customize it wraps, None when there was none
        self.entities: frozenset[str] = frozenset()

    def get(self, entity_id: str) -> dict:
        got = self.inner.get(entity_id) if self.inner is not None else {}
        if entity_id in self.entities:
            return {ATTR_ENTITY_PICTURE: ICON, **got}
        return got


@callback
def dress(hass: HomeAssistant, entities: set[str]) -> None:
    """The Genbu icon on these entities, and off the ones no longer among them."""
    dressed = hass.data.get(DATA_CUSTOMIZE)
    before: frozenset[str] = frozenset()
    if isinstance(dressed, Dressed):
        before = dressed.entities
    else:
        # The first look, or customize was reloaded under it: every one is written again.
        dressed = hass.data[DATA_CUSTOMIZE] = Dressed(dressed)
    dressed.entities = frozenset(entities)
    _write(hass, before ^ dressed.entities if before else dressed.entities)


@callback
def undress(hass: HomeAssistant) -> None:
    """Customize as it was, and the ESPHome icon back."""
    dressed = hass.data.get(DATA_CUSTOMIZE)
    if not isinstance(dressed, Dressed):
        return
    if dressed.inner is None:
        hass.data.pop(DATA_CUSTOMIZE)
    else:
        hass.data[DATA_CUSTOMIZE] = dressed.inner
    _write(hass, dressed.entities)


def _write(hass: HomeAssistant, entities) -> None:
    """Has the update entities among these write their state again, which takes the picture in."""
    if not entities:
        return
    for platform in async_get_platforms(hass, "esphome"):
        if platform.domain != "update":
            continue
        for entity_id in entities:
            if (entity := platform.entities.get(entity_id)) is not None and entity.hass is not None:
                entity.async_write_ha_state()
