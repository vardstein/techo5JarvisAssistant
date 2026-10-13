"""The Show's place in the house's updates, through the Genbu integration (docs/update-leader.md, "Installer
flow"). The house has one update card in Home Assistant, update.genbu_show_firmware; every Show follows the
house version it installs.

After the Show is in Home Assistant the installer checks the integration is there, sets it up over REST when
it is not set up yet (its form asks nothing), and waits for the Show to reach the house version."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .home_assistant import Chooser, HomeAssistant, HomeAssistantError, NotFound, entry_entities

DOMAIN = "genbu"
HOUSE = "sensor.genbu_house_version"
UPDATE = "update.genbu_show_firmware"

# What set_up came to.
FOLLOW, SKIPPED = "follow", "skipped"

HOW_TO_ADD = ("copy ha/custom_components/genbu/ to /config/custom_components/genbu/ on Home Assistant "
              "and restart it")
ON_ITS_OWN = "the Show looks for updates on its own channel, as before"
GONE = ("unavailable", "unknown", "")


@dataclass
class House:
    version: str = ""  # the house version; '' while no Show has committed one


def _entry(ha: HomeAssistant) -> dict | None:
    entries = ha.request("GET", f"/api/config/config_entries/entry?domain={DOMAIN}") or []
    return next((e for e in entries if isinstance(e, dict) and e.get("domain") == DOMAIN), None)


def _state(ha: HomeAssistant, entity: str) -> dict:
    try:
        return ha.request("GET", f"/api/states/{entity}") or {}
    except NotFound:
        return {}


def house(ha: HomeAssistant) -> House:
    """The house as the integration's sensor has it."""
    st = _state(ha, HOUSE)
    version = str(st.get("state") or "")
    return House(version="" if version in GONE else version)


def _drop(ha: HomeAssistant, path: str) -> None:
    try:
        ha.request("DELETE", path)
    except HomeAssistantError:
        pass


def _status_entity(ha: HomeAssistant, entry_id: str) -> str | None:
    return next((e for e in entry_entities(ha, entry_id)
                 if e.startswith("sensor.") and e.endswith("_update_status")), None)


def _wait_aligned(ha: HomeAssistant, entry_id: str, version: str, progress: Callable[[str], None],
                  sleep: Callable[[float], None], clock: Callable[[], float], wait: float) -> None:
    """Watches the Show's update status until it follows the house version, cannot, or wait runs out.
    A freshly installed Show first lets its own version settle (5 minutes), then moves."""
    status = _status_entity(ha, entry_id)
    if not status or wait <= 0:
        return
    progress(f"waiting for it to move to version {version}; up to {int(wait // 60)} min")
    deadline = clock() + wait
    last = ""
    while True:
        line = str(_state(ha, status).get("state") or "")
        if line.startswith("Following "):
            progress(line)
            return
        if "did not install here" in line or "could not be fetched" in line or "not reachable since" in line:
            progress(f"WARN: {line}")
            return
        if clock() >= deadline:
            progress(f"it carries on by itself ({line or 'restarting'}); its update card in Home Assistant "
                     "shows how far it is")
            return
        shown = "restarting" if line in GONE else line
        if shown != last:
            progress(shown)
            last = shown
        sleep(15)


def set_up(ha: HomeAssistant, entry_id: str, node: str, *, choose: Chooser | None = None,
           progress: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.monotonic, wait: float = 900.0) -> str:
    """Puts the Show (its ESPHome entry_id, its node name) in the house's updates. Returns FOLLOW, or
    SKIPPED when Genbu is not in Home Assistant."""
    if _entry(ha) is None and not _first(ha, choose, progress):
        return SKIPPED
    found = house(ha)
    if not found.version:
        progress(f"the house starts at the newest Show's version once it has settled; updates then come on "
                 f"the {UPDATE} card")
        return FOLLOW
    progress(f"the house is on version {found.version}; {node} follows it, and updates come on the {UPDATE} "
             "card")
    _wait_aligned(ha, entry_id, found.version, progress, sleep, clock, wait)
    return FOLLOW


def _first(ha: HomeAssistant, choose: Chooser | None, progress: Callable[[str], None]) -> bool:
    """No Genbu entry yet: sets the integration up. False when Genbu is not in Home Assistant."""
    base = "/api/config/config_entries/flow"
    while True:
        try:
            form = ha.request("POST", base, {"handler": DOMAIN, "show_advanced_options": False})
            break
        except NotFound:
            # Home Assistant has no such integration: the folder is not there, or it was not restarted since.
            progress(f"Genbu is not in Home Assistant yet: {HOW_TO_ADD}")
            if choose is None or choose("genbu missing", "", ["again", "skip"], {}) != "again":
                progress(ON_ITS_OWN)
                return False
    flow = form.get("flow_id") if isinstance(form, dict) else None
    if not flow or form.get("type") != "form":
        raise HomeAssistantError("Genbu's setup did not open")
    done = ha.request("POST", f"{base}/{flow}", {})
    if not isinstance(done, dict) or done.get("type") != "create_entry":
        _drop(ha, f"{base}/{flow}")
        raise HomeAssistantError("Genbu's setup did not finish")
    progress("Genbu set up in Home Assistant")
    return True
