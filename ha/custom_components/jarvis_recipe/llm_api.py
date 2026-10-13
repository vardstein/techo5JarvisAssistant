"""The rezept_zeigen tool, offered to the voice assistant as the "Genbu Rezepte" API. Pick it next to
"Assist" in the conversation agent's options (docs/recipes.md)."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from . import recipe as rec
from .const import CONF_SEARXNG, DOMAIN, NEW_RECIPE, SHOW_ACTION

_LOGGER = logging.getLogger(__name__)

PROMPT = ("Wenn jemand ein Rezept will oder wissen will, wie man etwas kocht oder backt, rufe rezept_zeigen "
          "mit dem Gericht auf. Das Rezept steht dann auf dem Bildschirm: sag nur kurz, welches Gericht von "
          "welcher Seite es ist, und lies es nicht vor, außer du wirst darum gebeten.")
AGENT = "Mozilla/5.0 (compatible; GenbuRezepte/1.0; +https://www.home-assistant.io)"
TIMEOUT = aiohttp.ClientTimeout(total=8)
SPOKEN_STEPS = 12  # steps handed back to the model; the screen shows them all


def _result(data: dict, error: bool = False) -> Any:
    """A ToolResult where Home Assistant has it (2026.10 on), the plain answer where it doesn't."""
    if hasattr(llm, "ToolResult"):
        return llm.ToolResult(data=data, error=error)
    return data


class RecipeTool(llm.Tool):
    name = "rezept_zeigen"
    description = ("Sucht ein Rezept im Netz und zeigt es auf dem Bildschirm des Geräts, das gefragt wurde. "
                   "Gibt Zutaten und Schritte des echten Rezepts zurück.")
    parameters = vol.Schema({vol.Required("gericht", description="Das Gericht, z. B. Hackfleisch-Tacos"): str})
    integration = DOMAIN
    if hasattr(llm, "ToolAnnotations"):
        annotations = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=True, open_world=True)

    def __init__(self, entry: ConfigEntry) -> None:
        self._searxng = entry.data[CONF_SEARXNG]

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        dish = str(tool_input.tool_args.get("gericht") or "").strip()[:120]
        if not dish:
            return _result({"error": "Welches Gericht?"}, error=True)
        session = async_get_clientsession(hass)
        found, tried = await rec.look_up(dish, lambda q: self._search(session, q), lambda u: _fetch(hass, session, u))
        if found is None:
            return _result({"error": f"Kein Rezept für {dish} gefunden",
                            "versuchte_seiten": tried}, error=True)
        async_dispatcher_send(hass, NEW_RECIPE, found)
        shown = await _show(hass, llm_context.device_id)
        return _result({
            "gericht": found["title"],
            "quelle": found["source"],
            "portionen": found["servings"] or None,
            "minuten": found["minutes"],
            "zutaten": found["ingredients"],
            "schritte": found["steps"][:SPOKEN_STEPS],
            "auf_dem_bildschirm": shown,
        })

    async def _search(self, session: aiohttp.ClientSession, query: str) -> list[str]:
        try:
            async with session.get(f"{self._searxng}/search", timeout=TIMEOUT,
                                   params={"q": query, "format": "json", "language": "de"}) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            _LOGGER.warning("SearXNG did not answer: %s", exc)
            return []
        return [str(r.get("url")) for r in data.get("results") or [] if isinstance(r, dict) and r.get("url")]


async def _public(hass: HomeAssistant, url: str) -> bool:
    """Whether url's host is on the internet. aiohttp looks the name up again when it connects, so a
    name that changes its answer in between gets through; this keeps out the plain cases, a result
    that links to 192.168.x.x or localhost."""
    parts = urlsplit(url)
    try:
        infos = await hass.async_add_executor_job(socket.getaddrinfo, parts.hostname, parts.port or 443)
    except OSError:
        return False
    return rec.all_public([info[4][0] for info in infos])


async def _fetch(hass: HomeAssistant, session: aiohttp.ClientSession, url: str) -> str | None:
    """The page at url as text, following up to three redirects, each checked like the first. None for
    anything that isn't an HTML page on the internet."""
    for _ in range(4):
        if not rec.web_address(url) or not await _public(hass, url):
            return None
        try:
            async with session.get(url, allow_redirects=False, timeout=TIMEOUT,
                                   headers={"User-Agent": AGENT, "Accept": "text/html"}) as resp:
                if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    url = urljoin(url, resp.headers["Location"])
                    continue
                if resp.status != 200 or "html" not in resp.headers.get("Content-Type", ""):
                    return None
                body = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    body += chunk
                    if len(body) >= rec.MAX_PAGE:
                        break
                return bytes(body[:rec.MAX_PAGE]).decode(resp.charset or "utf-8", "replace")
        except (aiohttp.ClientError, asyncio.TimeoutError, LookupError) as exc:
            _LOGGER.debug("%s: %s", urlsplit(url).hostname, exc)
            return None
    return None


async def _show(hass: HomeAssistant, device_id: str | None) -> bool:
    """Puts the recipe page up on the Show the question came from. False when it isn't a Show, or the
    Show has no recipe page (no jarvis-rezept dashboard or no dashcast server)."""
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    names = [device.name, device.name_by_user] if device else []
    action = rec.show_action(list(hass.services.async_services_for_domain("esphome")), names,
                             device_id is not None, SHOW_ACTION)
    if action is None:
        return False
    try:
        await hass.services.async_call("esphome", action, {}, blocking=True)
    except Exception as exc:  # noqa: BLE001 - the recipe is found either way; the screen is a bonus
        _LOGGER.warning("esphome.%s: %s", action, exc)
        return False
    return True


class RecipeAPI(llm.API):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass=hass, id=DOMAIN, name="Genbu Rezepte")
        self._entry = entry

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        if _genbu_searches(self.hass):  # Genbu Assistant's genbu__recipe does it now
            return llm.APIInstance(api=self, api_prompt="", llm_context=llm_context, tools=[])
        return llm.APIInstance(api=self, api_prompt=PROMPT, llm_context=llm_context,
                               tools=[RecipeTool(self._entry)])


def _genbu_searches(hass: HomeAssistant) -> bool:
    """Whether Genbu Assistant (1.4.0 and later) has a recipe search of its own."""
    return any(e.options.get(CONF_SEARXNG) for e in hass.config_entries.async_entries("genbu"))
