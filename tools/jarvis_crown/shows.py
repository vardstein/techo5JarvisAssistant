"""Which Show is which: backups/<serial>/show.json, written by the installer as soon as it knows the serial.

The later steps (adding a Show to Home Assistant, handing it its settings) find a unit by the name it was
installed with, so they never have to guess between several backups.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

RECORD = "show.json"


@dataclass(frozen=True)
class Show:
    name: str
    board: str
    folder: Path
    mac: str = ""   # kept by --remove-show, for the run after its ESPHome entry is gone

    @property
    def key_file(self) -> Path:
        return self.folder / "home-assistant.key"

    @property
    def serial_tail(self) -> str:
        return self.folder.name[-4:]


def record_show(folder: Path, *, name: str, board: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "board": board,
            "installed": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tmp = folder / (RECORD + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(folder / RECORD)


def update_show(folder: Path, **fields: str) -> bool:
    """Sets fields in the Show's record in folder, keeping the rest of it. False when there is no record
    there."""
    record = folder / RECORD
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    data.update(fields)
    tmp = folder / (RECORD + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(record)
    return True


def rename_show(folder: Path, name: str) -> bool:
    """Gives the Show recorded in folder its new name, keeping the rest of its record. False when there
    is no record there."""
    return update_show(folder, name=name)


def known_shows(backups: Path) -> list[Show]:
    shows = []
    for record in sorted(backups.glob(f"*/{RECORD}")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            shows.append(Show(name=str(data["name"]), board=str(data.get("board", "")), folder=record.parent,
                              mac=str(data.get("mac") or "")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return shows


def show_named(backups: Path, name: str) -> Show | None:
    """The Show installed under this name (case-insensitive), or None when there is not exactly one."""
    matches = [s for s in known_shows(backups) if s.name.casefold() == name.strip().casefold()]
    return matches[0] if len(matches) == 1 else None
