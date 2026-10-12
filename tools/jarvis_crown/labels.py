"""Home Assistant labels on what the installer makes, so that it can be found in Settings by label and taken
out again by jarvis-show.py --remove-show.

Everything gets "Genbu Show installed". What belongs to one Show (its devices, its turn-the-room-down
automation) also gets "Genbu Show: <node>", and --remove-show takes out what carries that one. The
house-wide automations and scripts only get the common label, so they stay when a Show goes.

Labels never stop an install: when Home Assistant does not take them, it is said and the run goes on.
"""
from __future__ import annotations

import time
from typing import Callable

from .home_assistant import HomeAssistant, HomeAssistantError

INSTALLED = "Genbu Show installed"
ICON = "mdi:tablet-dashboard"
COLOR = "teal"
_PLATFORMS = {"automation": "automation", "script": "script"}


def show_label(node: str) -> str:
    """The label for one Show's own pieces, by its ESPHome node name."""
    return f"Genbu Show: {node}"


def _listed(ha: HomeAssistant, kind: str) -> list[dict]:
    return [x for x in ha.ws({"type": f"config/{kind}/list"}) or [] if isinstance(x, dict)]


def find(ha: HomeAssistant, name: str, *, known: list[dict] | None = None) -> str | None:
    """The id of the label called name (case does not matter), or None."""
    want = name.casefold()
    for label in known if known is not None else _listed(ha, "label_registry"):
        if str(label.get("name") or "").casefold() == want and label.get("label_id"):
            return str(label["label_id"])
    return None


def ensure(ha: HomeAssistant, name: str, description: str) -> str:
    """The label's id, made first when the house has none by that name."""
    found = find(ha, name)
    if found:
        return found
    made = ha.ws({"type": "config/label_registry/create", "name": name, "icon": ICON, "color": COLOR,
                  "description": description}) or {}
    if not made.get("label_id"):
        raise HomeAssistantError(f"Home Assistant made no label {name!r}")
    return str(made["label_id"])


def _merge(have: list | None, add: list[str]) -> list[str] | None:
    """have plus add, or None when nothing is new. Home Assistant replaces the whole list on update."""
    have = [str(x) for x in have or []]
    new = [x for x in add if x not in have]
    return have + new if new else None


def tag_devices(ha: HomeAssistant, device_ids: list[str], label_ids: list[str]) -> int:
    """Adds the labels to these devices; returns how many changed."""
    changed = 0
    wanted = set(device_ids)
    for device in _listed(ha, "device_registry"):
        if device.get("id") not in wanted:
            continue
        labels = _merge(device.get("labels"), label_ids)
        if labels is not None:
            ha.ws({"type": "config/device_registry/update", "device_id": device["id"], "labels": labels})
            changed += 1
    return changed


def tag_items(ha: HomeAssistant, items: list[tuple[str, str, list[str]]], *,
              sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
              wait: float = 10) -> list[str]:
    """Adds labels to automations and scripts, each given as (kind, config id, label ids). One just saved
    has no registry entry until Home Assistant's reload is done, so this waits up to wait seconds for it.
    Returns the ones that never showed up, as "kind id"."""
    left = {(_PLATFORMS[kind], item): labels for kind, item, labels in items}
    until = clock() + wait
    while left:
        for entity in _listed(ha, "entity_registry"):
            key = (entity.get("platform"), entity.get("unique_id"))
            if key not in left:
                continue
            labels = _merge(entity.get("labels"), left.pop(key))
            if labels is not None:
                ha.ws({"type": "config/entity_registry/update", "entity_id": entity["entity_id"],
                       "labels": labels})
        if not left or clock() >= until:
            break
        sleep(1)
    return sorted(f"{kind} {item}" for kind, item in left)


def mark(ha: HomeAssistant, progress: Callable[[str], None], *, node: str = "", devices: list[str] = (),
         show_items: list[tuple[str, str]] = (), house_items: list[tuple[str, str]] = (),
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
    """Labels the Show's devices and automations with both labels, the house-wide items with the common
    one only. Says what did not work instead of failing."""
    if not (devices or show_items or house_items):
        return
    try:
        common = ensure(ha, INSTALLED, "Made by the Genbu Show installer")
        own = [ensure(ha, show_label(node), f"The Genbu Show {node}; --remove-show takes these out")] \
            if node and (devices or show_items) else []
        if devices:
            tag_devices(ha, list(devices), [common, *own])
        items = [(k, i, [common, *own]) for k, i in show_items] + [(k, i, [common]) for k, i in house_items]
        missing = tag_items(ha, items, sleep=sleep, clock=clock) if items else []
    except HomeAssistantError as exc:
        progress(f"WARN: Home Assistant did not take the labels ({exc}); the Show works without them, "
                 "but --remove-show then finds its pieces by name only")
        return
    if missing:
        progress(f"WARN: not labeled yet, Home Assistant had not loaded them: {', '.join(missing)}. "
                 "Running the installer again labels them")


def carrying(ha: HomeAssistant, label_id: str) -> tuple[list[dict], list[dict]]:
    """The entities and the devices that carry the label."""
    entities = [e for e in _listed(ha, "entity_registry") if label_id in (e.get("labels") or [])]
    devices = [d for d in _listed(ha, "device_registry") if label_id in (d.get("labels") or [])]
    return entities, devices
