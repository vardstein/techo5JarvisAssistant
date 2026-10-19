"""A Show taken out of the house for good (jarvis-show.py --remove-show), so that it can be added again as a
new device, or given away, without anything of it left in Home Assistant or Music Assistant.

What goes, in this order, all of it saved with keep first:
  1. our turn-the-room-down automation for it (techo5_turn_room_down_*, from the blueprint, on its voice
     assistant), and every automation and script with its label, "Genbu Show: <node>" (labels.py). One
     somebody made from the blueprint by hand stays theirs, unless they gave it the Show's label;
  2. its own Home Assistant user (show_<name>, under each name it went by), which ends its token;
  3. its Music Assistant user (the one for the lyrics) and its player there, connected or not. Music
     Assistant tells Home Assistant, which then drops the player's device too. A Show that was reset first
     (show_reset.py) has forgotten Music Assistant and stays away; one that was not and is still on the
     network makes its player again;
  4. its ESPHome entry, which takes its devices, their entities and their area links with it;
  5. its place in Genbu (its last version, music choice, switches), through genbu.forget_show, after the
     ESPHome entry so that Genbu does not pick it up again first;
  6. the Show's label, once nothing carries it any more.
What stays: everything with only the "Genbu Show installed" label (the house-wide jarvis_local_*
automations, the music scripts), the blueprints, the room's area and dashboard, the calendars, and
backups/<serial>, so the unit can be installed again. Other automations, scripts and dashboards that use its
entities are listed, not changed.

Run again later, it finds what is left by the names and the MAC kept from the first run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable

from . import genbu, labels
from .home_assistant import (HomeAssistant, HomeAssistantError, NotFound, find_entry, node_name, node_slug,
                             service_prefix, show_usernames)
from .rename import _dashboards, device_name, show_devices, swap
from .show_ssh import key_path
from .voice_kit import duck_existing

Keep = Callable[[str, Any], str]
# music(names, mac) -> (users, players), or None when it could not look; drop(user_ids, player_ids) ->
# failures: Music Assistant's side.
Music = Callable[[list[str], str], "tuple[list[dict], list[dict]] | None"]
Drop = Callable[[list[str], list[str]], "list[str]"]

OURS = "techo5_turn_room_down_"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class Found:
    """What the house has of one Show."""

    name: str
    names: list[str] = field(default_factory=list)   # every name it went by, the given one first
    entry: dict | None = None                         # its ESPHome entry
    node: str = ""                                    # its ESPHome node name, Genbu's name for it
    mac: str = ""
    devices: list[dict] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    automations: list[tuple[str, dict]] = field(default_factory=list)   # ours, by config id
    scripts: list[tuple[str, dict]] = field(default_factory=list)       # with its label, by config id
    labels: list[dict] = field(default_factory=list)                    # its own labels, "Genbu Show: <node>"
    house: list[str] = field(default_factory=list)    # house-wide automations and scripts; they stay
    others: dict[str, list[str]] = field(default_factory=dict)  # the other Shows' labelled pieces; they stay
    uses: list[str] = field(default_factory=list)     # what else uses it; left alone
    users: list[dict] = field(default_factory=list)   # its Home Assistant users
    skipped_users: list[str] = field(default_factory=list)
    music_asked: bool = False
    music_users: list[dict] = field(default_factory=list)
    players: list[dict] = field(default_factory=list)
    genbu: bool = False          # Genbu is set up
    forget_action: bool = False  # and has genbu.forget_show (1.1.0 on)

    @property
    def anything(self) -> bool:
        return bool(self.entry or self.automations or self.scripts or self.labels or self.users
                    or self.music_users or self.players)

    @property
    def in_genbu(self) -> bool:
        """Whether Genbu has the Show to forget: one still in ESPHome. What Genbu keeps about it is not
        visible from outside, and forgetting twice does no harm."""
        return self.genbu and bool(self.node) and self.entry is not None


def _mac(device: dict) -> str:
    for kind, value in device.get("connections") or []:
        if kind == "mac" and value:
            return str(value).lower()
    return ""


def _add(names: list[str], name: Any) -> None:
    name = str(name or "").strip()
    if name and node_slug(name) and node_slug(name) not in {node_slug(n) for n in names}:
        names.append(name)


def _users(ha: HomeAssistant, found: Found) -> None:
    wanted = {u for n in found.names for u in show_usernames(n)}
    for user in ha.ws({"type": "config/auth/list"}) or []:
        username = str(user.get("username") or "").lower()
        if username not in wanted:
            continue
        if user.get("is_owner") or user.get("system_generated") or "system-admin" in (user.get("group_ids") or []):
            found.skipped_users.append(username)
        else:
            found.users.append(user)


def _automations(ha: HomeAssistant, found: Found) -> None:
    """Ours for its voice assistant, and ours by the id it would have under each of its names (the one
    left when the ESPHome entry went first)."""
    seen: set[str] = set()
    for satellite in (e for e in found.entities if e.startswith("assist_satellite.")):
        hit = duck_existing(ha, satellite)
        if hit and hit[0].startswith(OURS) and hit[0] not in seen:
            seen.add(hit[0])
            found.automations.append(hit)
    for name in found.names:
        item = OURS + service_prefix(name)
        if item in seen or not _ID_RE.fullmatch(item):
            continue
        try:
            config = ha.request("GET", f"/api/config/automation/config/{item}")
        except NotFound:
            continue
        seen.add(item)
        found.automations.append((item, config))


def _labeled(ha: HomeAssistant, found: Found) -> None:
    """The automations and scripts with the Show's label, under any of its names. Only the Show's own label
    counts; the common one is on the house-wide pieces too."""
    wanted = {labels.show_label(found.node).casefold()} | \
        {labels.show_label(node_slug(n)).casefold() for n in found.names if node_slug(n)}
    found.labels = [x for x in ha.ws({"type": "config/label_registry/list"}) or []
                    if isinstance(x, dict) and str(x.get("name") or "").casefold() in wanted and x.get("label_id")]
    if not found.labels:
        return
    ids = {x["label_id"] for x in found.labels}
    have = {"automation": {item for item, _ in found.automations}, "script": set()}
    into = {"automation": found.automations, "script": found.scripts}
    for entity in ha.ws({"type": "config/entity_registry/list"}) or []:
        kind, item = entity.get("platform"), str(entity.get("unique_id") or "")
        if kind not in into or not ids & set(entity.get("labels") or []) or item in have[kind] \
                or not _ID_RE.fullmatch(item):
            continue
        try:
            config = ha.request("GET", f"/api/config/{kind}/config/{item}")
        except NotFound:
            continue
        have[kind].add(item)
        into[kind].append((item, config))


def _house(ha: HomeAssistant, found: Found) -> None:
    """What the installer made for the whole house or for the other Shows: the automations and scripts with
    the common label that aren't this Show's. Listed under what stays, so a removal doesn't read as all of
    Genbu going."""
    known = [x for x in ha.ws({"type": "config/label_registry/list"}) or [] if isinstance(x, dict)]
    common = {x.get("label_id") for x in known
              if str(x.get("name") or "").casefold() == labels.INSTALLED.casefold()} - {None}
    if not common:
        return
    mine = {x.get("label_id") for x in found.labels}
    prefix = labels.show_label("")
    shows = {x.get("label_id"): str(x.get("name"))[len(prefix):] for x in known
             if str(x.get("name") or "").casefold().startswith(prefix.casefold()) and x.get("label_id") not in mine}
    ours = {item for item, _ in found.automations} | {item for item, _ in found.scripts}
    for entity in ha.ws({"type": "config/entity_registry/list"}) or []:
        have = set(entity.get("labels") or [])
        if entity.get("platform") not in ("automation", "script") or not have & common or have & mine \
                or str(entity.get("unique_id") or "") in ours:
            continue
        other = sorted(shows[x] for x in have if x in shows)
        if other:
            found.others.setdefault(other[0], []).append(str(entity.get("entity_id")))
        else:
            found.house.append(str(entity.get("entity_id")))
    found.house.sort()


def _uses(ha: HomeAssistant, found: Found) -> None:
    """The automations, scripts, scenes and dashboards outside ours that use the Show."""
    ours = {("automation", item) for item, _ in found.automations} | {("script", item) for item, _ in found.scripts}
    states = {str(s.get("entity_id")): s for s in ha.request("GET", "/api/states") or []}
    out: set[str] = set()
    for device in found.devices:
        try:
            related = ha.ws({"type": "search/related", "item_type": "device", "item_id": device["id"]}) or {}
        except HomeAssistantError:
            continue
        for kind in ("automation", "script", "scene"):
            for entity_id in related.get(kind) or []:
                item = str((states.get(entity_id, {}).get("attributes") or {}).get("id") or "")
                if (kind, item) not in ours:
                    out.add(entity_id)
    if found.entities:
        names = {e: e for e in found.entities}
        for path, config in _dashboards(ha):
            if swap(config, names)[1]:
                out.add(f"dashboard {path or 'lovelace'}")
    found.uses = sorted(out)


def _genbu(ha: HomeAssistant, found: Found) -> None:
    if genbu._entry(ha) is None:
        return
    found.genbu = True
    services = next((d.get("services") or {} for d in ha.request("GET", "/api/services") or []
                     if d.get("domain") == genbu.DOMAIN), {})
    found.forget_action = "forget_show" in services


def find(ha: HomeAssistant, name: str, *, also: list[str] = (), mac: str = "",
         music: Music | None = None) -> Found:
    """Everything of the Show called name in the house. also are other names it went by (its record's),
    mac its MAC when known from before; music looks in Music Assistant when there is a token for it."""
    found = Found(name=name.strip(), mac=mac.lower())
    _add(found.names, name)
    for other in also:
        _add(found.names, other)
    entry = next((e for e in (find_entry(ha, n) for n in found.names) if e), None)
    if entry is not None:
        found.entry = entry
        entry_id = str(entry.get("entry_id") or "")
        entities = ha.ws({"type": "config/entity_registry/list"}) or []
        devices = ha.ws({"type": "config/device_registry/list"}) or []
        found.devices = show_devices(devices, entities, entry_id)
        found.entities = sorted(str(e.get("entity_id")) for e in entities if e.get("config_entry_id") == entry_id)
        _add(found.names, entry.get("title"))
        if found.devices:
            _add(found.names, device_name(found.devices[0]))
            found.mac = _mac(found.devices[0]) or found.mac
        found.node = node_name(ha, entry_id) or node_slug(str(entry.get("title") or name))
    else:
        found.node = node_slug(name)
    _add(found.names, found.node)
    _automations(ha, found)
    _labeled(ha, found)
    _house(ha, found)
    _users(ha, found)
    got = music(found.names, found.mac) if music is not None else None
    if got is not None:
        found.music_users, found.players = got
        found.music_asked = True
    _uses(ha, found)
    _genbu(ha, found)
    return found


def _mask(mac: str) -> str:
    return "…" + mac[-5:] if len(mac) > 5 else mac


def only_this(found: Found) -> str:
    """The line above the lists: the removal is this one Show's."""
    return (f"only {found.name} goes; Genbu, the house's voice assistant, its automations and scripts, and "
            "the other Shows stay")


def _count(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def plan(found: Found) -> tuple[list[str], list[str]]:
    """What goes and what stays, in words."""
    goes, stays = [], []
    for item, _ in found.automations:
        goes.append(f"automation {item}")
    for item, _ in found.scripts:
        goes.append(f"script {item}")
    for user in found.users:
        goes.append(f"Home Assistant user {user.get('username')!r}")
    for username in found.skipped_users:
        stays.append(f"Home Assistant user {username!r}: an admin or Home Assistant's own, never removed here")
    for user in found.music_users:
        if user.get("role") == "user":
            goes.append(f"Music Assistant user {user.get('username')!r}")
        else:
            stays.append(f"Music Assistant user {user.get('username')!r}: not a plain user, never removed here")
    for player in found.players:
        label = f"Music Assistant player {player.get('name') or player.get('player_id')!r}"
        now = ", connected now" if player.get("available") else ""
        goes.append(f"{label} (and its device in Home Assistant){now}")
    if found.entry is not None:
        goes.append(f"ESPHome entry {found.entry.get('title')!r} with its {len(found.devices)} devices, "
                    f"{len(found.entities)} entities and their area links")
    if found.in_genbu:
        if found.forget_action:
            goes.append(f"{found.node}'s place in Genbu")
        else:
            stays.append(f"{found.node}'s place in Genbu: Genbu here is older than 1.1.0 ({genbu.HOW_TO_ADD})")
    for label in found.labels:
        goes.append(f"label {label.get('name')!r}")
    if not found.music_asked:
        stays.append("its user and player in Music Assistant: not looked at (that needs a Music Assistant "
                     "admin: --music-assistant-token-file, the keyring or signing in)")
    for use in found.uses:
        stays.append(f"{use} uses it; left as it is")
    for kind in ("automation", "script"):
        ids = [x for x in found.house if x.startswith(kind + ".")]
        if ids:
            stays.append(f"{_count(len(ids), 'house-wide ' + kind)}: {', '.join(ids)}")
    for show, ids in sorted(found.others.items()):
        stays.append(f"{show}'s own: {', '.join(sorted(ids))}")
    if not found.house:
        stays.append(f"what has only the {labels.INSTALLED!r} label (the house-wide jarvis_local_* automations, "
                     "the music scripts)")
    stays.append("the blueprints, the room and its dashboard, the calendars, and the Show's backups")
    stays.append(f"on this computer, its SSH key and pinned host key (~/.ssh/{key_path(found.name).name}*), "
                 "until `shows` puts them away; on the router, any rules made for its address")
    return goes, stays


def snapshot(found: Found) -> dict:
    """What is saved before anything goes. No tokens are in it."""
    return {"name": found.name, "names": found.names, "node": found.node, "mac": found.mac,
            "entry": found.entry, "devices": found.devices, "entities": found.entities,
            "automations": {item: config for item, config in found.automations},
            "scripts": {item: config for item, config in found.scripts},
            "labels": found.labels,
            "home_assistant_users": [{k: u.get(k) for k in ("id", "name", "username", "group_ids", "local_only")}
                                     for u in found.users],
            "music_assistant_users": [{k: u.get(k) for k in ("user_id", "username", "role", "display_name")}
                                      for u in found.music_users],
            "music_assistant_players": [{k: p.get(k) for k in ("player_id", "provider", "name", "available")}
                                        for p in found.players],
            "genbu": {"node": found.node}}


def remove(ha: HomeAssistant, found: Found, *, keep: Keep, drop: Drop | None = None,
           progress: Callable[[str], None] = print) -> int:
    """Removes what find found, in the order the module says; returns how many steps failed. A failed step
    is reported and the rest still run."""
    saved = keep(f"remove-show-{found.node or node_slug(found.name)}", json.loads(json.dumps(snapshot(found))))
    progress(f"what it had is saved in {saved}")
    failed = 0

    def step(label: str, do: Callable[[], Any]) -> bool:
        nonlocal failed
        try:
            do()
        except HomeAssistantError as exc:
            failed += 1
            progress(f"WARN: {label}: {exc}")
            return False
        progress(f"{label}: removed")
        return True

    for item, _ in found.automations:
        step(f"automation {item}", lambda item=item: ha.request("DELETE", f"/api/config/automation/config/{item}"))
    for item, _ in found.scripts:
        step(f"script {item}", lambda item=item: ha.request("DELETE", f"/api/config/script/config/{item}"))
    for user in found.users:
        step(f"Home Assistant user {user.get('username')!r}",
             lambda user=user: ha.ws({"type": "config/auth/delete", "user_id": user["id"]}))
    users = [u["user_id"] for u in found.music_users if u.get("role") == "user" and u.get("user_id")]
    players = [p["player_id"] for p in found.players if p.get("player_id")]
    if drop is not None and (users or players):
        problems = drop(users, players)
        for problem in problems:
            progress(f"WARN: Music Assistant: {problem}")
        failed += len(problems)
        if not problems:
            progress(f"Music Assistant: {len(users)} user(s) and {len(players)} player(s) removed")
    if found.entry is not None:
        step(f"ESPHome entry {found.entry.get('title')!r}",
             lambda: ha.request("DELETE", f"/api/config/config_entries/entry/{found.entry['entry_id']}"))
    if found.in_genbu and found.forget_action:
        try:
            answer = ha.request("POST", f"/api/services/{genbu.DOMAIN}/forget_show?return_response",
                                {"node": found.node}) or {}
        except HomeAssistantError as exc:
            failed += 1
            progress(f"WARN: Genbu did not forget {found.node}: {exc}")
        else:
            # Genbu before 1.4.0 names the main update Show it picked instead.
            new = str((answer.get("service_response") or {}).get("new_main") or "")
            progress(f"Genbu forgot {found.node}" + (f"; {new} is the main update Show now" if new else ""))
    for label in found.labels:
        name = f"label {label.get('name')!r}"
        try:
            entities, devices = labels.carrying(ha, label["label_id"])
        except HomeAssistantError as exc:
            failed += 1
            progress(f"WARN: {name}: {exc}")
            continue
        if entities or devices:
            progress(f"{name} stays: {len(entities) + len(devices)} thing(s) still carry it; run --remove-show "
                     "again once they are gone")
            continue
        step(name, lambda label=label: ha.ws({"type": "config/label_registry/delete", "label_id": label["label_id"]}))
    return failed
