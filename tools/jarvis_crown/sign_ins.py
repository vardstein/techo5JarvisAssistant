"""The installer's own sign-ins, which last one run.

Given an admin's username and password, the installer signs in to Home Assistant and Music Assistant
the way a browser does, works with that sign-in, and signs out again when the run ends: done, stopped
by an error, Ctrl-C, or the terminal closed. The password is used once to sign in and dropped.

Nothing of a sign-in is written down but its id, which is no secret: it only says which sign-in to
end, and only the user it belongs to can end it. So a run that is killed outright (kill -9, a power
cut) leaves its id behind, and the next run that signs in as the same user at the same place ends
that sign-in first.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable


class SignIns:
    def __init__(self, where: Callable[[], Path]) -> None:
        self._where = where
        self._open: list[tuple[dict, Callable[[], None]]] = []

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self._where().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    def _write(self, records: list[dict]) -> None:
        path = self._where()
        try:
            if not records:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=1)
                f.write("\n")
            os.replace(tmp, path)
        except OSError:
            pass

    def opened(self, service: str, address: str, username: str, sign_in_id: str | None, *,
               end: Callable[[], None], end_other: Callable[[str], bool] | None = None) -> int:
        """A sign-in that has to end with the run. Earlier runs' sign-ins of the same user at the same
        place are ended first, with end_other; returns how many of them were still there."""
        mine = {"service": service, "address": address, "username": username}
        records = self._read()
        left = [r for r in records if {k: r.get(k) for k in mine} == mine and r.get("id") != sign_in_id]
        ended = 0
        if end_other:
            for record in left:
                if record.get("id") and end_other(record["id"]):
                    ended += 1
            records = [r for r in records if r not in left]
        if sign_in_id:
            records.append({**mine, "id": sign_in_id})
        self._write(records)
        self._open.append(({**mine, "id": sign_in_id}, end))
        return ended

    def end_all(self, progress: Callable[[str], None]) -> None:
        """Signs out of everything this run signed in to, the last first. One that cannot be ended
        now is said so and stays recorded, for the next run to end."""
        while self._open:
            record, end = self._open.pop()
            try:
                end()
            except Exception as exc:  # noqa: BLE001 - every sign-in gets its turn
                progress(f"WARN: could not sign out of {record['service']} as {record['username']!r}: {exc}; "
                         "the next run that signs in as them ends it")
                continue
            if record.get("id"):
                self._write([r for r in self._read() if r != record])
            progress(f"signed out of {record['service']} as {record['username']!r}")

    @property
    def active(self) -> bool:
        return bool(self._open)
