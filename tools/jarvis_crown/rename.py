"""Home Assistant's names for a Show that was renamed after it was installed.

Renaming a Show on its screen renames its device in Home Assistant, but the entity ids it got on the
first day keep the old name (select.jarvis_show_5_wake_word on a device called genbuShow5), and the
entities that turned up later have the room in front (switch.living_room_genbushow5_ssh). follow()
puts them all under the new name, and the automations, scripts and dashboards that use them with
them, each saved before it is changed. Home Assistant keeps an entity's history across a new id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import unicodedata
from typing import Any, Callable

from .home_assistant import (HomeAssistant, HomeAssistantError, NotFound, _areas, _services, service_prefix,
                             show_usernames)

Keep = Callable[[str, Any], str]


def slugify(text: str) -> str:
    """Home Assistant's slug for a name, the way it builds entity ids: "Küche 2" is kuche_2."""
    plain = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def _device_entry(device: dict) -> set[str]:
    one = device.get("config_entry_id")
    return set(device.get("config_entries") or []) | ({one} if one else set())


def show_devices(devices: list[dict], entities: list[dict], entry_id: str) -> list[dict]:
    """The Show's devices in Home Assistant, the main one first: the ESPHome entry's own, and the parts
    (ring, microphone, playback ...) that hang off it."""
    ids = {e.get("device_id") for e in entities if e.get("config_entry_id") == entry_id}
    found = [d for d in devices if d.get("id") in ids or entry_id in _device_entry(d)]
    return sorted(found, key=lambda d: bool(d.get("via_device_id")))


def device_name(device: dict) -> str:
    return str(device.get("name_by_user") or device.get("name") or "")


@dataclass
class Plan:
    """The new entity ids, old to new, and the ones whose new id is taken already."""

    entities: dict[str, str] = field(default_factory=dict)
    taken: list[tuple[str, str]] = field(default_factory=list)


def plan(entities: list[dict], devices: list[dict], entry_id: str, old: str, new: str,
         areas: dict[str, str], others: set[str] = frozenset()) -> Plan:
    """Which entities move to the new name. The Show's own ones lose the old name or the room in front
    of the new one; another integration's entities follow only when their device still has the old
    name (Music Assistant's player for the Show). others are entity ids Home Assistant has outside its
    registry, which are taken too."""
    out = Plan()
    old_slug, new_slug = slugify(old), slugify(new)
    if not old_slug or not new_slug or old_slug == new_slug:
        return out
    own = {d.get("id") for d in show_devices(devices, entities, entry_id)}
    named_old = {d.get("id") for d in devices if slugify(device_name(d)) == old_slug} - own
    rooms = sorted({slugify(n) for n in areas.values()} | {slugify(a) for a in areas}, key=len, reverse=True)
    rooms = [f"{r}_{new_slug}" for r in rooms if r]

    def moved(object_id: str, prefixes: list[str]) -> str | None:
        for prefix in prefixes:
            if object_id == prefix or object_id.startswith(prefix + "_"):
                return new_slug + object_id[len(prefix):]
        return None

    taken = {e.get("entity_id") for e in entities} | set(others)
    for e in entities:
        entity_id = str(e.get("entity_id") or "")
        domain, _, object_id = entity_id.partition(".")
        if e.get("config_entry_id") == entry_id or (e.get("device_id") and e.get("device_id") in own):
            to = moved(object_id, [old_slug] + rooms)
        elif e.get("device_id") and e.get("device_id") in named_old:
            to = moved(object_id, [old_slug])
        else:
            continue
        if to is None:
            continue
        new_id = f"{domain}.{to}"
        if new_id in taken or new_id in out.entities.values():
            out.taken.append((entity_id, new_id))
        else:
            out.entities[entity_id] = new_id
    return out


def _pattern(names: dict[str, str]) -> re.Pattern | None:
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_])(" + alternatives + r")(?![A-Za-z0-9_])")


def swap(node: Any, names: dict[str, str]) -> tuple[Any, int]:
    """node with every whole old name in names replaced by its new one, in values, keys and templates
    alike (select.x does not touch select.x_2); and how many were replaced."""
    pattern = _pattern(names)
    count = 0

    def walk(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str) and pattern is not None:
            text, n = pattern.subn(lambda m: names[m.group(1)], value)
            count += n
            return text
        if isinstance(value, dict):
            return {walk(k): walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value
    return walk(node), count


def _configs(ha: HomeAssistant, kind: str, states: list[dict]) -> list[tuple[str, Any]]:
    """The automations or scripts Home Assistant can edit (the ones in automations.yaml and scripts.yaml),
    by their config id, with their configs."""
    out = []
    for st in states:
        entity_id = str(st.get("entity_id") or "")
        if not entity_id.startswith(kind + "."):
            continue
        item = (st.get("attributes") or {}).get("id") if kind == "automation" else entity_id.split(".", 1)[1]
        if not item:
            continue
        try:
            out.append((str(item), ha.request("GET", f"/api/config/{kind}/config/{item}")))
        except NotFound:
            continue
    return out


def _dashboards(ha: HomeAssistant) -> list[tuple[str | None, Any]]:
    boards = ha.ws({"type": "lovelace/dashboards/list"}) or []
    out = []
    for path in [None] + [b.get("url_path") for b in boards if b.get("mode") == "storage"]:
        try:
            out.append((path, ha.ws({"type": "lovelace/config", "url_path": path})))
        except HomeAssistantError:
            continue  # never edited, or made by a strategy: no entity ids written down in it
    return out


def references(ha: HomeAssistant, names: dict[str, str], keep: Keep | None,
               progress: Callable[[str], None]) -> int:
    """Points the automations, scripts and dashboards that use an old name at the new one. Each is saved
    with keep first, and left alone when there is nowhere to save it. Returns how many were changed."""
    changed = 0
    states = ha.request("GET", "/api/states") or []
    found: list[tuple[str, str, Any, Callable[[Any], None]]] = []
    for kind in ("automation", "script"):
        for item, config in _configs(ha, kind, states):
            found.append((f"{kind}-{item}", f"{kind} {item}", config,
                          lambda new, kind=kind, item=item: ha.request(
                              "POST", f"/api/config/{kind}/config/{item}", new)))
    for path, config in _dashboards(ha):
        found.append((f"dashboard-{path or 'lovelace'}", f"dashboard {path or 'lovelace'}", config,
                      lambda new, path=path: ha.ws({"type": "lovelace/config/save", "url_path": path,
                                                    "config": new})))
    for what, label, config, save in found:
        new, count = swap(config, names)
        if not count:
            continue
        if keep is None:
            progress(f"WARN: {label} still uses the old names: there is nowhere to save it first")
            continue
        saved = keep(what, json.loads(json.dumps(config)))
        try:
            save(new)
        except HomeAssistantError as exc:
            progress(f"WARN: {label} still uses the old names ({exc}); the copy is in {saved}")
            continue
        progress(f"{label}: {count} name(s) changed; the old one is in {saved}")
        changed += 1
    return changed


def _check_config(ha: HomeAssistant, progress: Callable[[str], None]) -> None:
    try:
        result = ha.request("POST", "/api/config/core/check_config") or {}
    except HomeAssistantError as exc:
        progress(f"WARN: Home Assistant did not check its configuration: {exc}")
        return
    if result.get("result") == "valid":
        progress("Home Assistant checked its configuration: fine")
    else:
        progress(f"WARN: Home Assistant's configuration check says: {result.get('errors') or result.get('result')}")


def _entry_title(ha: HomeAssistant, entry_id: str, new: str, progress: Callable[[str], None]) -> None:
    entries = ha.request("GET", "/api/config/config_entries/entry?domain=esphome") or []
    title = next((str(e.get("title") or "") for e in entries if e.get("entry_id") == entry_id), None)
    if title is None or title == new:
        return
    ha.ws({"type": "config_entries/update", "entry_id": entry_id, "title": new})
    progress(f"ESPHome entry {title!r} is now called {new!r}")


def _show_user(ha: HomeAssistant, old: str, new: str, progress: Callable[[str], None]) -> None:
    """The Show's own Home Assistant user, from show_<old> to show_<new>, when there is one; the
    second name it may have (show_usernames) follows the same way."""
    users = ha.ws({"type": "config/auth/list"}) or []
    for was, now in zip(show_usernames(old), show_usernames(new)):
        if was == now:
            return
        user = next((u for u in users if (u.get("username") or "").lower() == was), None)
        if user is None:
            continue
        if any((u.get("username") or "").lower() == now for u in users):
            progress(f"WARN: Home Assistant has both {was!r} and {now!r}; the Show's user stays {was!r}")
            continue
        try:
            ha.ws({"type": "config/auth_provider/homeassistant/admin_change_username", "user_id": user["id"],
                   "username": now})
        except HomeAssistantError as exc:
            if "unauthorized" not in str(exc).lower():
                raise
            progress(f"WARN: the Show's user stays {was!r}: only Home Assistant's owner may rename users")
            return
        ha.ws({"type": "config/auth/update", "user_id": user["id"], "name": new})
        progress(f"Home Assistant user {was!r} is now {now!r}")


def follow(ha: HomeAssistant, entry_id: str, old: str, new: str, *, keep: Keep | None,
           progress: Callable[[str], None] = print) -> int:
    """Renames the Show's entities, and its actions where automations call them, from old to new; then
    the automations, scripts and dashboards that use them, the ESPHome entry's title and the Show's
    own user. Returns how many entities got a new id."""
    entities = ha.ws({"type": "config/entity_registry/list"}) or []
    devices = ha.ws({"type": "config/device_registry/list"}) or []
    states = ha.request("GET", "/api/states") or []
    known = plan(entities, devices, entry_id, old, new, _areas(ha),
                 {str(s.get("entity_id")) for s in states if s.get("entity_id")})
    for was, now in known.taken:
        progress(f"WARN: {was} keeps its id: {now} is taken already")
    done: dict[str, str] = {}
    for was, now in known.entities.items():
        try:
            ha.ws({"type": "config/entity_registry/update", "entity_id": was, "new_entity_id": now})
        except HomeAssistantError as exc:
            progress(f"WARN: {was} keeps its id: {exc}")
            continue
        done[was] = now
    if done:
        first = next(iter(done))
        progress(f"{len(done)} entities renamed ({first} is now {done[first]})")
    names = dict(done)
    old_actions, new_actions = service_prefix(old), service_prefix(new)
    if old_actions != new_actions:
        for action in _services(ha):
            if action.startswith(new_actions + "_"):
                names[f"esphome.{old_actions}{action[len(new_actions):]}"] = f"esphome.{action}"
    if names and references(ha, names, keep, progress):
        _check_config(ha, progress)
    _entry_title(ha, entry_id, new, progress)
    _show_user(ha, old, new, progress)
    return len(done)
