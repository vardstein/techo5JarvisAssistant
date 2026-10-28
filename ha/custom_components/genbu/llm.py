"""What Genbu Assistant gives the voice assistant: its own tools in Home Assistant's Assist set, so every
agent that can control the house finds them, with nothing to tick (Home Assistant 2026.10 and later).

genbu__recipe finds a recipe on the web and puts it on the Show's screen. It is offered only when all of
these hold, asked anew for every request: a recipe search is set up in Genbu Assistant's options, the
request comes from a Genbu Show, and that Show has Recipes on. A phone, a Voice PE or the chat in the app
never see it.

genbu__firmware answers questions about the Shows' software and does what the house's update card does:
installs a waiting update or goes back to the version before, after asking, skips an update, picks the
release channel and turns Install at night on or off. A Show with Updates by voice on gets it, and so does
an admin typing in the app; another voice device never does. At a Show it only tells, installs and turns
night installs on or off; going back, a new channel and skipping are the app's (GENBU-262). Whoever is
standing there, or a web page the model read, can't talk it into those, since the model sets the yes
itself. See docs/genbu-assistant.md §1 and §4.

And when a wake word cut the Show's last reply off, the turn after it is told how much of the reply was
heard (reply_cut.py, GENBU-250); that comes with no tool.

Lines that only help a language model, the LLM extras, are added only while the house says its assistant is
one (switch.genbu_llm_assistant, hub.house.llm, GENBU-240). The first is the room line for a request whose
nearest Show is offline (GENBU-239); there is none yet. The lines above go out either way, as before."""

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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.ssl import get_default_context

try:  # Home Assistant 2026.10 and later
    import probatio as vol
except ImportError:
    import voluptuous as vol

from . import recipe as rec
from . import reply_cut
from .const import DOMAIN, NEW_RECIPE, RECIPES, SHOW_RECIPE, UPDATE_ENTITY, VOICE_UPDATES
from .house import CHANNELS, Show, channel_of, newer, stage, valid

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
FIRMWARE_PROMPT = {
    "de": ("Fragen zur Software oder Version der Genbu Shows beantwortet genbu__firmware. Nenne nur Versionen "
           "aus dem Ergebnis und sag nur, dass alle gleich sind, wenn all_on_house_version das sagt. Bevor ein Update "
           "installiert, zurückgegangen oder der Kanal gewechselt wird, frag immer erst nach."),
    "en": ("genbu__firmware answers questions about the Genbu Shows' software or version. Name only versions "
           "from its result, and say they all match only when all_on_house_version says so. Always ask before an "
           "update is installed, a version is gone back to or the channel changes."),
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
    # The app sends no device; whether it is an admin is checked when the tool is called.
    if hub.shows and (hub.switched_on(show, VOICE_UPDATES) if show else llm_context.device_id is None):
        tools.append(FirmwareTool(show))
        prompt.append(FIRMWARE_PROMPT[_lang(llm_context)])
    if show is not None and (cut := hub.take_cut(show.device)) is not None:
        prompt.append(reply_cut.prompt(cut, _lang(llm_context)))
    if not prompt:
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


ACTIONS = ["status", "install", "go_back", "skip", "channel", "night"]
AT_A_SHOW = ["status", "install", "night"]  # the rest only an admin in the app (GENBU-262)
NEEDS_YES = {"install": "install {version} on all Shows now", "go_back": "go back to {version} on all Shows",
             "channel": "switch the Shows to the {channel} channel"}


class FirmwareTool(llm.Tool):
    name = f"{DOMAIN}__firmware"
    title = "Genbu Show software"
    description = (
        "The software (firmware) of the Genbu Shows: you are one of them. The Shows follow one version, the house "
        "version, but a Show can still be behind, ahead or offline: all_on_house_version says whether they match, "
        "and not_on_house_version names the ones that don't. Use it when someone asks about your or the Shows' version, whether there is an update, what is "
        "new in it, or wants it installed, skipped, or to go back to the version before; also for the release "
        "channel and whether updates install at night. Not for other devices, their settings or readings, and "
        "not for news, weather or lists. "
        "action status: versions, channel and the waiting update. install: installs the waiting update on all "
        "Shows. go_back: back to the version before (or the one given). skip: no more asking about the waiting "
        "update. channel: stable, staging or dev. night: on or off. For install, go_back and channel, ask first "
        "and set confirm only after the person said yes.")
    parameters = vol.Schema({
        vol.Required("action", description="status, install, go_back, skip, channel or night"): vol.In(ACTIONS),
        vol.Optional("confirm", description="True only after the person said yes"): bool,
        vol.Optional("version", description="go_back only: the version to go to, if one was named"): str,
        vol.Optional("channel", description="channel only: stable, staging or dev"): vol.In(list(CHANNELS)),
        vol.Optional("on", description="night only: true to install updates at night"): bool,
    })
    integration = DOMAIN
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=True, open_world=False)

    def __init__(self, show: Show | None) -> None:
        self.show = show  # None: asked in the app
        if show is not None:
            # At a Show the model is told only of what it may do there.
            self.description = self.description.split(" action status:")[0] + (
                " action status: versions, channel and the waiting update. install: installs the waiting update "
                "on all Shows; ask first and set confirm only after the person said yes. night: on or off. "
                "Going back, skipping an update or another channel are done in the Home Assistant app by an "
                "admin; say so when asked.")
            self.parameters = vol.Schema({
                vol.Required("action", description="status, install or night"): vol.In(AT_A_SHOW),
                vol.Optional("confirm", description="True only after the person said yes"): bool,
                vol.Optional("on", description="night only: true to install updates at night"): bool,
            })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        if self.show is None:
            user_id = llm_context.context.user_id if llm_context.context else None
            user = await hass.auth.async_get_user(user_id) if user_id else None
            if user is None or not user.is_admin:
                return llm.ToolResult(data={"error": "Only an admin can ask about the Shows' software"}, error=True)
            who = user.name or "an admin"
        else:
            who = self.show.name
        hub = _hub(hass)
        if hub is None:
            return llm.ToolResult(data={"error": "Genbu Assistant is not running"}, error=True)
        args = tool_input.tool_args
        action = args.get("action", "status")
        if self.show is not None and action not in AT_A_SHOW:
            # The schema has kept it out already; this is for a model that calls it anyway.
            return llm.ToolResult(data={"error": "Not from a Show: going back, skipping an update and the "
                                        "channel are done in the Home Assistant app, by an admin"}, error=True)
        status = self._status(hass, hub)
        if action == "status":
            return llm.ToolResult(data=status)
        if action == "night":
            await hub.async_set_night(bool(args.get("on")), who)
            return llm.ToolResult(data={"install_at_night": hub.house.night})
        if action == "skip":
            if not hub.waiting:
                return llm.ToolResult(data={**status, "skipped": "nothing, no update is waiting"})
            await hass.services.async_call("update", "skip", {"entity_id": UPDATE_ENTITY}, blocking=True,
                                           context=llm_context.context)
            return llm.ToolResult(data={"skipped": status["update_waiting"],
                                        "note": "A newer update will be offered again."})
        try:
            return await self._change(hass, hub, action, args, status, who, llm_context)
        except HomeAssistantError as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)

    async def _change(self, hass: HomeAssistant, hub, action: str, args: dict, status: dict, who: str,
                      llm_context: llm.LLMContext) -> llm.ToolResult:
        """install, go_back and channel: each asks first."""
        if action == "install":
            if not status["update_waiting"]:
                return llm.ToolResult(data={**status, "installed": "nothing, every Show is up to date"})
            words = {"version": status["update_waiting"]}
        elif action == "go_back":
            target = str(args.get("version") or hub.house.before or "").strip()
            if not valid(target) or target == hub.house.version:
                return llm.ToolResult(data={**status, "went_back": "nothing, there is no version before to go "
                                            "back to; name one"})
            words = {"version": target}
        else:
            channel = args.get("channel")
            if channel not in CHANNELS:
                return llm.ToolResult(data={**status, "error": "say which channel: stable, staging or dev"},
                                      error=True)
            if channel == hub.house.channel:
                return llm.ToolResult(data={**status, "channel": f"already {channel}"})
            words = {"channel": channel}
        if not args.get("confirm"):
            return llm.ToolResult(data={**status, "ask_first": "Ask whether to " + NEEDS_YES[action].format(**words)
                                        + ". Do it only after a yes."})
        if action == "install":
            started = await hub.async_install_updates(who=who)
            return llm.ToolResult(data={"installing_on": started["updating"], "up_to_date": started["current"],
                                        "note": "Each Show restarts when its update is in."})
        if action == "go_back":
            await hass.services.async_call("update", "install", {"entity_id": UPDATE_ENTITY,
                                                                 "version": words["version"]}, blocking=True,
                                           context=llm_context.context)
            return llm.ToolResult(data={"house_version": hub.house.version,
                                        "note": "Each Show goes back and restarts. The newer one is skipped."})
        await hub.async_pick_channel(words["channel"], who)
        return llm.ToolResult(data={"channel": hub.house.channel, "update_waiting": hub.waiting or None})

    def _status(self, hass: HomeAssistant, hub) -> dict:
        """The house version and channel, the update waiting with its summary, each Show's version, and the
        Shows not on the house version. A Show still behind the house can have an update of its own waiting
        too."""
        house = hub.house
        out: dict = {"house_version": house.version or None, "channel": house.channel,
                     "update_waiting": hub.waiting or None, "whats_new": None,
                     "install_at_night": house.night, "version_before": house.before or None, "shows": []}
        if hub.waiting:
            out["whats_new"] = rec._line(str(hub.release.get("title") or "")) or None
        for show in hub.shows:
            state = hass.states.get(show.watch[0]) if show.watch else None
            attrs = state.attributes if state else {}
            installed = str(attrs.get("installed_version") or show.installed or "")
            item = {"name": show.name, "version": installed, "channel": channel_of(installed),
                    "online": state is not None and state.state in ("on", "off")}
            if self.show is not None and show.device == self.show.device:
                item["this_is_you"] = True
            if show.updating or installed != house.version:
                item["update_stage"] = stage(show, house.version)  # downloading 40%, waiting: why (GENBU-249)
            if state is not None and state.state == "on":
                item["update_to"] = latest = str(attrs.get("latest_version") or "")
                if not out["update_waiting"] and (not house.version or not newer(latest, house.version)):
                    out["update_waiting"] = latest  # catching up with the house
                    out["whats_new"] = out["whats_new"] or rec._line(str(attrs.get("release_summary") or "")) or None
            out["shows"].append(item)
        # Worked out here, not left to the model: it read "one version" as every Show's (GENBU-259).
        odd = [f"{s['name']} {s['version'] or 'unknown'}{'' if s['online'] else ' (offline)'}"
               for s in out["shows"] if not s["online"] or s["version"] != house.version]
        out["all_on_house_version"], out["not_on_house_version"] = not odd, odd
        return out
