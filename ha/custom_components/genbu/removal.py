"""Deleting a Show in Home Assistant (GENBU-223): Delete on the Show's Genbu device takes the Show out of
the house and resets the Show itself back to setup, the same as the installer's --remove-show does from a
computer (tools/jarvis_crown/remove.py). Home Assistant's own "Are you sure?" is the only question.

What goes, in this order, once a copy of it is kept (.storage/genbu.removed.<node>) and a backup made:
  1. the Show's Home Assistant users (show_<name> and show_<name>-2, and the one its key belonged to), our
     automation for its voice assistant (techo5_turn_room_down_<name>), and the automations and scripts
     with its label, "Genbu Show: <node>";
  2. the Show's settings: it is asked over its own encrypted link to reset to setup (reset_to_setup in
     echod), keeping its Wi-Fi when its system can;
  3. its users and players in Music Assistant, after the reset, so the Show doesn't make its player again;
  4. its ESPHome entry, what Genbu kept about it, and its label once nothing carries it.
Then a notification says what went, what stayed and where the copy is.

A Show switched off in ESPHome, or offline, isn't reset behind anybody's back: the removal waits, with a
Repairs issue, until the Show is back, and goes on by itself then. Its button forgets the Show without
the reset, for one that is gone for good or was reset on its own screen. Switching a Show off on its own
never removes anything; only Delete does."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Callable

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import dump, load_yaml

from . import keys
from .const import DOMAIN
from .house import Show
from .recipe import show_action, show_prefix

LOGGER = logging.getLogger(__name__)
LEAVING = "leaving_"  # each waiting Show's issue is leaving_<node>
OURS = "techo5_turn_room_down_"  # our automation for a Show's voice assistant, as the installer names it
INSTALLED = "Genbu Show installed"  # the label on the house-wide pieces, which stay
RESET = "reset_to_setup"
ASK_FOR = 20  # seconds the Show has to answer the reset
ASK_AGAIN = 300  # seconds before a Show online that didn't answer is asked again
# What echod answers when it changed nothing (api/reset.go).
CANT_RESET = "can't be reset from Home Assistant"
CANT_KEEP_WIFI = "can't keep its Wi-Fi"
AUTOMATIONS, SCRIPTS = "automations.yaml", "scripts.yaml"


def _slug(name: str) -> str:
    """The node name echod gives itself from its display name (layout.Slug)."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def show_label(node: str) -> str:
    """The label on one Show's own pieces (tools/jarvis_crown/labels.py)."""
    return f"Genbu Show: {node}"


def ma_username(name: str) -> str:
    """The Show's user in Music Assistant (tools/jarvis_crown/music_assistant.py show_username)."""
    slug = _slug(name)
    return slug if len(slug) >= 2 else ""


def _mac(value) -> str:
    digits = re.sub(r"[^0-9a-f]", "", str(value or "").lower())
    return digits if len(digits) == 12 else ""


def shows_player(player: dict, mac: str, names: set[str]) -> bool:
    """Whether a Music Assistant player is the Show's: by its MAC or the Sendspin client id echod connects
    with, by its name when the MAC isn't known (music_assistant.py _shows_player)."""
    if mac:
        if _mac((player.get("device_info") or {}).get("mac_address")) == mac:
            return True
        return any(o.get("protocol_domain") == "sendspin" and _mac(o.get("output_protocol_id")) == mac
                   for o in player.get("output_protocols") or [])
    return (player.get("provider") in ("universal_player", "sendspin")
            and str(player.get("name") or "").strip().casefold() in names)


def _drop_items(path: str, wanted: set[str]) -> list[str]:
    """Takes the items with these ids out of automations.yaml (a list) or scripts.yaml (a map), the way
    Home Assistant's own editor deletes them; answers with the ids that were there."""
    if not os.path.isfile(path):
        return []
    data = load_yaml(path)
    if isinstance(data, list):
        gone = [str(i.get("id")) for i in data if isinstance(i, dict) and str(i.get("id")) in wanted]
        data = [i for i in data if not (isinstance(i, dict) and str(i.get("id")) in wanted)]
    elif isinstance(data, dict):
        gone = [k for k in data if str(k) in wanted]
        for k in gone:
            del data[k]
    else:
        return []
    if gone:
        write_utf8_file_atomic(path, dump(data))
    return [str(k) for k in gone]


def _read_items(path: str, wanted: set[str]) -> dict[str, dict]:
    if not os.path.isfile(path):
        return {}
    data = load_yaml(path)
    if isinstance(data, list):
        return {str(i.get("id")): i for i in data if isinstance(i, dict) and str(i.get("id")) in wanted}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if str(k) in wanted}
    return {}


class Removal:
    """The Shows on their way out, kept in House.data["leaving"] by node so a restart picks them up again."""

    def __init__(self, hub, find: Callable[[], list[Show]]) -> None:
        self.hub, self.hass, self._find = hub, hub.hass, find
        self._busy: set[str] = set()
        self._asked: dict[str, float] = {}  # when each Show online was last asked to reset, by node
        self._looked: set[str] = set()  # the Shows tried once since this start, which puts their issue back

    @property
    def leaving(self) -> dict[str, dict]:
        return self.hub.house.data["leaving"]

    async def _keep(self) -> None:
        await self.hub.store.async_save(self.hub.house.data)

    def tidy_issues(self) -> None:
        """Takes down the waiting issues of Shows no longer on their way out."""
        for domain, issue in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue.startswith(LEAVING) and issue[len(LEAVING):] not in self.leaving:
                ir.async_delete_issue(self.hass, DOMAIN, issue)

    async def async_start(self, device_id: str) -> bool:
        """Delete on a Show's Genbu device. True lets Home Assistant take the Genbu device away; the rest
        goes on in the background."""
        device = dr.async_get(self.hass).async_get(device_id)
        entry = self.hass.config_entries.async_get_entry(device.config_entry_id) if device else None
        if device is None or entry is None or entry.domain != "esphome":
            return True  # only Genbu's device is left of it
        name = device.name_by_user or device.name or ""
        node = entry.data.get("device_name") or _slug(name)
        if not node:
            return True
        if node not in self.leaving:
            self.leaving[node] = {
                "device": device.id, "entry": entry.entry_id, "name": name or node,
                "names": sorted({n for n in (name, device.name or "", node, entry.title) if n}),
                "mac": next((v for k, v in device.connections if k == dr.CONNECTION_NETWORK_MAC), ""),
                "since": dt_util.utcnow().isoformat(), "goes": [], "stays": [],
            }
            await self._keep()
            LOGGER.warning("%s was deleted in Home Assistant: it is taken out of the house and reset to setup",
                           name or node)
        self.kick(node)
        await self.hub.async_refresh()
        return True

    def kick(self, node: str) -> None:
        if node in self.leaving and node not in self._busy:
            self._busy.add(node)  # now, not when the task starts, so a look meanwhile doesn't start a second
            self.hub.entry.async_create_background_task(self.hass, self._run(node), f"{DOMAIN} remove {node}")

    def look(self, shows: list[Show]) -> None:
        """Every look: a Show waiting to be reset is tried again once it is online, or once its ESPHome
        entry is gone, and once after each start (its issue doesn't outlive a restart on its own)."""
        online = {s.node for s in shows if s.online}
        for node, rec in list(self.leaving.items()):
            entry = self.hass.config_entries.async_get_entry(rec.get("entry", ""))
            if node not in self._looked:
                self._looked.add(node)
                self.kick(node)
            elif entry is None or rec.get("reset") or rec.get("unreset") or not rec.get("house_done") or (
                    node in online and entry.state is ConfigEntryState.LOADED
                    and time.monotonic() - self._asked.get(node, -ASK_AGAIN) >= ASK_AGAIN):
                self.kick(node)

    async def async_forget_anyway(self, node: str) -> bool:
        """The waiting issue's button: the Show goes without being reset."""
        rec = self.leaving.get(node)
        if rec is None:
            return False
        rec["unreset"] = True
        await self._keep()
        self.kick(node)
        return True

    async def _run(self, node: str) -> None:
        try:
            rec = self.leaving.get(node)
            if rec is None:
                return
            if not rec.get("saved"):
                await self._save_first(node, rec)
            if not rec.get("house_done"):
                await self._house_side(node, rec)
                rec["house_done"] = True
                await self._keep()
            entry = self.hass.config_entries.async_get_entry(rec["entry"])
            if entry is not None and not rec.get("reset") and not rec.get("unreset"):
                why = await self._reset(node, rec, entry)
                if why:
                    LOGGER.info("%s is not reset yet: %s; it waits", rec["name"], why)
                    self._wait(node, rec)
                    return
            await self._finish(node, rec)
        except Exception:  # a removal half done must never take Genbu down with it
            LOGGER.exception("Removing %s stopped; it is tried again on the next look", node)
        finally:
            self._busy.discard(node)

    # 1. The copy, the backup and the house's side.

    def _items(self, node: str, rec: dict) -> tuple[set[str], set[str], list[str]]:
        """Our automations and the scripts with the Show's label, by config id, and those labels' ids."""
        ents = er.async_get(self.hass)
        labels = lr.async_get(self.hass)
        names = rec["names"]
        wanted = {show_label(node).casefold()} | {show_label(_slug(n)).casefold() for n in names if _slug(n)}
        label_ids = [l.label_id for l in labels.async_list_labels() if l.name.casefold() in wanted]
        autos = {OURS + show_prefix(n) for n in names if show_prefix(n)}
        scripts: set[str] = set()
        for label_id in label_ids:
            for e in er.async_entries_for_label(ents, label_id):
                if e.platform == "automation" and e.domain == "automation":
                    autos.add(e.unique_id)
                elif e.platform == "script" and e.domain == "script":
                    scripts.add(e.unique_id)
        for e in er.async_entries_for_device(ents, rec["device"], include_disabled_entities=True):
            if e.domain == "assist_satellite":
                for auto in self._automations_with_entity(e.entity_id):
                    if (r := ents.async_get(auto)) is not None and r.unique_id.startswith(OURS):
                        autos.add(r.unique_id)
        return autos, scripts, label_ids

    def _automations_with_entity(self, entity_id: str) -> list[str]:
        from homeassistant.components.automation import automations_with_entity
        return automations_with_entity(self.hass, entity_id)

    async def _hass_users(self, node: str, rec: dict) -> list:
        recorded = (self.hub.house.data["keys"].get(rec["device"]) or {}).get("user", "")
        names = {u for n in rec["names"] if show_prefix(n) for u in keys.usernames(show_prefix(n))}
        found = []
        for user in await self.hass.auth.async_get_users():
            mine = {str(c.data.get("username", "")).strip().casefold() for c in user.credentials
                    if c.auth_provider_type == "homeassistant"}
            if user.id == recorded or mine & names:
                found.append(user)
        return found

    async def _music(self, rec: dict) -> list[tuple[object, list[dict], list[dict]]]:
        """Each Music Assistant connection, with the Show's users and players there."""
        names = rec["names"]
        usernames = {u for u in (ma_username(n) for n in names) if u}
        folded = {n.strip().casefold() for n in names if n.strip()}
        out = []
        for entry in self.hass.config_entries.async_entries("music_assistant"):
            mass = getattr(getattr(entry, "runtime_data", None), "mass", None)
            if entry.state is not ConfigEntryState.LOADED or mass is None:
                continue
            async with asyncio.timeout(ASK_FOR):
                users = [u for u in await mass.send_command("auth/users") or []
                         if isinstance(u, dict) and u.get("username") in usernames]
                players = [p for p in await mass.send_command("players/all") or []
                           if isinstance(p, dict) and shows_player(p, _mac(rec.get("mac")), folded)]
            out.append((mass, users, players))
        return out

    async def _save_first(self, node: str, rec: dict) -> None:
        """A copy of everything that goes, for putting it back by hand, and a backup if the house makes them."""
        autos, scripts, _ = self._items(node, rec)
        path = self.hass.config.path
        configs = await self.hass.async_add_executor_job(
            lambda: {"automations": _read_items(path(AUTOMATIONS), autos),
                     "scripts": _read_items(path(SCRIPTS), scripts)})
        users = [{"id": u.id, "name": u.name, "admin": u.is_admin, "active": u.is_active}
                 for u in await self._hass_users(node, rec)]
        music: list[dict] = []
        try:
            for _, ma_users, players in await self._music(rec):
                music.append({"users": [{k: u.get(k) for k in ("user_id", "username", "role", "display_name")}
                                        for u in ma_users],
                              "players": [{k: p.get(k) for k in ("player_id", "name", "provider")} for p in players]})
        except Exception as err:  # Music Assistant down: its part is said in the notification later
            LOGGER.info("Music Assistant not read for %s's copy: %s", node, err)
        copy = {"show": {k: rec.get(k) for k in ("name", "names", "since")}, "home_assistant_users": users,
                "music_assistant": music, **configs}
        await Store(self.hass, 1, f"{DOMAIN}.removed.{node}").async_save(copy)
        rec["copy"] = f".storage/{DOMAIN}.removed.{node}"
        if self.hass.services.has_service("backup", "create_automatic"):
            try:
                await self.hass.services.async_call("backup", "create_automatic", blocking=True)
                rec["backup"] = True
            except Exception as err:
                LOGGER.warning("No backup before removing %s: %s", node, err)
                rec["backup"] = False
        rec["saved"] = True
        await self._keep()

    async def _house_side(self, node: str, rec: dict) -> None:
        goes, stays = rec["goes"], rec["stays"]
        for user in await self._hass_users(node, rec):
            if user.is_owner or user.is_admin or user.system_generated:
                stays.append(f"Home Assistant user {user.name!r}: an admin, never removed here")
                continue
            await self.hass.auth.async_remove_user(user)
            goes.append(f"Home Assistant user {user.name!r}")
        autos, scripts, _ = self._items(node, rec)
        path = self.hass.config.path
        # Home Assistant's editor holds a lock of its own while it writes these files; an edit there in the
        # very same second could be lost, which is why the copy above comes first.
        gone_autos = await self.hass.async_add_executor_job(_drop_items, path(AUTOMATIONS), autos)
        gone_scripts = await self.hass.async_add_executor_job(_drop_items, path(SCRIPTS), scripts)
        ents = er.async_get(self.hass)
        for kind, gone, wanted in (("automation", gone_autos, autos), ("script", gone_scripts, scripts)):
            for item in gone:
                if entity_id := ents.async_get_entity_id(kind, kind, item):
                    ents.async_remove(entity_id)
                goes.append(f"{kind} {item}")
            for item in sorted(wanted - set(gone)):
                if ents.async_get_entity_id(kind, kind, item):  # in a package, not in the editor's file
                    stays.append(f"{kind} {item}: not in {kind}s.yaml, so not removed here")
        for use in self._uses(rec, autos | scripts):
            stays.append(f"{use} uses it; left as it is")

    def _uses(self, rec: dict, ours: set[str]) -> list[str]:
        """The automations and scripts outside ours that use the Show."""
        from homeassistant.components.automation import automations_with_device
        from homeassistant.components.script import scripts_with_device, scripts_with_entity
        ents = er.async_get(self.hass)
        found = set(automations_with_device(self.hass, rec["device"])) | set(
            scripts_with_device(self.hass, rec["device"]))
        for e in er.async_entries_for_device(ents, rec["device"], include_disabled_entities=True):
            found |= set(self._automations_with_entity(e.entity_id)) | set(scripts_with_entity(self.hass, e.entity_id))
        return sorted(u for u in found if (r := ents.async_get(u)) is None or r.unique_id not in ours)

    # 2. The Show's own reset.

    async def _reset(self, node: str, rec: dict, entry) -> str:
        """Asks the Show to reset to setup. "" when that is done or can't be done; else why it waits."""
        if entry.state is not ConfigEntryState.LOADED:
            return "switched off in ESPHome" if entry.disabled_by else "its ESPHome connection isn't set up"
        show = next((s for s in self._find() if s.node == node), None)
        if show is None or not show.online:
            return "offline"
        action = show_action(list(self.hass.services.async_services_for_domain("esphome")), rec["names"], True,
                             RESET)
        if action is None:
            rec["stays"].append("the Show's own settings: its system can't be reset from Home Assistant; "
                                "reset it on its own screen")
            rec["reset"] = "cant"
            return ""
        self._asked[node] = time.monotonic()
        keep = rec.get("keep_wifi", True)
        try:
            async with asyncio.timeout(ASK_FOR):
                answer = await self.hass.services.async_call(
                    "esphome", action, {"confirm": "reset", "keep_wifi": keep}, blocking=True, return_response=True)
        except TimeoutError:
            return "it didn't answer"
        except HomeAssistantError as err:
            said = str((getattr(err, "translation_placeholders", None) or {}).get("error") or err)
            if CANT_KEEP_WIFI in said and keep:
                rec["keep_wifi"] = False
                await self._keep()
                return await self._reset(node, rec, entry)
            if CANT_RESET in said:
                rec["stays"].append("the Show's own settings: its system can't be reset from Home Assistant; "
                                    "reset it on its own screen")
                rec["reset"] = "cant"
                return ""
            return f"it said: {said}"
        if not (isinstance(answer, dict) and answer.get("reset") is True):
            return "it didn't say it resets"
        rec["reset"] = "done"
        rec["goes"].append("the Show's own settings: it restarts in setup mode"
                           + (", on the same Wi-Fi" if keep else ", Wi-Fi erased too (its system can't keep it)"))
        await self._keep()
        return ""

    # 3. and 4. The rest, once the Show is reset (or can't be, or is forgotten anyway).

    async def _finish(self, node: str, rec: dict) -> None:
        goes, stays = rec["goes"], rec["stays"]
        if rec.get("unreset"):
            stays.append("the Show's own settings: it was forgotten without a reset; reset it on its own "
                         "screen if it comes back")
        try:
            for mass, users, players in await self._music(rec):
                for user in users:
                    if user.get("role") != "user":
                        stays.append(f"Music Assistant user {user.get('username')!r}: not a plain user, "
                                     "never removed here")
                        continue
                    await mass.send_command("auth/user/delete", user_id=user["user_id"])
                    goes.append(f"Music Assistant user {user.get('username')!r}")
                for player in players:
                    await mass.send_command("config/players/remove", player_id=player["player_id"])
                    goes.append(f"Music Assistant player {player.get('name') or player['player_id']!r}")
        except Exception as err:
            stays.append(f"its user and player in Music Assistant: Music Assistant said {err}")
        if self.hass.config_entries.async_get_entry(rec["entry"]) is not None:
            await self.hass.config_entries.async_remove(rec["entry"])
            goes.append("its ESPHome entry, with its devices and entities")
        _, _, label_ids = self._items(node, rec)
        labels, ents, devs = lr.async_get(self.hass), er.async_get(self.hass), dr.async_get(self.hass)
        for label_id in label_ids:
            label = labels.async_get_label(label_id)
            if label is None:
                continue
            if er.async_entries_for_label(ents, label_id) or dr.async_entries_for_label(devs, label_id):
                stays.append(f"label {label.name!r}: something else still carries it")
            else:
                labels.async_delete(label_id)
                goes.append(f"label {label.name!r}")
        goes.append("what Genbu kept about it")
        stays.append(f"what has only the {INSTALLED!r} label, the blueprints, the room and its dashboard, "
                     "the calendars, and backups; on the router, any rules made for its address")
        name = rec["name"]
        self.leaving.pop(node, None)
        self._asked.pop(node, None)
        await self.hub.async_forget(node)
        await self._keep()
        ir.async_delete_issue(self.hass, DOMAIN, f"{LEAVING}{node}")
        kept = f"A copy of what went is in {rec.get('copy')}"
        if rec.get("backup"):
            kept += ", and a backup was made first (Settings, System, Backups)"
        elif rec.get("backup") is False:
            kept += "; the backup before it failed, see the log"
        lines = [f"**{name} was removed.**", "", "Gone:", *(f"- {g}" for g in goes), "", "Stayed:",
                 *(f"- {s}" for s in stays), "", kept + "."]
        persistent_notification.async_create(self.hass, "\n".join(lines), title=f"{name} removed",
                                             notification_id=f"{DOMAIN}_removed_{node}")
        LOGGER.warning("%s was removed: %d things went, %d stayed", name, len(goes), len(stays))

    def _wait(self, node: str, rec: dict) -> None:
        ir.async_create_issue(
            self.hass, DOMAIN, f"{LEAVING}{node}", is_fixable=True, is_persistent=False,
            severity=ir.IssueSeverity.WARNING, translation_key="leaving",
            translation_placeholders={"name": rec["name"]}, data={"node": node})
