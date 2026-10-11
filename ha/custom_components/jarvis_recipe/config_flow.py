"""Sets Jarvis Rezepte up: it only needs to know where SearXNG is."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigFlow

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from .const import CONF_SEARXNG, DOMAIN


class RecipeFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input.get(CONF_SEARXNG, "")).strip().rstrip("/")
            parts = urlsplit(url)
            if parts.scheme in ("http", "https") and parts.hostname:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Jarvis Rezepte", data={CONF_SEARXNG: url})
            errors[CONF_SEARXNG] = "bad_url"
        return self.async_show_form(step_id="user", errors=errors,
                                    data_schema=vol.Schema({vol.Required(CONF_SEARXNG): str}))
