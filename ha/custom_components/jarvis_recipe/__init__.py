"""Genbu Rezepte: a tool for the voice assistant that looks a recipe up on the web, keeps it in
sensor.jarvis_recipe and puts it on the Show that asked (the jarvis-rezept dashboard). See
docs/recipes.md.

Since Genbu Assistant 1.4.0 the recipe search is part of it (genbu__recipe), offered to every agent with
nothing to tick. This one stays for a release with a note in Repairs, and stands back as soon as Genbu
Assistant has a recipe search, so the assistant never gets two recipe tools."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir, llm

from .const import DOMAIN
from .llm_api import RecipeAPI

MOVED = "moved_to_genbu"

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.async_on_unload(llm.async_register_api(hass, RecipeAPI(hass, entry)))
    ir.async_create_issue(hass, DOMAIN, MOVED, is_fixable=False, is_persistent=False,
                          severity=ir.IssueSeverity.WARNING, translation_key=MOVED,
                          learn_more_url="https://github.com/vardstein/techo5JarvisAssistant/blob/stable/docs/recipes.md")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, MOVED)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
