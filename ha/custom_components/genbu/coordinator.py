"""GenbuHouse: looks at the Genbu Shows every minute and whenever one of them changes, feeds that to
house.House, and does what it answers: Repairs issues, the push, the pick after 7 days. It is the only
thing that ever changes which Shows are main update Shows, so nothing can race for the job."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (CHANGED, CONF_MAIN, CONF_NOTIFY, DOMAIN, ISSUE_MISSING, ISSUE_NO_MAIN, ISSUE_PICKED,
                    MAIN_SELECT, MANUFACTURERS, PUSH, STORE_KEY, STORE_VERSION)
from .house import ASK, PICK, House, Show, recommend

LOGGER = logging.getLogger(__name__)
LOOK_EVERY = timedelta(minutes=1)


def find_shows(hass: HomeAssistant) -> list[Show]:
    """Every Genbu Show the ESPHome integration knows, with the two entities the house reads."""
    devices, entities = dr.async_get(hass), er.async_get(hass)
    shows: dict[str, Show] = {}
    for entry in hass.config_entries.async_entries("esphome"):
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
            if device.manufacturer not in MANUFACTURERS or device.via_device_id:
                continue
            node = entry.data.get("device_name") or _slug(device.name or "")
            firmware = committed = None
            for e in er.async_entries_for_device(entities, device.id):
                if e.domain == "update" and (e.unique_id.endswith("-firmware") or e.entity_id.endswith("_firmware")):
                    firmware = e.entity_id
                elif e.domain == "sensor" and (e.unique_id.endswith("-committed_version")
                                               or e.entity_id.endswith("_committed_version")):
                    committed = e.entity_id
            if not node or not firmware:
                continue
            up = hass.states.get(firmware)
            done = hass.states.get(committed) if committed else None
            shows[node] = Show(
                node=node,
                name=device.name_by_user or device.name or node,
                installed=(up.attributes.get("installed_version") or "") if up else "",
                committed=done.state if done else "",
                online=bool(up) and up.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
                watch=(firmware, committed),
                device=device.id,
            )
    return sorted(shows.values(), key=lambda s: s.node)


class GenbuHouse:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.store: Store[dict] = Store(hass, STORE_VERSION, STORE_KEY)
        self.house = House()
        self.shows: list[Show] = []
        self._watched: set[str] = set()
        self._stops: list = []
        self._debounce = Debouncer(hass, LOGGER, cooldown=2, immediate=True, function=self.async_refresh)

    async def async_start(self) -> None:
        self.house = House(await self.store.async_load() or {})
        if not self.house.data.get("seeded"):
            # The installer or the setup form named the first main update Show.
            if main := self.entry.data.get(CONF_MAIN):
                self.house.make_main(main, dt_util.utcnow())
            self.house.data["seeded"] = True
            self._save()
        self._stops.append(async_track_time_interval(self.hass, self._tick, LOOK_EVERY))
        self._stops.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._changed,
                                                      event_filter=self._watching))
        self._stops.append(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._changed))
        await self.async_refresh()

    @callback
    def async_stop(self) -> None:
        for stop in self._stops:
            stop()
        self._stops.clear()
        self._debounce.async_cancel()

    @callback
    def _watching(self, data) -> bool:
        return data["entity_id"] in self._watched

    @callback
    def _changed(self, _event: Event) -> None:
        self.hass.async_create_task(self._debounce.async_call())

    async def _tick(self, _now: datetime) -> None:
        await self.async_refresh()

    def name(self, node: str) -> str:
        return next((s.name for s in self.shows if s.node == node), node)

    def recommended(self, leaving: str | None = None) -> str | None:
        return recommend(self.shows, self.house.version, leaving)

    def missing_words(self, node: str) -> dict[str, str]:
        """What the issue, its fix flow and the push say about a missing main update Show."""
        pick = self.recommended(leaving=node)
        return {"name": self.name(node), "since": _local(self.house.gone_since(node)),
                "pick": self.name(pick) if pick else "-", "pick_at": _local(self.house.pick_at(node))}

    # What the select, the options and the fix flow do.

    async def async_make_main(self, node: str) -> None:
        self.house.make_main(node, dt_util.utcnow())
        await self._changed_by_hand()

    async def async_set_extra(self, nodes: list[str]) -> None:
        self.house.set_extra(nodes, dt_util.utcnow())
        await self._changed_by_hand()

    async def async_replace(self, gone: str, new: str | None) -> None:
        self.house.replace(gone, new, dt_util.utcnow())
        await self._changed_by_hand()

    async def async_wait_more(self, node: str) -> None:
        self.house.wait_more(node, dt_util.utcnow())
        await self._changed_by_hand()

    async def _changed_by_hand(self) -> None:
        for node in self.house.mains:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_PICKED + node)
        self._save()
        await self.async_refresh()

    async def async_refresh(self) -> None:
        now = dt_util.utcnow()
        self.shows = find_shows(self.hass)
        self._watched = {e for s in self.shows for e in s.watch if e}
        changed = self.house.see(self.shows, now)
        for node, step in self.house.missing(now).items():
            if step == PICK:
                new = self.house.auto_pick(node, self.shows, now)
                if new is not None:
                    changed = True
                    self._picked(node, new)
        self._issues(now)
        await self._push(now)
        if changed:
            self._save()
        async_dispatcher_send(self.hass, CHANGED)

    def _save(self) -> None:
        self.store.async_delay_save(lambda: self.house.data, 5)

    def _picked(self, gone: str, new: str) -> None:
        LOGGER.warning("Main update Show %s was gone 7 days; %s", gone,
                       f"{new} took its place" if new else "the other main update Show carries on")
        ir.async_create_issue(
            self.hass, DOMAIN, ISSUE_PICKED + (new or gone), is_fixable=True, is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="main_picked" if new else "main_dropped",
            translation_placeholders={"gone": self.name(gone), "new": self.name(new) if new else "",
                                      "select": MAIN_SELECT},
            data={"kind": "picked"},
        )

    def _issues(self, now: datetime) -> None:
        want: set[str] = set()
        for node, step in self.house.missing(now).items():
            if step != ASK:
                continue
            issue = ISSUE_MISSING + node
            want.add(issue)
            ir.async_create_issue(
                self.hass, DOMAIN, issue, is_fixable=True, severity=ir.IssueSeverity.WARNING,
                translation_key="main_missing", translation_placeholders=self.missing_words(node),
                data={"node": node},
            )
        if not self.house.mains and self.shows:
            want.add(ISSUE_NO_MAIN)
            ir.async_create_issue(self.hass, DOMAIN, ISSUE_NO_MAIN, is_fixable=True,
                                  severity=ir.IssueSeverity.WARNING, translation_key="no_main",
                                  translation_placeholders={"select": MAIN_SELECT}, data={"node": ""})
        for domain, issue in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue not in want and not issue.startswith(ISSUE_PICKED):
                ir.async_delete_issue(self.hass, DOMAIN, issue)

    async def _push(self, now: datetime) -> None:
        service = (self.entry.options.get(CONF_NOTIFY) or "").removeprefix("notify.")
        if not service:
            return
        for node, step in self.house.missing(now).items():
            if step != ASK or not self.house.tell_once(node):
                continue
            self._save()
            title, text = PUSH.get(self.hass.config.language[:2], PUSH["en"])
            text = text.format(**self.missing_words(node))
            try:
                await self.hass.services.async_call("notify", service, {
                    "title": title, "message": text,
                    "data": {"url": "/config/repairs", "clickAction": "/config/repairs"},
                }, blocking=True)
            except Exception as err:  # a phone that is gone must not stop the house
                LOGGER.warning("Could not push to notify.%s: %s", service, err)


def _slug(name: str) -> str:
    """The node name echod gives itself from its display name (layout.Slug)."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _local(t: datetime | None) -> str:
    return dt_util.as_local(t).strftime("%d.%m. %H:%M") if t else "-"
