"""Genbu Rezepte: a tool for the voice assistant that looks a recipe up on the web, keeps it in
sensor.jarvis_recipe and puts it on the Show that asked (the jarvis-rezept dashboard). See
docs/recipes.md."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .llm_api import RecipeAPI

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.async_on_unload(llm.async_register_api(hass, RecipeAPI(hass, entry)))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
