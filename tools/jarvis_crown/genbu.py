"""The Show's place in the house's updates, through the Genbu integration (docs/update-leader.md, "Installer
flow"). The house has one update card in Home Assistant, update.genbu_show_firmware; every Show follows the
house version it installs.

After the Show is in Home Assistant the installer checks the integration is there, sets it up over REST when
it is not set up yet (its form asks nothing), and waits for the Show to reach the house version."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .home_assistant import Chooser, HomeAssistant, HomeAssistantError, NotFound, _entry_devices, entry_entities

DOMAIN = "genbu"
HOUSE = "sensor.genbu_house_version"
UPDATE = "update.genbu_show_firmware"

# The Show's optional features on Genbu's side: how the installer asks for each, and Genbu's switch key.
FEATURES = (("recipes", "recipes"), ("voice updates", "voice_updates"))
PAGE = "Everything is on the Genbu Control Center page in Home Assistant's sidebar"

# What set_up came to.
FOLLOW, SKIPPED = "follow", "skipped"

HOW_TO_ADD = ("copy ha/custom_components/genbu/ to /config/custom_components/genbu/ on Home Assistant "
              "and restart it")
ON_ITS_OWN = "the Show looks for updates on its own channel, as before"
GONE = ("unavailable", "unknown", "")
# Said for a status in GONE: the Show may be restarting, or Home Assistant may have lost its entities, so
# it says only what is known.
NO_STATUS = "Home Assistant has no update status from it right now"


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
                  sleep: Callable[[float], None], clock: Callable[[], float], wait: float,
                  enter: Callable[[float], bool] | None = None) -> None:
    """Watches the Show's update status until it follows the house version, cannot, or wait runs out.
    A freshly installed Show first lets its own version settle (5 minutes), then moves. With enter
    (waits up to so many seconds, True when Enter was pressed) the user can go on at any time
    (GENBU-222); without it, nobody is at the terminal and it waits as before."""
    status = _status_entity(ha, entry_id)
    if not status or wait <= 0:
        return
    start = f"waiting for it to move to version {version}; up to {int(wait // 60)} min"
    progress(start + (". Press Enter once the update is done, or to go on without waiting" if enter else ""))
    deadline = clock() + wait
    last = ""
    while True:
        line = str(_state(ha, status).get("state") or "")
        if line.startswith("Following "):
            progress(line)
            return
        failed = line.startswith("Update to ") and " failed: " in line
        if failed or "did not install here" in line or "could not be fetched" in line or "not reachable since" in line:
            progress(f"WARN: {line}")
            return
        if "install it from the update card" in line or "with an update on the Genbu Show card" in line:
            # it was asked and said no (or nobody answered): it moves only when someone installs it
            progress(line)
            return
        shown = NO_STATUS if line in GONE else line
        carries_on = f"it carries on by itself ({shown}); its update card in Home Assistant shows how far it is"
        if clock() >= deadline:
            progress(carries_on)
            return
        if shown != last:
            progress(shown)
            last = shown
        if enter is None:
            sleep(15)
        elif enter(15):
            progress(f"going on; {carries_on}")
            return


def set_up(ha: HomeAssistant, entry_id: str, node: str, *, choose: Chooser | None = None,
           progress: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.monotonic, wait: float = 900.0,
           enter: Callable[[float], bool] | None = None) -> str:
    """Puts the Show (its ESPHome entry_id, its node name) in the house's updates. Returns FOLLOW, or
    SKIPPED when Genbu is not in Home Assistant. enter lets the user end the wait for the house version
    (see _wait_aligned)."""
    if _entry(ha) is None and not _first(ha, choose, progress):
        return SKIPPED
    found = house(ha)
    if not found.version:
        progress(f"the house starts at the newest Show's version once it has settled; updates then come on "
                 f"the {UPDATE} card")
        return FOLLOW
    progress(f"the house is on version {found.version}; {node} follows it, and updates come on the {UPDATE} "
             "card")
    _wait_aligned(ha, entry_id, found.version, progress, sleep, clock, wait, enter)
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


def features(ha: HomeAssistant, entry_id: str, given: dict[str, bool | None], *, choose: Chooser | None = None,
             progress: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic, wait: float = 30.0) -> None:
    """Recipes and Updates by voice for this Show (docs/genbu-assistant.md, "Optional features"). Each is
    taken from its switch, else asked; neither given nor asked, Genbu's own default stays. Recipes is there
    only while a recipe search is set up in Genbu, so it is only asked then."""
    entry = _entry(ha)
    if entry is None:
        return
    devices = _entry_devices(ha, entry_id)
    ids = {f"{entry['entry_id']}_{key}_{d}": label for label, key in FEATURES for d in devices}
    until = clock() + wait
    while True:
        # Genbu adds a Show's switches once it has seen the Show, a moment after ESPHome has it.
        found = {ids[e["unique_id"]]: e["entity_id"] for e in ha.ws({"type": "config/entity_registry/list"}) or []
                 if isinstance(e, dict) and e.get("platform") == DOMAIN and e.get("unique_id") in ids}
        if "voice updates" in found or clock() >= until:
            break
        sleep(2)
    if not found:
        progress("Genbu has not picked the Show up yet; Recipes and Updates by voice are on its card on the "
                 "Genbu Control Center page")
        return
    for label, _ in FEATURES:
        entity = found.get(label)
        on = given.get(label)
        if entity is None:
            continue
        if on is None and choose is not None:
            picked = choose(label, "", ["yes", "no"], {})
            on = None if picked is None else picked == "yes"
        if on is None:
            continue
        ha.request("POST", f"/api/services/switch/turn_{'on' if on else 'off'}", {"entity_id": entity})
        progress(f"{'Recipes' if label == 'recipes' else 'Updates by voice'} {'on' if on else 'off'} on this Show")
