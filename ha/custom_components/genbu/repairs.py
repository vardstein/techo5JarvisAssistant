"""The button on a Show's key issue (GENBU-196, keycare.py). Home Assistant lets only admins use it, which is
the point: a Show gets a new key, or a user, only when an admin asks for it. For a Show turned away for
coming from outside, the button has it try again once the way it comes in is fixed.

And the one on a deleted Show still waiting to be reset (GENBU-223, removal.py): it goes without the reset."""

from __future__ import annotations

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from . import keys
from .const import DOMAIN
from .keycare import KeyRefused
from .removal import LEAVING


class KeyFlow(RepairsFlow):
    def __init__(self, device: str) -> None:
        self.device = device

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}),
                                        description_placeholders=issue.translation_placeholders if issue else None)
        hub = next((e.runtime_data for e in self.hass.config_entries.async_entries(DOMAIN)
                    if e.state is ConfigEntryState.LOADED), None)
        show = hub.show(self.device) if hub else None
        if show is None:
            return self.async_abort(reason="no_show")
        try:
            if issue is not None and issue.translation_key == keys.HOME_ONLY:
                if await hub.keycare.async_retry(show) != keys.OK:
                    return self.async_abort(reason="still_away")
            else:
                await hub.keycare.async_renew(show)
        except KeyRefused as err:
            return self.async_abort(reason=err.reason)
        except HomeAssistantError:
            return self.async_abort(reason="no_answer")
        return self.async_create_entry(data={})


class ForgetFlow(RepairsFlow):
    def __init__(self, node: str) -> None:
        self.node = node

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}),
                                        description_placeholders=issue.translation_placeholders if issue else None)
        hub = next((e.runtime_data for e in self.hass.config_entries.async_entries(DOMAIN)
                    if e.state is ConfigEntryState.LOADED), None)
        if hub is None or not await hub.removal.async_forget_anyway(self.node):
            return self.async_abort(reason="no_show")
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict | None) -> RepairsFlow:
    if issue_id.startswith(LEAVING):
        return ForgetFlow(str((data or {}).get("node") or issue_id[len(LEAVING):]))
    return KeyFlow(str((data or {}).get("device") or ""))
