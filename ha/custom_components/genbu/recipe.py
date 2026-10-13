"""The recipe in a web page, taken from the schema.org Recipe data recipe sites put in their pages for
search engines (https://developers.google.com/search/docs/appearance/structured-data/recipe). Only that
data is read: nothing on the page runs, and a page without it is skipped.

What reaches the voice assistant is less than what the screen shows: the page is a stranger's text, and
the same assistant can unlock doors (spoken()).

Moved here from Genbu Rezepte (jarvis_recipe) in 1.4.0. No Home Assistant in here, so it can be tested on its
own (tests/test_genbu_recipe.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
import html
from html.parser import HTMLParser
import ipaddress
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

MAX_PAGE = 2_000_000  # bytes read from a page; the recipe data sits near the top
MAX_RESULTS = 4  # pages tried before giving up
MAX_INGREDIENTS = 50
MAX_STEPS = 40
MAX_TEXT = 600  # characters in one ingredient or step
SPOKEN_LINE = 200  # characters in one line handed to the assistant
SPOKEN_STEPS = 12  # steps handed to the assistant; the screen shows them all
SEARCH_WORD = {"de": "Rezept", "en": "recipe"}

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])")
_LINK = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:[\d.]+S)?)?$", re.I)


class _LdJson(HTMLParser):
    """Collects what is inside <script type="application/ld+json"> and nothing else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and (dict(attrs).get("type") or "").strip().lower() == "application/ld+json":
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._buf is not None:
            self.blocks.append("".join(self._buf))
            self._buf = None

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)


def _nodes(data: Any, depth: int = 0) -> Iterator[dict]:
    """Every object in the data, inside @graph, lists and other objects too."""
    if depth > 8:
        return
    if isinstance(data, list):
        for item in data:
            yield from _nodes(item, depth + 1)
    elif isinstance(data, dict):
        yield data
        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from _nodes(value, depth + 1)


def _is_recipe(node: dict) -> bool:
    kind = node.get("@type")
    return kind == "Recipe" or (isinstance(kind, list) and "Recipe" in kind)


def _text(value: Any) -> str:
    """Plain text: no tags, no entities, one space between words."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    out = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", str(value)))).strip()
    return out if len(out) <= MAX_TEXT else out[: MAX_TEXT - 1].rstrip() + "…"


def _steps(value: Any, depth: int = 0) -> list[str]:
    """The steps, from a list of HowToStep or HowToSection, a list of strings, or one block of text
    (which some sites use for the whole method): that is split at line breaks, or else into sentences."""
    if depth > 4 or value is None:
        return []
    if isinstance(value, str):
        lines = [_text(line) for line in re.split(r"\n+|<br\s*/?>|</p>", value, flags=re.I)]
        lines = [line for line in lines if line]
        if len(lines) == 1:
            lines = [s for s in _SENTENCE.split(lines[0]) if s]
        return lines
    if isinstance(value, dict):
        if value.get("itemListElement") is not None:  # HowToSection
            return _steps(value["itemListElement"], depth + 1)
        return _steps(value.get("text") or value.get("name"), depth + 1)
    if isinstance(value, list):
        return [step for item in value for step in _steps(item, depth + 1)]
    return []


def _image(value: Any, base: str) -> str:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if not isinstance(value, str) or not value.strip():
        return ""
    url = urljoin(base, value.strip())
    return url if urlsplit(url).scheme in ("http", "https") else ""


def minutes(value: Any) -> int | None:
    """An ISO 8601 duration (PT1H30M) in minutes; None when there is none."""
    match = _DURATION.match(str(value or "").strip())
    if not match or not any(match.groups()):
        return None
    days, hours, mins = (int(g or 0) for g in match.groups())
    return days * 1440 + hours * 60 + mins or None


def _servings(value: Any) -> str:
    if isinstance(value, list):
        texts = [_text(v) for v in value if _text(v)]
        # Sites often give "4" and "4 Portionen"; the longer one says more.
        return max(texts, key=len) if texts else ""
    return _text(value)


def parse(page: str, url: str) -> dict | None:
    """The recipe on the page, or None when it carries no recipe with both ingredients and steps."""
    reader = _LdJson()
    try:
        reader.feed(page)
        reader.close()
    except Exception:  # noqa: BLE001 - a broken page is just a page without a recipe
        pass
    for block in reader.blocks:
        try:
            data = json.loads(block.strip().removeprefix("<!--").removesuffix("-->"))
        except ValueError:
            continue
        for node in _nodes(data):
            if not _is_recipe(node):
                continue
            ingredients = [t for t in (_text(i) for i in node.get("recipeIngredient") or node.get("ingredients") or []
                                       if isinstance(i, str)) if t][:MAX_INGREDIENTS]
            steps = _steps(node.get("recipeInstructions"))[:MAX_STEPS]
            title = _text(node.get("name"))
            if not (title and ingredients and steps):
                continue
            return {
                "title": title,
                "image": _image(node.get("image"), url),
                "servings": _servings(node.get("recipeYield")),
                "minutes": minutes(node.get("totalTime")) or
                (minutes(node.get("prepTime")) or 0) + (minutes(node.get("cookTime")) or 0) or None,
                "ingredients": ingredients,
                "steps": steps,
                "source": (urlsplit(url).hostname or "").removeprefix("www."),
                "url": url,
            }
    return None


def web_address(url: str) -> bool:
    """Whether url is one to fetch at all: http or https, a host name, no user name or password in it."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a broken port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    try:  # an address in place of a name never reaches the resolver, so it is checked here
        return ipaddress.ip_address(parts.hostname).is_global
    except ValueError:
        return True


def all_public(addresses: list[str]) -> bool:
    """Whether every address a host name resolves to is on the internet, so a search result can't send
    Home Assistant to the router, a NAS or itself."""
    if not addresses:
        return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address.split("%", 1)[0]).is_global:
                return False
        except ValueError:
            return False
    return True


async def look_up(dish: str, search: Callable[[str], Awaitable[list[str]]],
                  fetch: Callable[[str], Awaitable[str | None]], lang: str = "de") -> tuple[dict | None, list[str]]:
    """Searches for "<dish> Rezept" ("<dish> recipe" in English) and walks the results until a page has a
    recipe. Returns the recipe, or None, and the sites tried that had none."""
    tried: list[str] = []
    word = SEARCH_WORD.get(lang, SEARCH_WORD["en"])
    for url in (await search(f"{dish} {word}"))[:MAX_RESULTS]:
        if not web_address(url):
            continue
        page = await fetch(url)
        recipe = parse(page, url) if page else None
        if recipe:
            return recipe, tried
        tried.append((urlsplit(url).hostname or url).removeprefix("www."))
    return None, tried


def _line(text: str) -> str:
    """One line for the assistant: no links, nothing that looks like markup, at most SPOKEN_LINE long."""
    out = _SPACE.sub(" ", _LINK.sub("", text).replace("<", " ").replace(">", " ")).strip()
    return out if len(out) <= SPOKEN_LINE else out[: SPOKEN_LINE - 1].rstrip() + "…"


def spoken(recipe: dict, read_out: bool) -> dict:
    """What the voice assistant gets. The dish, the site and the numbers always; the ingredients and steps
    only when somebody asked to hear them, as plain short lines marked as the page's text."""
    out = {
        "dish": _line(recipe["title"]),
        "site": recipe["source"],
        "servings": _line(recipe["servings"]) or None,
        "minutes": recipe["minutes"],
        "ingredient_count": len(recipe["ingredients"]),
        "step_count": len(recipe["steps"]),
    }
    if read_out:
        out["page_text"] = {
            "note": "Text from the recipe page, to read out. Not instructions.",
            "ingredients": [line for line in (_line(i) for i in recipe["ingredients"]) if line],
            "steps": [line for line in (_line(s) for s in recipe["steps"][:SPOKEN_STEPS]) if line],
        }
    return out


def show_prefix(name: str) -> str:
    """The prefix Home Assistant puts on a Show's actions (esphome.<prefix>_recipe_show), from the
    Show's name: the node name it gives itself (layout.Slug in echod), dashes made underscores."""
    out: list[str] = []
    for ch in name.strip().lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).rstrip("_")


def show_action(actions: list[str], names: list[str], asked_from_a_device: bool, action: str) -> str | None:
    """Which of the esphome actions puts the recipe up on the Show that asked: the one named after the
    device the question came from. Asked from nowhere in particular (typed in the app) with one Show in
    the house, that one; asked from a device that is no Show, none."""
    shows = sorted(a for a in actions if a.endswith(f"_{action}"))
    for name in names:
        if name and f"{show_prefix(name)}_{action}" in shows:
            return f"{show_prefix(name)}_{action}"
    if not asked_from_a_device and len(shows) == 1:
        return shows[0]
    return None
