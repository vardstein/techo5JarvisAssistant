"""Whether the Show can reach what it dials itself, and which router rule is missing when it can't.

On a default-deny IoT network (docs/iot-firewall-policy.md) each service the Show talks to needs a rule
of its own, and a missing one shows up far from its cause: the Home Assistant step waits for minutes, the
screen stays black, an update says "connection refused". So after first boot the installer logs in to the
Show with its own key and tries each one from there, then names the rule for each that is blocked, with
every value filled in (GENBU-219).

It only reads: nothing here changes the router or the Show. Sendspin (8928) and ESPHome (6053) are left
out, those are dialled into the Show, not by it.
"""
from __future__ import annotations

import re
import shlex
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from jarvis_crown.show_ssh import ssh_options

GITHUB = "github.com"  # where updates come from until they come through Home Assistant (GENBU-225)
MUSIC_ASSISTANT_PORT = 8095
MDNS = "224.0.0.251"
FIRST_SET_YEAR = 2025  # the kernel starts in 1970; a later year means NTP got through (echod clockSet)

_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]*")


@dataclass(frozen=True)
class Target:
    key: str  # the rule's name in the install guide's genbu_rules: haos, dashcast, mass, github
    what: str
    host: str
    port: int
    breaks: str
    proto: str = "tcp"


@dataclass
class Reach:
    """What a round of probes found. None where it could not be told."""
    address: str
    mac: str | None = None
    blocked: list[Target] = field(default_factory=list)
    clock_set: bool | None = None
    found_by_name: bool | None = None
    trouble: str = ""  # why the Show could not be asked at all

    @property
    def ok(self) -> bool:
        return not self.trouble and not self.blocked and self.clock_set is not False \
            and self.found_by_name is not False


def _host_port(value: str, default_port: int) -> tuple[str, int] | None:
    """host and port of "host:port", "[v6]:port" or a bare host; None when it isn't one."""
    s = value.strip()
    if s.startswith("["):
        host, _, rest = s[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif s.count(":") == 1:
        host, _, port = s.partition(":")
    else:
        host, port = s, ""
    if not _HOST.fullmatch(host or "-") or (port and not port.isdigit()):
        return None
    return host, int(port) if port else default_port


def targets(ha_url: str | None, dashcast: str | None, music_assistant: str | None) -> list[Target]:
    """What this Show dials, from the settings it was installed with. One left empty isn't set up and
    isn't probed."""
    out = []
    if ha_url:
        u = urlsplit(ha_url)
        if u.hostname and _HOST.fullmatch(u.hostname):
            out.append(Target("haos", "Home Assistant", u.hostname, u.port or (443 if u.scheme == "https" else 80),
                              "the Home Assistant step waits for the Show to hand over its assistant and wake "
                              "words and gives up, and the dashboard can't log in"))
    hp = _host_port(dashcast or "", 9555)
    if hp:
        out.append(Target("dashcast", "DashCast", *hp, "the screen stays black instead of showing the dashboard"))
    hp = _host_port(music_assistant or "", MUSIC_ASSISTANT_PORT)
    if hp:
        out.append(Target("mass", "Music Assistant", hp[0], MUSIC_ASSISTANT_PORT, "the Show shows no lyrics"))
    out.append(Target("github", "updates", GITHUB, 443, "updates fail with \"connection refused\""))
    return out


def probe_script(want: list[Target]) -> str:
    """The shell run on the Show: a line per target, its MAC and the year its clock says."""
    lines = ["command -v nc >/dev/null 2>&1 || echo NO-NC"]
    for t in want:
        lines.append(f"if nc -z -w 4 {shlex.quote(t.host)} {t.port} </dev/null >/dev/null 2>&1; "
                     f"then echo 'REACH {t.key} ok'; else echo 'REACH {t.key} no'; fi")
    lines.append("echo \"MAC $(cat /sys/class/net/wlan0/address 2>/dev/null)\"")
    lines.append("echo \"YEAR $(date +%Y)\"")
    return "\n".join(lines)


Runner = Callable[[list[str], str], subprocess.CompletedProcess]


def _ssh(argv: list[str], script: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=script, capture_output=True, text=True, timeout=60, check=False)


def probe(address: str, want: list[Target], *, identity: Path | None, name: str | None,
          run: Runner = _ssh, find_by_name: Callable[[], str | None] | None = None) -> Reach:
    """Tries each target from the Show at address, over ssh with its own key, and whether this computer
    finds it by name (mDNS) when find_by_name is given."""
    found = Reach(address)
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *ssh_options(identity, name),
            f"root@{address}", "sh", "-s"]
    try:
        r = run(argv, probe_script(want))
    except (OSError, subprocess.TimeoutExpired) as exc:
        found.trouble = f"ssh to the Show: {exc}"
        return found
    if r.returncode != 0:
        found.trouble = f"ssh to the Show: {(r.stderr or '').strip() or r.returncode}"
        return found
    said = r.stdout or ""
    if "NO-NC" in said.split():
        found.trouble = "the Show has no nc to try them with"
        return found
    by_key = {t.key: t for t in want}
    for line in said.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "REACH" and parts[2] == "no" and parts[1] in by_key:
            found.blocked.append(by_key[parts[1]])
        elif parts[:1] == ["MAC"] and len(parts) == 2 and re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", parts[1].lower()):
            found.mac = parts[1].lower()
        elif parts[:1] == ["YEAR"] and len(parts) == 2 and parts[1].isdigit():
            found.clock_set = int(parts[1]) >= FIRST_SET_YEAR
    if find_by_name is not None:
        found.found_by_name = bool(find_by_name())
    return found


def _resolved(host: str) -> str:
    """host, with the address it resolves to here when it is a name: a rule needs the address."""
    if re.fullmatch(r"[0-9.]+|.*:.*", host):
        return host
    try:
        addr = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except (OSError, IndexError):
        return host
    return f"{host} ({addr})"


def rule_name(name: str) -> str:
    """The Show's name as the install guide's genbu_rules takes it: letters, digits and _, a letter first."""
    s = re.sub(r"[^A-Za-z0-9_]", "", name)
    return s if s[:1].isalpha() else ("show" + s if s else "")


def missing_rules(found: Reach, *, resolve: Callable[[str], str] = _resolved) -> list[str]:
    """A line per missing rule: what it is for, from the Show to where, and what breaks without it.
    Empty when nothing is missing."""
    source = f"the Show {found.address}" + (f" (MAC {found.mac})" if found.mac else "")
    out = []
    for t in found.blocked:
        to = "the internet" if t.key == "github" else resolve(t.host)
        out.append(f"{t.what}: allow {t.proto.upper()} from {source} to {to} port {t.port}. "
                   f"Without it {t.breaks}.")
    if found.clock_set is False:
        out.append(f"Time: allow UDP from {source} to a time server port 123 (the router, or the one your "
                   "network hands out). Without it the clock is wrong, and updates wait until it is set.")
    if found.found_by_name is False:
        out.append(f"Finding it by name: let mDNS (UDP port 5353, {MDNS}) from {source} reach the network "
                   "Home Assistant and this computer are on, for example with an mDNS reflector. Without it "
                   "Home Assistant and the installer don't find the Show by its name.")
    return out


def openwrt_line(found: Reach, *, name: str, music_assistant: str | None) -> str | None:
    """The install guide's genbu_rules line for this Show, every value filled in; None when one is
    missing, since an empty value must never reach a command that writes router rules."""
    rn = rule_name(name)
    ma = _host_port(music_assistant or "", MUSIC_ASSISTANT_PORT)
    if not rn or not ma or not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", found.address) \
            or not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ma[0]):
        return None
    return f"genbu_rules {rn} {found.address} {ma[0]}"
