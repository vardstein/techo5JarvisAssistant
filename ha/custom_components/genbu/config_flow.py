"""Sets Genbu up. The form asks nothing: the house starts where its newest Show is (GENBU-119). The
installer sends the same empty form over REST. The options set the recipe search, a SearXNG address the
voice assistant's genbu__recipe looks recipes up with, and the agent Genbu's own conversation agent hands on
to (conversation.py, GENBU-253)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from .const import CONF_AGENT, CONF_SEARXNG, DOMAIN, NAME
from .conversation import is_genbu
from .coordinator import find_shows


def _search(answer: dict[str, Any]) -> str | None:
    """The SearXNG address as given, without a slash at the end; "" for none, None for one that is no
    http or https address."""
    url = str(answer.get(CONF_SEARXNG) or "").strip().rstrip("/")
    if not url:
        return ""
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.hostname else None


class GenbuFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}),
                                    description_placeholders={"count": str(len(find_shows(self.hass)))})

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return GenbuOptions()


class GenbuOptions(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            search = _search(user_input)
            agent = str(user_input.get(CONF_AGENT) or "")
            if search is None:
                errors[CONF_SEARXNG] = "searxng"
            if agent and is_genbu(self.hass, agent):
                errors[CONF_AGENT] = "own_agent"
            if not errors:
                options = {**self.config_entry.options, CONF_SEARXNG: search}
                options.pop(CONF_AGENT, None)
                return self.async_create_entry(data={**options, CONF_AGENT: agent} if agent else options)
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_SEARXNG, description={"suggested_value": options.get(CONF_SEARXNG, "")}): str,
            # Left empty, Genbu has no conversation agent of its own.
            vol.Optional(CONF_AGENT, description={"suggested_value": options.get(CONF_AGENT)}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="conversation")),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
