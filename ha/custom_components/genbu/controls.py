"""Which of a Show's ESPHome entities get a copy on the Show's genbu device, so Devices & Integrations >
Genbu > a Show has all its controls (GENBU-114). Home Assistant lets a device belong to one integration
only, so they can't move there; Genbu adds copies that follow the original and pass on what is done to
them, and hides the everyday originals so dashboards and the device list don't show them twice.

The ESPHome unique id is "<MAC>/<sub-device>/<kind>/<Name>" for what echod makes, and "<MAC>-<key>" for
the Assist settings the ESPHome integration adds itself. echod's names stay English (it only translates
its own screen), so they are safe to match on. No Home Assistant import here, so the tests run without
it."""

from __future__ import annotations

import re
from dataclasses import dataclass

# ESPHome kind -> the genbu platform its copy is.
KINDS = {"light": "light", "switch": "switch", "select": "select", "number": "number", "button": "button",
         "text_sensor": "sensor"}
# Single entities with a copy of their own kind: (kind, Name) -> (platform, translation key, category).
# The speaker stays a media player on the ESPHome device (Assist and Music Assistant use it); its copy is
# the volume. The firmware stays the update entity there, so an update is offered once; the copy says
# which version runs.
SPECIAL = {("media_player", "Speaker"): ("number", "volume", None),
           ("update", "Firmware"): ("sensor", "firmware", "diagnostic")}
# The Assist settings ESPHome adds to a voice satellite, by the end of their unique id.
ASSIST = {"pipeline": "assistant", "pipeline_2": "assistant_2", "wake_word": "wake_word",
          "wake_word_2": "wake_word_2", "vad_sensitivity": "finished_speaking"}


@dataclass(frozen=True)
class Control:
    key: str  # what it is on the Show, the unique id without the MAC: "0/light/Screen", "pipeline"
    platform: str  # the copy: light, switch, select, number, button or sensor
    name: str  # ESPHome's name for it, or "" when the copy has a translated one
    special: str  # the copy's translation key, "" for a plain copy
    category: str | None  # "config" for a setting, "diagnostic", None for an everyday control
    hide: bool  # hide the ESPHome original once the copy is there


def control_for(unique_id: str, category: str | None) -> Control | None:
    """The copy one ESPHome entity of a Show gets, None when it gets none: diagnostics, the camera,
    events and the satellite itself stay on the ESPHome device only."""
    parts = unique_id.split("/", 3)
    if len(parts) != 4:
        key = unique_id.rsplit("-", 1)[-1] if "-" in unique_id else ""
        special = ASSIST.get(key)
        return Control(key, "select", "", special, "config", False) if special else None
    if category == "diagnostic":
        return None
    _mac, sub, kind, name = parts
    key = f"{sub}/{kind}/{name}"
    if (kind, name) in SPECIAL:
        platform, special, own = SPECIAL[(kind, name)]
        return Control(key, platform, "", special, own, False)
    platform = KINDS.get(kind)
    if platform is None or (platform == "sensor" and category == "config"):  # sensors can't be settings
        return None
    return Control(key, platform, name, "", category, category is None)


def object_id(node: str, control: Control) -> str:
    """genbu_<node>_<name>, so a copy's entity id never takes the original's."""
    return "_".join(("genbu", _slug(node), _slug(control.name or control.special)))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
