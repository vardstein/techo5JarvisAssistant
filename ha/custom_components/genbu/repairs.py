"""The fix flows behind Genbu's Repairs issues: pick a new main update Show, keep waiting 7 more days,
or say the missing one is gone for good. The note after an automatic pick only needs a confirm."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from .const import DOMAIN

RECOMMENDED = {"en": "recommended", "de": "empfohlen"}  # a form option label is not translated by HA


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None):
    if (data or {}).get("kind") == "picked":
        return ConfirmRepairFlow()
    return MainMissingFlow((data or {}).get("node", ""))


class MainMissingFlow(RepairsFlow):
    def __init__(self, node: str) -> None:
        self.node = node  # empty for "no main update Show yet"

    @property
    def hub(self):
        entry = self.hass.config_entries.async_entries(DOMAIN)[0]
        return entry.runtime_data

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if not self.node:
            return await self.async_step_pick()
        return self.async_show_menu(step_id="init", menu_options=["pick", "wait", "gone"],
                                    description_placeholders=self.hub.missing_words(self.node))

    async def async_step_pick(self, user_input: dict[str, Any] | None = None):
        hub = self.hub
        online = [s for s in hub.shows if s.online and s.node != self.node]
        if not online:
            return self.async_abort(reason="none_online")
        if user_input is not None:
            if self.node:
                await hub.async_replace(self.node, user_input["show"])
            else:
                await hub.async_make_main(user_input["show"])
            return self.async_create_entry(data={})
        best = hub.recommended(leaving=self.node)
        labels = {}
        for s in online:
            label = f"{s.name} · {s.committed or s.installed or '?'}"
            labels[s.node] = f"{label} ({RECOMMENDED.get(self.hass.config.language[:2], RECOMMENDED['en'])})" \
                if s.node == best else label
        return self.async_show_form(
            step_id="pick", data_schema=vol.Schema({vol.Required("show", default=best): vol.In(labels)}),
            description_placeholders={"name": hub.name(self.node) if self.node else "-",
                                      "house": hub.house.version or "-"})

    async def async_step_wait(self, user_input: dict[str, Any] | None = None):
        await self.hub.async_wait_more(self.node)
        return self.async_create_entry(data={})

    async def async_step_gone(self, user_input: dict[str, Any] | None = None):
        await self.hub.async_replace(self.node, None)
        return self.async_create_entry(data={})
