"""Which key opens a Show over SSH.

Each Show has a key pair of its own on this computer, ~/.ssh/genbu_<name>, made for that Show and
let in by no other. The tools that log in to a Show (boot-logo, the reboot into fastboot) look for it
by the Show's name, and --ssh-identity names another file. With neither, ssh picks a key the way it
always does, which is how a Show installed before its own key existed is still reached.

The Show's host key is pinned the same way, in ~/.ssh/genbu_<name>.known_hosts: the install reads it
over the USB console, which nobody on the network can stand in for, so ssh never has to take the
first answer on trust. A Show installed before that is trusted on first contact instead and checked
from then on.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
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


def ssh_options(identity: Path | None, name: str | None = None, key_dir: Path | None = None) -> list[str]:
    """ssh's switches for that key, and for the Show's pinned host key when its name is known.
    IdentitiesOnly, so the agent or ~/.ssh/config can't offer a different key first and use up the
    Show's login attempts."""
    options = [] if identity is None else ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
    return options + host_options(name, key_dir)


def known_hosts_path(name: str, key_dir: Path | None = None) -> Path:
    """Where the Show's pinned host key lives, next to its key pair."""
    path = key_path(name, key_dir)
    return path.with_name(path.name + ".known_hosts")


def host_alias(name: str) -> str:
    """The name the pin is filed under. Not the address: that changes with DHCP, the Show doesn't.
    Lower case, as ssh files it."""
    return "genbu-" + key_path(name, Path(".")).name[len("genbu_"):].lower()


def host_options(name: str | None, key_dir: Path | None = None) -> list[str]:
    """ssh's switches that check the Show is the one installed: its own known_hosts file under its own
    alias, so a key pinned for one Show never vouches for another and nothing in ~/.ssh/known_hosts
    gets in the way. Pinned, a different key stops ssh; not pinned yet (installed before pinning),
    the first key seen is kept and checked from then on."""
    if not name:
        return []
    pins = known_hosts_path(name, key_dir)
    strict = "yes" if pins.is_file() else "accept-new"
    return ["-o", f"HostKeyAlias={host_alias(name)}", "-o", f"UserKnownHostsFile={pins}",
            "-o", f"StrictHostKeyChecking={strict}"]


_HOST_KEY = re.compile(r"(ssh-ed25519) (AAAAC3NzaC1lZDI1NTE5[A-Za-z0-9+/]+={0,2})")


def pin_host_key(name: str, text: str, key_dir: Path | None = None) -> Path | None:
    """Pins the ed25519 host key found in text (dropbearkey -y's output) for the Show called name,
    replacing an older pin: the install is the one moment the key comes over a trusted line. None when
    text holds no such key, and nothing is changed then."""
    found = _HOST_KEY.search(text or "")
    if not found:
        return None
    pins = known_hosts_path(name, key_dir)
    pins.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fresh = pins.with_name(pins.name + ".new")
    fd = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{host_alias(name)} {found.group(1)} {found.group(2)}\n")
    os.replace(fresh, pins)
    return pins


class ShowKeyError(RuntimeError):
    pass


def public_key(path: Path) -> str:
    """The type and key of an OpenSSH public key file, without its comment."""
    fields = path.read_text(encoding="utf-8").split()
    if len(fields) < 2 or not fields[0].startswith(("ssh-", "ecdsa-", "sk-")):
        raise ShowKeyError(f"{path} does not look like an SSH public key")
    return " ".join(fields[:2])


def owner_of(pub: str, name: str, key_dir: Path | None = None) -> str | None:
    """Another Show whose own key this public key is, or None. One key never opens two Shows."""
    mine = key_path(name, key_dir).name
    for other in sorted((key_dir or KEY_DIR).glob("genbu_*.pub")):
        if other.name == mine + ".pub":
            continue
        try:
            if public_key(other) == pub:
                return other.name[len("genbu_"):-len(".pub")]
        except (OSError, ShowKeyError):
            continue
    return None


def make_key(name: str, key_dir: Path | None = None,
             run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[Path, bool]:
    """The Show's own key pair, made when it isn't there yet: (the private key, whether it was made
    now). A pair already there is the same Show's from an earlier install and is kept, never
    overwritten; half a pair is left alone and stops the install."""
    path = key_path(name, key_dir)
    pub = path.with_name(path.name + ".pub")
    if path.exists() and pub.exists():
        public_key(pub)
        return path, False
    if path.exists() or pub.exists():
        raise ShowKeyError(f"only half of the key pair {path} is there; move it away or pass --ssh-key")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    done = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", path.name, "-f", str(path)],
               stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if done.returncode != 0 or not pub.exists():
        raise ShowKeyError(f"ssh-keygen could not make {path}: {(done.stderr or '').strip() or done.returncode}")
    return path, True
