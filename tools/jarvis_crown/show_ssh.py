"""Which key opens a Show over SSH.

Each Show has a key pair of its own on this computer, ~/.ssh/genbu_<name>, made for that Show and
let in by no other. The tools that log in to a Show (boot-logo, the reboot into fastboot) look for it
by the Show's name, and --ssh-identity names another file. With neither, ssh picks a key the way it
always does, which is how a Show installed before its own key existed is still reached.
"""
from __future__ import annotations

import re
from pathlib import Path

KEY_DIR = Path.home() / ".ssh"


def key_path(name: str, key_dir: Path | None = None) -> Path:
    """Where the private key for the Show called name lives. A name is never a path: anything but
    letters, digits, dot, dash and underscore becomes a dash."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", name).lstrip(".") or "show"
    return (key_dir or KEY_DIR) / f"genbu_{safe}"


def identity_for(name: str | None, given: Path | None = None, key_dir: Path | None = None) -> Path | None:
    """The key to log in to this Show with: the one given, else the Show's own if it is there."""
    if given:
        return given
    if name:
        path = key_path(name, key_dir)
        if path.is_file():
            return path
    return None


def ssh_options(identity: Path | None) -> list[str]:
    """ssh's switches for that key. IdentitiesOnly, so the agent or ~/.ssh/config can't offer a
    different key first and use up the Show's login attempts."""
    if identity is None:
        return []
    return ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
