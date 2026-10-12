"""The Show's place in the house's updates, through the Genbu integration (docs/update-leader.md, "Installer
flow"). One Show, the main update Show, looks for updates and asks about them; the others follow it.

After the Show is in Home Assistant the installer checks the integration is there and sets it up over REST
when it is not set up yet, asks whether this Show is a main update Show or follows one, sets that through
the integration's select or options, and waits for a following Show to reach the house version."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .home_assistant import Chooser, HomeAssistant, HomeAssistantError, NotFound, entry_entities

DOMAIN = "genbu"
HOUSE = "sensor.genbu_house_version"
MAIN_SELECT = "select.genbu_main_update_show"
CONF_MAIN, CONF_EXTRA, CONF_NOTIFY = "main_show", "extra_mains", "notify_service"  # the integration's const.py

# What set_up came to, and what --main-update-show and --follow ask for.
MAIN, FOLLOW, SKIPPED = "main", "follow", "skipped"

HOW_TO_ADD = ("copy ha/custom_components/genbu/ to /config/custom_components/genbu/ on Home Assistant "
              "and restart it")
ON_ITS_OWN = "the Show looks for updates on its own channel, as before"
GONE = ("unavailable", "unknown", "")


@dataclass
class House:
    version: str = ""                                 # the house version; '' while no main has committed one
    mains: list[str] = field(default_factory=list)    # node names, the first main first


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
    mains = (st.get("attributes") or {}).get("main_shows") or []
    return House(version="" if version in GONE else version, mains=[str(m) for m in mains if m])


def _drop(ha: HomeAssistant, path: str) -> None:
    try:
        ha.request("DELETE", path)
    except HomeAssistantError:
        pass


def _offered(form: dict, name: str) -> list[str]:
    """The values a select field of a flow form offers, from the schema Home Assistant sends."""
    for f in form.get("data_schema") or []:
        if isinstance(f, dict) and f.get("name") == name:
            options = f.get("options") or []
            if isinstance(options, dict):  # a multi_select sends {value: label}
                return [str(o) for o in options]
            return [str(o[0] if isinstance(o, (list, tuple)) else o) for o in options]
    return []


def _suggested(form: dict, name: str) -> Any:
    for f in form.get("data_schema") or []:
        if isinstance(f, dict) and f.get("name") == name:
            return (f.get("description") or {}).get("suggested_value", f.get("default"))
    return None


def _make_main(ha: HomeAssistant, node: str, *, sleep: Callable[[float], None], clock: Callable[[], float],
               wait: float = 60.0) -> bool:
    """Makes node the main update Show through the select, once the integration has seen the Show."""
    deadline = clock() + wait
    while True:
        options = (_state(ha, MAIN_SELECT).get("attributes") or {}).get("options") or []
        if node in options:
            ha.request("POST", "/api/services/select/select_option", {"entity_id": MAIN_SELECT, "option": node})
            return True
        if clock() >= deadline:
            return False
        sleep(5)


def _add_main(ha: HomeAssistant, entry_id: str, node: str) -> None:
    """Makes node a further main update Show through the integration's options, past their warning,
    keeping the other answers there as they are."""
    base = "/api/config/config_entries/options/flow"
    form = ha.request("POST", base, {"handler": entry_id})
    flow = form.get("flow_id") if isinstance(form, dict) else None
    if not flow or form.get("type") != "form":
        raise HomeAssistantError("Genbu's options did not open")
    if node not in _offered(form, CONF_EXTRA):
        _drop(ha, f"{base}/{flow}")
        raise HomeAssistantError(f"Genbu's options do not offer {node} as a main update Show yet")
    extra = [str(m) for m in _suggested(form, CONF_EXTRA) or [] if m != node]
    answer = {CONF_EXTRA: extra + [node], CONF_NOTIFY: str(_suggested(form, CONF_NOTIFY) or "")}
    done = ha.request("POST", f"{base}/{flow}", answer)
    if isinstance(done, dict) and done.get("type") == "menu":
        done = ha.request("POST", f"{base}/{flow}", {"next_step_id": "second_yes"})
    if not isinstance(done, dict) or done.get("type") != "create_entry":
        _drop(ha, f"{base}/{flow}")
        raise HomeAssistantError("Genbu's options did not take the second main update Show")


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


def set_up(ha: HomeAssistant, entry_id: str, node: str, *, role: str | None = None, choose: Chooser | None = None,
           progress: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.monotonic, wait: float = 900.0) -> str:
    """Puts the Show (its ESPHome entry_id, its node name) in the house's updates; role is MAIN or FOLLOW
    from the switches, None to ask through choose. Returns MAIN, FOLLOW or SKIPPED."""
    entry = _entry(ha)
    if entry is None:
        return _first(ha, node, role, choose, progress, sleep, clock)

    found = house(ha)
    if node in found.mains:
        progress(f"{node} is {'the' if found.mains[0] == node else 'a'} main update Show")
        return MAIN
    if not found.mains:
        want = role == MAIN or (role is None and choose is not None
                                and choose("main update show", "", ["yes", "no"], {}) == "yes")
        if not want:
            progress("no main update Show yet; the Shows stay where they are until one is picked in "
                     f"{MAIN_SELECT}")
            return FOLLOW
        if not _make_main(ha, node, sleep=sleep, clock=clock):
            raise HomeAssistantError(f"Genbu has not seen {node} yet; pick it in {MAIN_SELECT} once it has")
        progress(f"{node} is now the main update Show")
        return MAIN

    mains = " and ".join(found.mains)
    at = f" (version {found.version})" if found.version else ""
    one = len(found.mains) == 1
    want = role
    if want is None and choose is not None:
        want = choose("follow main", "", [FOLLOW, MAIN], {
            FOLLOW: f"follow {mains}{at}",
            MAIN: "make this one a main update Show too"})
        if want == MAIN and choose("second main", "", ["no", "yes"], {
                "yes": f"yes, {node} asks about updates as well as {mains}"}) != "yes":
            want = FOLLOW
    if want == MAIN:
        progress(f"{mains} {'is' if one else 'are'} already main; with more than one, the other Shows follow "
                 "whichever is newer, and each main asks about updates")
        _add_main(ha, entry["entry_id"], node)
        progress(f"{node} is now a main update Show too")
        return MAIN
    progress(f"{mains} {'is your main update Show' if one else 'are your main update Shows'}{at}; "
             f"{node} follows")
    if found.version:
        _wait_aligned(ha, entry_id, found.version, progress, sleep, clock, wait)
    else:
        progress("no main update Show has settled on a version yet; it follows once one has")
    return FOLLOW


def _first(ha: HomeAssistant, node: str, role: str | None, choose: Chooser | None,
           progress: Callable[[str], None], sleep: Callable[[float], None], clock: Callable[[], float]) -> str:
    """No Genbu entry yet: sets the integration up, with this Show as the main update Show if wanted."""
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
                return SKIPPED
    flow = form.get("flow_id") if isinstance(form, dict) else None
    if not flow or form.get("type") != "form":
        raise HomeAssistantError("Genbu's setup did not open")
    want = role
    if want is None and choose is not None:
        want = MAIN if choose("main update show", "", ["yes", "no"], {}) == "yes" else FOLLOW
    if want is None:
        _drop(ha, f"{base}/{flow}")
        progress(f"Genbu is not set up; add it under Settings, Devices & services. Until then {ON_ITS_OWN}")
        return SKIPPED
    named = want == MAIN and node in _offered(form, CONF_MAIN)
    done = ha.request("POST", f"{base}/{flow}", {CONF_MAIN: node} if named else {})
    if not isinstance(done, dict) or done.get("type") != "create_entry":
        _drop(ha, f"{base}/{flow}")
        raise HomeAssistantError("Genbu's setup did not finish")
    progress("Genbu set up in Home Assistant")
    if want != MAIN:
        progress(f"no main update Show yet; the Shows stay where they are until one is picked in {MAIN_SELECT}")
        return FOLLOW
    if not named and not _make_main(ha, node, sleep=sleep, clock=clock):
        raise HomeAssistantError(f"Genbu has not seen {node} yet; pick it in {MAIN_SELECT} once it has")
    progress(f"{node} is now the main update Show")
    return MAIN
