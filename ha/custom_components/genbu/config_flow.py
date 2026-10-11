"""Sets Genbu up. The form asks which Show is the main update Show; the installer fills the same form
over REST. The options add more main update Shows (after a warning) and a phone to tell."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from .const import CONF_EXTRA, CONF_MAIN, CONF_NOTIFY, DOMAIN
from .coordinator import find_shows


def _labels(shows) -> dict[str, str]:
    return {s.node: f"{s.name} · {s.committed or s.installed or '?'}" for s in shows}


class GenbuFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        shows = _labels(find_shows(self.hass))
        if user_input is not None:
            main = user_input.get(CONF_MAIN) or ""
            return self.async_create_entry(title="Genbu", data={CONF_MAIN: main})
        schema = vol.Schema({vol.Optional(CONF_MAIN): vol.In(shows)} if shows else {})
        return self.async_show_form(step_id="user", data_schema=schema,
                                    description_placeholders={"count": str(len(shows))})

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return GenbuOptions()


class GenbuOptions(OptionsFlow):
    def __init__(self) -> None:
        self.answer: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        hub = self.config_entry.runtime_data
        mains = hub.house.mains
        if user_input is not None:
            self.answer = user_input
            extra = user_input.get(CONF_EXTRA, [])
            if extra and set(extra) - set(mains[1:]):
                return await self.async_step_second()
            return await self._done()
        others = {k: v for k, v in _labels(hub.shows).items() if k not in mains[:1]}
        schema = vol.Schema({
            vol.Optional(CONF_EXTRA, default=[m for m in mains[1:] if m in others]): cv.multi_select(others),
            vol.Optional(CONF_NOTIFY, description={
                "suggested_value": self.config_entry.options.get(CONF_NOTIFY, "")}): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema, description_placeholders={
            "main": hub.name(mains[0]) if mains else "-"})

    async def async_step_second(self, user_input: dict[str, Any] | None = None):
        # "No" is first, so it is what a quick tap does.
        hub = self.config_entry.runtime_data
        mains = hub.house.mains
        return self.async_show_menu(step_id="second", menu_options=["second_no", "second_yes"],
                                    description_placeholders={"main": hub.name(mains[0]) if mains else "-"})

    async def async_step_second_no(self, user_input: dict[str, Any] | None = None):
        self.answer[CONF_EXTRA] = self.config_entry.runtime_data.house.mains[1:]
        return await self._done()

    async def async_step_second_yes(self, user_input: dict[str, Any] | None = None):
        return await self._done()

    async def _done(self):
        hub = self.config_entry.runtime_data
        await hub.async_set_extra(list(self.answer.get(CONF_EXTRA, [])))
        notify = str(self.answer.get(CONF_NOTIFY) or "").strip().removeprefix("notify.")
        return self.async_create_entry(data={CONF_NOTIFY: notify})
