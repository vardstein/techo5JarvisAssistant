"""What Genbu Assistant gives the voice assistant: its own tools in Home Assistant's Assist set, so every
agent that can control the house finds them, with nothing to tick (Home Assistant 2026.10 and later).

genbu__recipe finds a recipe on the web and puts it on the Show's screen. It is offered only when all of
these hold, asked anew for every request: a recipe search is set up in Genbu Assistant's options, the
request comes from a Genbu Show, and that Show has Recipes on. A phone, a Voice PE or the chat in the app
never see it. See docs/genbu-assistant.md §4."""

from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.ssl import get_default_context

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from . import recipe as rec
from .const import DOMAIN, NEW_RECIPE, RECIPES, SHOW_RECIPE
from .house import Show

_LOGGER = logging.getLogger(__name__)

AGENT = "Mozilla/5.0 (compatible; GenbuAssistant/1.4; +https://www.home-assistant.io)"
TIMEOUT = aiohttp.ClientTimeout(total=8)  # per page, and for the search
PROMPT = {
    "de": ("Will jemand ein Rezept oder wissen, wie man etwas kocht oder backt, nutze genbu__recipe. Das Rezept "
           "steht dann auf dem Bildschirm: sag nur kurz, welches Gericht von welcher Seite es ist. Zutaten und "
           "Schritte nur, wenn jemand sie hören will."),
    "en": ("When someone wants a recipe or asks how to cook or bake something, use genbu__recipe. The recipe "
           "then is on the screen: just say which dish it is and from which site. Ingredients and steps only "
           "when someone wants to hear them."),
}


def _lang(llm_context: llm.LLMContext) -> str:
    return "de" if (llm_context.language or "").lower().startswith("de") else "en"


def _hub(hass: HomeAssistant):
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED),
                 None)
    return entry.runtime_data if entry else None


@callback
def async_get_tools(hass: HomeAssistant, llm_context: llm.LLMContext, api_id: str) -> LLMTools | None:
    if api_id != llm.LLM_API_ASSIST or (hub := _hub(hass)) is None:
        return None
    show = hub.show(llm_context.device_id)
    tools: list[llm.Tool] = []
    prompt = []
    if hub.searxng and show is not None and hub.switched_on(show, RECIPES):
        tools.append(RecipeTool(show))
        prompt.append(PROMPT[_lang(llm_context)])
    if not tools:
        return None
    return LLMTools(tools=tools, prompt=" ".join(prompt))


class PublicResolver(AbstractResolver):
    """Looks a name up and drops every address that is not on the internet, so the address checked is the
    one connected to: a name can't answer with a public address first and the router's next. A name left
    with none fails like a name that doesn't exist."""

    def __init__(self) -> None:
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(self, host: str, port: int = 0,
                      family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        found = [r for r in await self._inner.resolve(host, port, family) if rec.all_public([r["host"]])]
        if not found:
            raise OSError(f"{host} has no public address")
        return found

    async def close(self) -> None:
        await self._inner.close()


def _web_session() -> aiohttp.ClientSession:
    """A session of its own for recipe pages: only public addresses, no proxy from the environment, no
    cookies kept from one page to the next."""
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), ssl=get_default_context(), limit=2)
    return aiohttp.ClientSession(connector=connector, timeout=TIMEOUT, trust_env=False,
                                 cookie_jar=aiohttp.DummyCookieJar(),
                                 headers={"User-Agent": AGENT, "Accept": "text/html"})


async def _fetch(session: aiohttp.ClientSession, url: str) -> str | None:
    """The page at url as text, following up to three redirects, each checked like the first. None for
    anything that isn't an HTML page on the internet."""
    for _ in range(4):
        if not rec.web_address(url):
            return None
        try:
            async with session.get(url, allow_redirects=False) as resp:
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
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, LookupError) as exc:
            _LOGGER.debug("%s: %s", urlsplit(url).hostname, exc)
            return None
    return None


class RecipeTool(llm.Tool):
    name = f"{DOMAIN}__recipe"
    title = "Recipe on the Show"
    description = ("Finds a recipe on the web and puts it on the screen of the Genbu Show you are talking to. "
                   "Use it when someone wants a recipe or wants to know how to cook or bake a dish. Set read_out "
                   "only when they asked to hear the ingredients or the steps.")
    parameters = vol.Schema({
        vol.Required("dish", description="The dish, e.g. beef tacos or Pfannkuchen"): str,
        vol.Optional("read_out", description="True only when the ingredients or steps should be read out"): bool,
    })
    integration = DOMAIN
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=True, open_world=True)

    def __init__(self, show: Show) -> None:
        self.show = show

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        hub = _hub(hass)
        dish = str(tool_input.tool_args.get("dish") or "").strip()[:120]
        if hub is None or not hub.searxng:
            return llm.ToolResult(data={"error": "No recipe search is set up"}, error=True)
        if not dish:
            return llm.ToolResult(data={"error": "Which dish?"}, error=True)
        device = self.show.device
        if device in hub.cooking:  # one lookup at a time per Show
            return llm.ToolResult(data={"error": "Still looking up the last recipe"}, error=True)
        hub.cooking.add(device)
        try:
            search, lang = async_get_clientsession(hass), _lang(llm_context)
            async with _web_session() as web:
                found, tried = await rec.look_up(dish, lambda q: _search(search, hub.searxng, q, lang),
                                                 lambda u: _fetch(web, u), lang)
        finally:
            hub.cooking.discard(device)
        if found is None:
            return llm.ToolResult(data={"error": f"No recipe found for {dish}", "sites_tried": tried}, error=True)
        async_dispatcher_send(hass, NEW_RECIPE, found)
        answer = rec.spoken(found, bool(tool_input.tool_args.get("read_out")))
        answer["on_screen"] = await _show(hass, self.show)
        return llm.ToolResult(data=answer)


async def _search(session: aiohttp.ClientSession, searxng: str, query: str, lang: str) -> list[str]:
    """The result addresses SearXNG finds. It is the house's own search, so the shared session is fine."""
    try:
        async with session.get(f"{searxng}/search", timeout=TIMEOUT,
                               params={"q": query, "format": "json", "language": lang}) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        _LOGGER.warning("SearXNG did not answer: %s", exc)
        return []
    return [str(r.get("url")) for r in data.get("results") or [] if isinstance(r, dict) and r.get("url")]


async def _show(hass: HomeAssistant, show: Show) -> bool:
    """Puts the recipe page up on the Show. False when its firmware has no recipe page."""
    action = rec.show_action(list(hass.services.async_services_for_domain("esphome")), [show.name, show.node],
                             True, SHOW_RECIPE)
    if action is None:
        return False
    try:
        await hass.services.async_call("esphome", action, {}, blocking=True)
    except Exception as exc:  # noqa: BLE001 - the recipe is found either way; the screen is a bonus
        _LOGGER.warning("esphome.%s: %s", action, exc)
        return False
    return True
