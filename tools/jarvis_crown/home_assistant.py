"""Home Assistant's side of a Jarvis Show deployment, over Home Assistant's REST API.

Once the installer is done, a Show still has to be taken in by Home Assistant. By hand that means
five screens per unit: add the ESPHome device with its encryption key, allow it to perform Home
Assistant actions, pick the assistant and the wake word, and give it the DashCast server and its own
Home Assistant access, and put it in its room. Here that is one call as an admin: signed in for
the run with a username and password (sign_in, SignedIn), or with an admin's long-lived token. Both
are used for this and kept nowhere.

Every step looks first at what is already there, so running it again on a unit Home Assistant
already has only fills in what is missing. No secret is ever put in an error message.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

DASHCAST_PORT = 9555
DASHCAST_MIN_KEY = 16
ESPHOME_PORT = 6053
_ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class HomeAssistantError(RuntimeError):
    """Home Assistant refused, or never got to, a step of the deployment."""


class NotFound(HomeAssistantError):
    """Home Assistant answered 404: what was asked for is not there."""


class Unauthorized(HomeAssistantError):
    """Home Assistant answered 401: it did not take the token."""


class NotSignedIn(HomeAssistantError):
    """Home Assistant turned a sign-in down: a wrong password or code, or not an admin."""


# ------------------------------------------------------------------------------- addresses and names


def node_slug(name: str) -> str:
    """The ESPHome node name the daemon gives itself from its display name (layout.Slug in echod)."""
    out: list[str] = []
    dash = False
    for ch in name.strip().lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
            dash = False
        elif out and not dash:
            out.append("-")
            dash = True
    return "".join(out).rstrip("-")


def service_prefix(name: str) -> str:
    """How Home Assistant names the Show's actions: esphome.<prefix>_<action>."""
    return node_slug(name).replace("-", "_")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return "%" not in host


def _valid_host(host: str) -> bool:
    if not host:
        return False
    if _is_ip(host):
        return True
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        return False
    return not host.startswith(("-", "."))


def normalize_dashcast(addr: str) -> str:
    """The DashCast server as host:port, however it was typed; the same rules as the daemon's
    NormalizeServer, so what passes here is what the Show keeps. Empty stays empty."""
    s = addr.strip()
    if not s:
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    cut = re.search(r"[/?#]", s)
    if cut:
        s = s[: cut.start()]
    if "@" in s:
        s = s[s.rindex("@") + 1:]

    split = None
    if s.startswith("["):
        end = s.find("]")
        if end > 0 and s[end + 1: end + 2] == ":":
            split = (s[1:end], s[end + 2:])
    elif s.count(":") == 1:
        split = tuple(s.split(":"))
    if split is None:
        host, port = s.removeprefix("[").removesuffix("]"), str(DASHCAST_PORT)
        if ":" in host and not _is_ip(host):
            raise ValueError("not an address: write it as host:port, with an IPv6 address in brackets")
    else:
        host, port = split
    if not _valid_host(host):
        raise ValueError("not an address: write it as host:port, like 192.168.1.20:9555")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError(f"not a port number: {port}")
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def check_dashcast_key(key: str) -> str:
    key = key.strip()
    if len(key) < DASHCAST_MIN_KEY:
        raise ValueError(f"the DashCast key is shorter than the server accepts ({DASHCAST_MIN_KEY} characters)")
    if any(ord(ch) < 32 for ch in key):
        raise ValueError("the DashCast key has a control character in it")
    return key


def normalize_ha_url(url: str) -> str:
    """Home Assistant's address as http(s)://host[:port], the way the Show and this tool both use it.
    Without a scheme, a domain name means its https site (a reverse proxy, like haos.example.org) and
    an IP address, a .local name or a host:port means Home Assistant itself on http, port 8123."""
    url = url.strip()
    if url and "://" not in url:
        host = url.split("/", 1)[0]
        bare = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        proxied = ":" not in host and "." in bare and not bare.endswith(".local") \
            and not re.fullmatch(r"[\d.]+", bare)
        url = f"https://{url}" if proxied else f"http://{host if ':' in host else host + ':8123'}{url[len(host):]}"
    u = urllib.parse.urlsplit(url)
    scheme = u.scheme.lower()
    if scheme not in ("http", "https") or not u.hostname:
        raise ValueError("not a Home Assistant address: write it like http://homeassistant.local:8123")
    if u.username or u.password:
        raise ValueError("a Home Assistant address has no user in it; the token is asked for separately")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("a Home Assistant address is only the scheme, host and port")
    return f"{scheme}://{u.netloc}"


def normalize_music_assistant(addr: str, resolve: Callable[[str], str] | None = None) -> str:
    """The Music Assistant server as the one IP address the Show pairs its Sendspin player with.
    The Show takes a literal address only, so a name is looked up here, once, when resolve is given."""
    s = addr.strip()
    if not s:
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    cut = re.search(r"[/?#]", s)
    if cut:
        s = s[: cut.start()]
    if s.startswith("["):
        s = s[1:s.find("]")] if "]" in s else s
    elif s.count(":") == 1:
        s = s.split(":")[0]  # a port, as a browser shows it; the Show finds the player port itself
    if _is_ip(s):
        return str(ipaddress.ip_address(s))
    if resolve is None or not _valid_host(s):
        raise ValueError("the Music Assistant server has to be an IP address, like 192.168.1.20")
    try:
        ip = resolve(s)
    except OSError as exc:
        raise ValueError(f"cannot look up {s}: {exc}") from None
    if not _is_ip(ip):
        raise ValueError(f"{s} did not resolve to an IP address")
    return str(ipaddress.ip_address(ip))


def check_token(token: str, what: str) -> str:
    token = token.strip()
    if not token or any(ord(ch) < 33 or ord(ch) > 126 for ch in token):
        raise ValueError(f"{what} is empty or has spaces or other characters a token never has")
    return token


# ----------------------------------------------------------------------------------------- the client


class HomeAssistant:
    """A small REST client. The token goes in a header and nowhere else."""

    def __init__(self, url: str, token: str, *, timeout: float = 20.0,
                 opener: Callable[..., Any] | None = None) -> None:
        self.url = normalize_ha_url(url)
        self._token = token
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def request(self, method: str, path: str, body: Any = None, *, auth: bool = True,
                form: bool = False) -> Any:
        """auth=False leaves the token out (the login flow); form=True sends body as a form."""
        if body is None:
            data = None
        elif form:
            data = urllib.parse.urlencode(body).encode("utf-8")
        else:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if auth:
            req.add_header("Authorization", "Bearer " + self._bearer())
        if data is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(300).decode("utf-8", "replace").strip()
            except OSError:
                pass
            if exc.code == 401:
                raise Unauthorized(f"{method} {path}: Home Assistant did not accept the token (401)") from None
            if exc.code == 404:
                raise NotFound(f"{method} {path}: HTTP 404 {detail}".rstrip()) from None
            raise HomeAssistantError(f"{method} {path}: HTTP {exc.code} {detail}".rstrip()) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HomeAssistantError(f"cannot reach Home Assistant at {self.url}: {reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", "replace")

    def _bearer(self) -> str:
        return self._token

    def as_user(self, token: str) -> "HomeAssistant":
        """The same Home Assistant, signed in with another token."""
        return HomeAssistant(self.url, token, timeout=self.timeout, opener=self._open)

    def ws(self, message: dict) -> Any:
        """One command over the websocket API, for what REST cannot do (the device registry); returns
        its result."""
        u = urllib.parse.urlsplit(self.url)
        what = message.get("type", "websocket")
        try:
            sock: socket.socket = socket.create_connection(
                (u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=self.timeout)
        except OSError as exc:
            raise HomeAssistantError(f"cannot reach Home Assistant at {self.url}: {exc}") from None
        try:
            if u.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
            return _ws_exchange(sock, u.netloc, self._bearer(), message)
        except (OSError, ValueError) as exc:
            raise HomeAssistantError(f"{what}: {exc}") from None
        finally:
            sock.close()


class SignedIn(HomeAssistant):
    """An admin signed in for one run, the way a browser signs in (sign_in). It holds the refresh token
    and a short-lived access token, renews the access token from the refresh token when it runs out,
    and sign_out ends the sign-in in Home Assistant, which makes both worthless."""

    def __init__(self, url: str, tokens: dict, *, timeout: float = 20.0,
                 opener: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(url, tokens["access_token"], timeout=timeout, opener=opener)
        self._refresh: str | None = tokens.get("refresh_token") or None
        self._clock = clock
        self._until = 0.0
        self._lasts(tokens)

    def _lasts(self, tokens: dict) -> None:
        # A minute early, so a token never runs out halfway through a step.
        self._until = self._clock() + float(tokens.get("expires_in") or 1800) - 60

    def _renew(self) -> None:
        tokens = super().request("POST", "/auth/token", {"grant_type": "refresh_token", "client_id": self.url + "/",
                                                        "refresh_token": self._refresh}, auth=False, form=True)
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise HomeAssistantError("Home Assistant ended the installer's sign-in before the run was done")
        self._token = tokens["access_token"]
        self._lasts(tokens)

    def _bearer(self) -> str:
        if not self._refresh:
            raise HomeAssistantError("the installer has signed out of Home Assistant already")
        if self._clock() >= self._until:
            self._renew()
        return self._token

    def request(self, method: str, path: str, body: Any = None, *, auth: bool = True,
                form: bool = False) -> Any:
        try:
            return super().request(method, path, body, auth=auth, form=form)
        except Unauthorized:
            if not (auth and self._refresh):
                raise
            self._renew()
            return super().request(method, path, body, auth=auth, form=form)

    @property
    def signed_in(self) -> bool:
        return self._refresh is not None

    def sign_in_id(self) -> str | None:
        """The id Home Assistant knows this sign-in by. It is no secret: it only says which sign-in
        to end, and only the user it belongs to may end it."""
        for token in self.ws({"type": "auth/refresh_tokens"}) or []:
            if token.get("is_current"):
                return token.get("id")
        return None

    def end_other(self, sign_in_id: str) -> bool:
        """Ends another sign-in of the same user, one an earlier run left behind; False when Home
        Assistant has no such sign-in any more."""
        try:
            self.ws({"type": "auth/delete_refresh_token", "refresh_token_id": sign_in_id})
        except HomeAssistantError:
            return False
        return True

    def sign_out(self) -> None:
        """Ends the sign-in. Once it has, nothing can use it again, the installer included."""
        if not self._refresh:
            return
        HomeAssistant.request(self, "POST", "/auth/revoke", {"token": self._refresh}, auth=False, form=True)
        self._refresh = None
        self._token = ""


class _Frames:
    """Just enough of RFC 6455 for a short exchange: text frames out, masked; text frames in."""

    def __init__(self, sock: Any, buffered: bytes = b"", peer: str = "Home Assistant") -> None:
        self.sock = sock
        self.buf = buffered
        self.peer = peer

    def _take(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ValueError(f"{self.peer} closed the websocket")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, data: Any, opcode: int = 1) -> None:
        payload = json.dumps(data).encode("utf-8") if opcode == 1 else data
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 1 << 16:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> Any:
        message = b""
        while True:
            b0, b1 = self._take(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._take(2), "big")
            elif n == 127:
                n = int.from_bytes(self._take(8), "big")
            mask = self._take(4) if b1 & 0x80 else b""
            payload = self._take(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            opcode = b0 & 0x0F
            if opcode == 8:
                raise ValueError(f"{self.peer} closed the websocket")
            if opcode == 9:
                self.send(payload, opcode=10)
                continue
            if opcode not in (0, 1):
                continue
            message += payload
            if b0 & 0x80:
                return json.loads(message)


def ws_open(sock: Any, host: str, path: str, *, key: str | None = None,
            peer: str = "Home Assistant") -> _Frames:
    """The websocket handshake on a connected socket; returns its frames."""
    key = key or base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            raise ValueError(f"{peer} closed the connection before the websocket opened")
        head += chunk
    head, rest = head.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    if not lines[0].startswith("HTTP/1.1 101"):
        raise ValueError(f"no websocket: {lines[0]}")
    want = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
    accept = next((l.split(":", 1)[1].strip() for l in lines[1:] if l.lower().startswith("sec-websocket-accept:")), "")
    if accept != want:
        raise ValueError("the websocket answer does not match what was asked")
    return _Frames(sock, rest, peer)


def _ws_exchange(sock: Any, host: str, token: str, message: dict, *, key: str | None = None) -> Any:
    frames = ws_open(sock, host, "/api/websocket", key=key)
    if frames.recv().get("type") != "auth_required":
        raise ValueError("Home Assistant did not ask for the token")
    frames.send({"type": "auth", "access_token": token})
    if frames.recv().get("type") != "auth_ok":
        raise ValueError("Home Assistant did not accept the token")
    frames.send({"id": 1, **message})
    while True:
        answer = frames.recv()
        if answer.get("id") != 1 or answer.get("type") != "result":
            continue
        if not answer.get("success"):
            error = answer.get("error") or {}
            raise ValueError(f"{error.get('code', 'failed')}: {error.get('message', '')}".rstrip(": "))
        return answer.get("result")


# ----------------------------------------------------------------------------------- the ESPHome entry


def find_entry(ha: HomeAssistant, name: str) -> dict | None:
    """The ESPHome entry for this Show, by the title Home Assistant gave it (its display name)."""
    entries = ha.request("GET", "/api/config/config_entries/entry?domain=esphome") or []
    node = node_slug(name)
    for entry in entries:
        if entry.get("domain") != "esphome":
            continue
        title = str(entry.get("title") or "")
        if title.strip().lower() == name.strip().lower() or node_slug(title) == node:
            return entry
    return None


def _drop_flow(ha: HomeAssistant, flow_id: str) -> None:
    try:
        ha.request("DELETE", f"/api/config/config_entries/flow/{flow_id}")
    except HomeAssistantError:
        pass


class _Unreachable(HomeAssistantError):
    """Home Assistant could not connect to the Show (yet)."""


def _drive_flow(ha: HomeAssistant, result: dict, *, host: str | None, psk: str, name: str) -> str:
    """Answers ESPHome's config flow form by form until it makes the entry; returns its entry_id."""
    seen: set[str] = set()
    for _ in range(8):
        kind = result.get("type")
        flow_id = result.get("flow_id")
        if kind == "create_entry":
            entry = result.get("result") or {}
            entry_id = entry.get("entry_id") if isinstance(entry, dict) else None
            if not entry_id:
                found = find_entry(ha, name)
                entry_id = found and found.get("entry_id")
            if not entry_id:
                raise HomeAssistantError("Home Assistant made the entry but did not say which one")
            return entry_id
        if kind == "abort":
            reason = result.get("reason")
            if reason in ("already_configured", "already_in_progress"):
                found = find_entry(ha, name)
                if found:
                    return found["entry_id"]
                if reason == "already_in_progress":
                    raise _Unreachable("another ESPHome flow for this Show is open in Home Assistant")
            raise HomeAssistantError(f"Home Assistant stopped adding the Show: {reason}")
        if kind != "form" or not flow_id:
            raise HomeAssistantError(f"Home Assistant answered with an unexpected step ({kind})")
        step = str(result.get("step_id"))
        errors = result.get("errors") or {}
        if errors:
            _drop_flow(ha, flow_id)
            base = errors.get("base") if isinstance(errors, dict) else None
            if base in ("connection_error", "cannot_connect", "unknown_error"):
                raise _Unreachable(f"Home Assistant cannot connect to the Show ({base})")
            if base == "invalid_psk":
                raise HomeAssistantError("the Show refused the encryption key: it is not this unit's key")
            raise HomeAssistantError(f"Home Assistant refused step {step}: {errors}")
        if step in seen:
            _drop_flow(ha, flow_id)
            raise HomeAssistantError(f"Home Assistant asked for step {step} twice")
        seen.add(step)
        if step == "user":
            if not host:
                _drop_flow(ha, flow_id)
                raise _Unreachable("no address for the Show yet")
            answer: dict = {"host": host, "port": ESPHOME_PORT}
        elif step == "discovery_confirm":
            answer = {}
        elif step in ("encryption_key", "reauth_confirm", "reauth_encryption_removed_confirm"):
            answer = {"noise_psk": psk}
        else:
            _drop_flow(ha, flow_id)
            raise HomeAssistantError(f"Home Assistant asked something this tool does not answer (step {step}); "
                                     "finish it in Settings -> Devices & services")
        result = ha.request("POST", f"/api/config/config_entries/flow/{flow_id}", answer)
    raise HomeAssistantError("Home Assistant's ESPHome flow did not finish")


def add_device(ha: HomeAssistant, *, name: str, psk: str, host: str | None) -> tuple[str, bool]:
    """The Show's ESPHome entry, made when it is missing. Returns (entry_id, made_now).

    REST cannot list the discoveries Home Assistant has open (that is websocket only), so this always starts
    its own flow. Without an address it gives the Show's .local name, which Home Assistant resolves over mDNS
    when it shares a network with the Show. A discovery left open for the same unit closes on its own once
    this flow makes the entry."""
    found = find_entry(ha, name)
    if found:
        return found["entry_id"], False
    result = ha.request("POST", "/api/config/config_entries/flow", {"handler": "esphome", "show_advanced_options": False})
    return _drive_flow(ha, result, host=host or f"{node_slug(name)}.local", psk=psk, name=name), True


def allow_actions(ha: HomeAssistant, entry_id: str) -> bool:
    """Turns on "Allow the device to perform Home Assistant actions". Returns whether it changed."""
    if not _ENTRY_ID_RE.fullmatch(entry_id):
        raise HomeAssistantError("Home Assistant returned an entry id this tool does not trust")
    form = ha.request("POST", "/api/config/config_entries/options/flow", {"handler": entry_id})
    flow_id = form.get("flow_id") if isinstance(form, dict) else None
    if not flow_id or form.get("type") != "form":
        raise HomeAssistantError("the ESPHome entry has no options form")
    answer: dict = {}
    current = None
    for field in form.get("data_schema") or []:
        key = field.get("name")
        if key and "default" in field:
            answer[key] = field["default"]
        if key == "allow_service_calls":
            current = field.get("default")
    if current is None and not any(f.get("name") == "allow_service_calls" for f in form.get("data_schema") or []):
        ha.request("DELETE", f"/api/config/config_entries/options/flow/{flow_id}")
        raise HomeAssistantError("this Home Assistant's ESPHome options have no allow_service_calls")
    if current is True:
        ha.request("DELETE", f"/api/config/config_entries/options/flow/{flow_id}")
        return False
    answer["allow_service_calls"] = True
    done = ha.request("POST", f"/api/config/config_entries/options/flow/{flow_id}", answer)
    if not isinstance(done, dict) or done.get("type") != "create_entry":
        raise HomeAssistantError(f"Home Assistant did not save the ESPHome options: {done.get('errors') if isinstance(done, dict) else done}")
    return True


# ----------------------------------------------------------------------------- entities and actions


def entry_entities(ha: HomeAssistant, entry_id: str) -> list[str]:
    """The entity ids belonging to the ESPHome entry, through the template API (REST has no registry)."""
    if not _ENTRY_ID_RE.fullmatch(entry_id):
        raise HomeAssistantError("Home Assistant returned an entry id this tool does not trust")
    template = ("{% for e in integration_entities('esphome') if config_entry_id(e) == '" + entry_id
                + "' %}{{ e }}\n{% endfor %}")
    text = ha.request("POST", "/api/template", {"template": template})
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


# choose(label, current, options, names): names says how to show an option, where it is not its own name.
Chooser = Callable[[str, str, list[str], "dict[str, str]"], "str | None"]


def _preferred_pipeline(ha: HomeAssistant) -> str | None:
    """The name of the Assist pipeline Home Assistant has as preferred, the one the option "preferred" means."""
    try:
        listed = ha.ws({"type": "assist_pipeline/pipeline/list"})
    except HomeAssistantError:
        return None
    if not isinstance(listed, dict):
        return None
    want = listed.get("preferred_pipeline")
    return next((str(p.get("name")) for p in listed.get("pipelines") or []
                 if isinstance(p, dict) and want and p.get("id") == want and p.get("name")), None)


def _select(ha: HomeAssistant, entities: list[str], suffix: str, option: str | None,
            choose: Chooser | None = None) -> str:
    """Sets the select ending in suffix to option, or to what choose picks from the options it really has
    (the assistants this Home Assistant has, the wake words this Show has). Returns what happened, for the log."""
    matches = [e for e in entities if e.startswith("select.") and e.endswith(suffix)]
    if not matches:
        return f"no {suffix.lstrip('_')} select on this Show"
    entity = min(matches, key=len)
    state = ha.request("GET", f"/api/states/{entity}") or {}
    if state.get("state") in (None, "unavailable", "unknown"):
        return f"{entity} is not available yet; set it in Home Assistant once the Show is connected"
    if option is None:
        options = [str(o) for o in (state.get("attributes") or {}).get("options") or []]
        current = str(state.get("state") or "")
        names: dict[str, str] = {}
        if suffix == "_assistant" and choose and options:
            # "preferred" and the pipeline it points at do the same thing; say which that is.
            pref = _preferred_pipeline(ha)
            if pref and [o for o in options if o != "preferred"] == [pref]:
                return f"{entity} left at {current}: {pref} is the only assistant, and Home Assistant's default"
            if pref in options:
                names = {"preferred": f"Home Assistant's default, whichever that is (now {pref})",
                         pref: f"{pref} (default)"}
        option = choose(suffix.lstrip("_").replace("_", " "), current, options, names) if choose and options else None
        if option is None or option == current:
            return f"{entity} left at {current or 'its default'}"
    if state.get("state") == option:
        return f"{entity} is already {option}"
    options = (state.get("attributes") or {}).get("options") or []
    if option not in options:
        return f"{entity} has no option {option!r} (it has {', '.join(map(str, options)) or 'none yet'})"
    ha.request("POST", "/api/services/select/select_option", {"entity_id": entity, "option": option})
    return f"{entity} set to {option}"


def _selects_ready(ha: HomeAssistant, entities: list[str]) -> bool:
    """Whether the assistant and wake word selects are there with their real options. Right after the
    entry is added the wake word one is unavailable and offers only no_wake_word, until the Show has sent
    Home Assistant its configuration."""
    for suffix in ("_assistant", "_wake_word"):
        matches = [e for e in entities if e.startswith("select.") and e.endswith(suffix)]
        if not matches:
            continue
        state = ha.request("GET", f"/api/states/{min(matches, key=len)}") or {}
        if state.get("state") in (None, "unavailable", "unknown"):
            return False
    return True


# The name the house's assistant goes by (GENBU-109): the Assist pipeline the Shows talk to, which the rest
# of the assistant chain sends its requests to as well.
ASSISTANT_NAME = "Genbu Assistant"
_PIPELINE_SELECT = re.compile(r"-pipeline(?:_\d+)?$")
# What assist_pipeline/pipeline/update takes besides the id (PIPELINE_FIELDS in Home Assistant).
_PIPELINE_FIELDS = ("conversation_engine", "conversation_language", "language", "name", "stt_engine",
                    "stt_language", "tts_engine", "tts_language", "tts_voice", "wake_word_entity",
                    "wake_word_id", "prefer_local_intents", "acknowledge_media_id")


def _pipeline_selects(ha: HomeAssistant) -> dict[str, str]:
    """Every device's assistant select (Shows, voice satellites, ...) and the option it is on."""
    found = {}
    for e in ha.ws({"type": "config/entity_registry/list"}) or []:
        eid = str(e.get("entity_id") or "")
        if eid.startswith("select.") and _PIPELINE_SELECT.search(str(e.get("unique_id") or "")):
            state = ha.request("GET", f"/api/states/{eid}") or {}
            found[eid] = str(state.get("state") or "")
    return found


def _name_assistant(ha: HomeAssistant, entities: list[str], wanted: bool | None, choose: Chooser | None,
                    keep: Callable[[str, Any], str] | None) -> str:
    """Calls the Assist pipeline the Show talks to ASSISTANT_NAME, when wanted or choose says so. Devices
    pick a pipeline by its name, and Home Assistant turns a select whose name went away back to
    "preferred"; so every select that was on the old name is put on the new one, and none of them ends up
    talking to another assistant. The pipeline and those selects are saved first. Returns what happened."""
    matches = [e for e in entities if e.startswith("select.") and e.endswith("_assistant")]
    if not matches:
        return "no assistant select on this Show, so its assistant keeps its name"
    current = str((ha.request("GET", f"/api/states/{min(matches, key=len)}") or {}).get("state") or "")
    try:
        listed = ha.ws({"type": "assist_pipeline/pipeline/list"}) or {}
    except HomeAssistantError as exc:
        return f"the assistant keeps its name: Home Assistant did not list its pipelines ({exc})"
    pipelines = [p for p in listed.get("pipelines") or [] if isinstance(p, dict)]
    want_id = listed.get("preferred_pipeline") if current == "preferred" else None
    pipeline = next((p for p in pipelines if (p.get("id") == want_id if want_id else p.get("name") == current)), None)
    if pipeline is None:
        return f"the assistant keeps its name: no pipeline {current!r} in Home Assistant"
    old = str(pipeline.get("name") or "")
    if old == ASSISTANT_NAME:
        return f"the assistant is already called {ASSISTANT_NAME}"
    if any(p.get("name") == ASSISTANT_NAME for p in pipelines):
        return f"the assistant keeps the name {old}: another pipeline is already called {ASSISTANT_NAME}"
    if wanted is None and choose:
        wanted = choose("assistant name", "", ["yes", "no"],
                        {"yes": f"yes, {old} becomes {ASSISTANT_NAME}", "no": f"no, keep {old}"}) == "yes"
    if not wanted:
        return f"the assistant keeps the name {old}"
    if keep is None:
        return f"the assistant keeps the name {old}: there is nowhere to save it first"
    selects = {e: o for e, o in _pipeline_selects(ha).items() if o == old}
    saved = keep(f"assist-pipeline-{old}", {"pipeline": pipeline, "selects_on_it": sorted(selects)})
    ha.ws({"type": "assist_pipeline/pipeline/update", "pipeline_id": pipeline["id"],
           **{k: pipeline[k] for k in _PIPELINE_FIELDS if k in pipeline}, "name": ASSISTANT_NAME})
    for entity in sorted(selects):
        ha.request("POST", "/api/services/select/select_option", {"entity_id": entity, "option": ASSISTANT_NAME})
    on = f"; {', '.join(sorted(selects))} follow{'s' if len(selects) == 1 else ''} it" if selects else ""
    return f"the assistant {old} is now called {ASSISTANT_NAME}{on}; the old one is in {saved}"


def _switch_on(ha: HomeAssistant, entities: list[str], suffix: str) -> str:
    matches = [e for e in entities if e.startswith("switch.") and e.endswith(suffix)]
    if not matches:
        return f"no {suffix.lstrip('_')} switch on this Show"
    entity = min(matches, key=len)
    if (ha.request("GET", f"/api/states/{entity}") or {}).get("state") == "on":
        return f"{entity} is already on"
    ha.request("POST", "/api/services/switch/turn_on", {"entity_id": entity})
    return f"{entity} switched on"


def _areas(ha: HomeAssistant) -> dict[str, str]:
    """Home Assistant's rooms, id to name."""
    text = ha.request("POST", "/api/template", {"template":
                      "{% for a in areas() %}{{ a }}\t{{ area_name(a) }}\n{% endfor %}"})
    out = {}
    for line in str(text or "").splitlines():
        area, _, name = line.partition("\t")
        if area.strip():
            out[area.strip()] = name.strip() or area.strip()
    return out


def _room(ha: HomeAssistant, entities: list[str], room: str | None, choose: Chooser | None = None) -> str:
    """Puts the Show's device in room (an area's name or id), or in the one choose picks. The room is how
    the Show finds the speaker the room's music plays on, and how Assist knows where "the light" is."""
    known = sorted((e for e in entities if _ENTITY_ID_RE.fullmatch(e)), key=len)
    if not known:
        return "no entity of the Show in Home Assistant yet, so it was not put in a room"
    text = ha.request("POST", "/api/template", {"template":
                      "{% set d = device_id('" + known[0] + "') %}{{ d }}\t{{ area_id(d) if d else '' }}"})
    device, _, current = str(text or "").partition("\t")
    device, current = device.strip(), current.strip()
    if device in ("", "None"):
        return "Home Assistant has no device for the Show yet, so it was not put in a room"
    current = "" if current == "None" else current
    areas = _areas(ha)
    if not areas:
        return "this Home Assistant has no rooms (areas) yet; the Show is in none"
    if room is None:
        names = sorted(areas.values(), key=str.casefold)
        picked = choose("room", areas.get(current, ""), names, {}) if choose else None
        room = next((a for a, n in areas.items() if n == picked), None) if picked else None
        if room is None:
            return f"the Show left in {areas.get(current, 'no room')}"
    else:
        want = room.strip().casefold()
        found = [a for a, n in areas.items() if want in (a.casefold(), n.casefold())]
        if len(found) != 1:
            return f"no room {room!r} in Home Assistant (it has {', '.join(sorted(areas.values()))})"
        room = found[0]
    if room == current:
        return f"the Show is already in {areas[room]}"
    ha.ws({"type": "config/device_registry/update", "device_id": device, "area_id": room})
    return f"the Show put in {areas[room]}"


def slug_key(s: str, spell: bool = False) -> str:
    """s with only its letters and digits left, lower case, as the Show compares dashboard addresses with
    rooms (echod's slugKey): "living-room" and "Living Room" are one key. spell writes ä as ae."""
    out = []
    for c in s.lower():
        if "a" <= c <= "z" or "0" <= c <= "9":
            out.append(c)
        elif c == "ß":
            out.append("ss")
        elif c in "äöü":
            out.append({"ä": "a", "ö": "o", "ü": "u"}[c] + ("e" if spell else ""))
    return "".join(out)


def _room_board(ha: HomeAssistant, area: str, name: str) -> str | None:
    """The dashboard the Show would take as the room's, by the Show's own rules (a dashboard titled or
    addressed after the room, or a view with its name); None when there is none."""
    keys = {k for k in (slug_key(area), slug_key(name), slug_key(name, True)) if k}
    boards = ha.ws({"type": "lovelace/dashboards/list"}) or []
    for b in boards:
        path = str(b.get("url_path") or "")
        if str(b.get("title") or "").strip().casefold() == name.casefold() or \
                slug_key(path) in keys or slug_key(path.removeprefix("dashboard-")) in keys:
            return path
    for path in [None] + [b.get("url_path") for b in boards if b.get("mode") == "storage"]:
        try:
            config = ha.ws({"type": "lovelace/config", "url_path": path})
        except HomeAssistantError:
            continue  # never edited, or made by a strategy: nothing to look in
        views = config.get("views") if isinstance(config, dict) else None
        for view in views or []:
            if isinstance(view, dict) and str(view.get("title") or "").strip().casefold() == name.casefold():
                return f"{path or 'lovelace'}/{view.get('path') or ''}".rstrip("/")
    return None


# What a room's dashboard shows, in this order: what someone in the room would reach for. Doors, windows
# and motion are left out, since whoever looks at the Show is in the room and sees them. A media player's
# tile opens its controls; a switch's toggles it.
_ROOM_CARDS = (("climate", None, 2), ("sensor", "temperature", 2), ("sensor", "humidity", 2),
               ("light", None, 6), ("media_player", None, 2), ("cover", None, 4), ("switch", None, 4),
               ("fan", None, 2))
_ROOM_CARDS_MAX = 16


def _room_cards(ha: HomeAssistant, area: str, own_device: str) -> list[dict]:
    """Tile cards for what is in the area, the Show's own device left out."""
    devices = {d.get("id"): d.get("area_id") for d in ha.ws({"type": "config/device_registry/list"}) or []}
    here = []
    for e in ha.ws({"type": "config/entity_registry/list"}) or []:
        if e.get("entity_category") or e.get("hidden_by") or e.get("disabled_by"):
            continue
        if own_device and e.get("device_id") == own_device:
            continue
        if (e.get("area_id") or devices.get(e.get("device_id"))) == area:
            here.append(e.get("entity_id") or "")
    classes = {st.get("entity_id"): (st.get("attributes") or {}).get("device_class")
               for st in ha.request("GET", "/api/states") or []}
    cards: list[dict] = []
    for domain, device_class, most in _ROOM_CARDS:
        found = sorted(e for e in here if e.split(".", 1)[0] == domain and e in classes
                       and (device_class is None or classes[e] == device_class))
        for entity in found[:most]:
            card: dict = {"type": "tile", "entity": entity}
            if domain == "climate":
                card["features"] = [{"type": "target-temperature"}]
            cards.append(card)
    return cards[:_ROOM_CARDS_MAX]


def _room_dashboard(ha: HomeAssistant, entities: list[str], make: bool | None,
                    choose: Chooser | None = None) -> str:
    """Makes a small dashboard for the Show's room, the page a swipe in from the left goes on to, when the
    house has none and make says so (None: choose is asked). An existing one is never touched."""
    known = sorted((e for e in entities if _ENTITY_ID_RE.fullmatch(e)), key=len)
    if not known:
        return "no entity of the Show in Home Assistant yet, so no room dashboard"
    text = ha.request("POST", "/api/template", {"template":
                      "{% set d = device_id('" + known[0] + "') %}{{ d }}\t{{ area_id(d) if d else '' }}"})
    device, _, area = str(text or "").partition("\t")
    device, area = device.strip(), area.strip()
    if device == "None":
        device = ""
    if area in ("", "None"):
        return "the Show is in no room, so it has no room dashboard"
    name = _areas(ha).get(area, area)
    found = _room_board(ha, area, name)
    if found:
        return f"{name} has a dashboard already ({found}); the Show goes on to it"
    if make is None:
        picked = choose("room dashboard", "", ["yes", "no"],
                        {"yes": f"yes, a small one for {name}", "no": "no"}) if choose else None
        make = picked == "yes"
    if not make:
        return f"no room dashboard for {name}"
    cards = _room_cards(ha, area, device)
    if not cards:
        return (f"nothing in {name} worth a room dashboard (no heating, lights, players, blinds, switches "
                "or fans), so none made")
    url_path = "dashboard-" + (slug_key(name, True) or slug_key(area) or "room")
    ha.ws({"type": "lovelace/dashboards/create", "url_path": url_path, "title": name, "icon": "mdi:sofa",
           "require_admin": False, "show_in_sidebar": True, "mode": "storage"})
    ha.ws({"type": "lovelace/config/save", "url_path": url_path, "config": {
        "title": name,
        "views": [{"title": name, "path": "room", "type": "sections", "max_columns": 3,
                   "sections": [{"type": "grid", "cards": cards}]}]}})
    return f"room dashboard {url_path} made for {name}, {len(cards)} cards"


def _entry_devices(ha: HomeAssistant, entry_id: str) -> list[str]:
    """The ids of the devices the ESPHome entry has."""
    return [str(d["id"]) for d in ha.ws({"type": "config/device_registry/list"}) or []
            if isinstance(d, dict) and d.get("id") and entry_id in (d.get("config_entries") or [])]


def node_name(ha: HomeAssistant, entry_id: str) -> str | None:
    """The ESPHome node name Home Assistant has for the entry, which names the Show's actions. It is not
    the installed name once the Show was renamed on its screen; None when Home Assistant does not say.
    Only this one value is taken from the diagnostics."""
    try:
        diag = ha.request("GET", f"/api/diagnostics/config_entry/{entry_id}")
    except HomeAssistantError:
        return None
    node = diag
    for key in ("data", "config", "data", "device_name"):
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, str) and node.strip() else None


def _services(ha: HomeAssistant) -> set[str]:
    for domain in ha.request("GET", "/api/services") or []:
        if domain.get("domain") == "esphome":
            return set((domain.get("services") or {}).keys())
    return set()


# ------------------------------------------------------------------------------------------- calendars


@dataclass(frozen=True)
class CalDAVAccount:
    """A calendar account for Home Assistant's CalDAV integration. The password goes to Home Assistant's
    own form and nowhere else."""

    url: str
    username: str
    password: str = field(default="", repr=False)
    verify_ssl: bool = True


def check_caldav_url(url: str) -> str:
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("give the whole address, like https://cloud.example.org/remote.php/dav")
    return url


def _calendars(ha: HomeAssistant) -> dict[str, str]:
    """The calendar entities Home Assistant has: entity id -> the name it shows."""
    found = {}
    for st in ha.request("GET", "/api/states") or []:
        entity = str(st.get("entity_id") or "")
        if entity.startswith("calendar.") and _ENTITY_ID_RE.fullmatch(entity):
            found[entity] = str((st.get("attributes") or {}).get("friendly_name") or entity)
    return found


def add_caldav(ha: HomeAssistant, account: CalDAVAccount) -> tuple[str, bool]:
    """Adds the account through Home Assistant's CalDAV form, which tries it before it saves it.
    Returns what happened, and whether the account is new; raises when Home Assistant refused it."""
    result = ha.request("POST", "/api/config/config_entries/flow", {"handler": "caldav", "show_advanced_options": False})
    flow_id = result.get("flow_id") if isinstance(result, dict) else None
    if not flow_id or result.get("type") != "form" or result.get("step_id") != "user":
        if flow_id:
            _drop_flow(ha, flow_id)
        raise HomeAssistantError("Home Assistant's CalDAV integration did not ask for an account")
    done = ha.request("POST", f"/api/config/config_entries/flow/{flow_id}", {
        "url": account.url, "username": account.username, "password": account.password,
        "verify_ssl": account.verify_ssl})
    kind = done.get("type") if isinstance(done, dict) else None
    if kind == "create_entry":
        return f"CalDAV account {account.username} added", True
    if kind == "abort" and done.get("reason") == "already_configured":
        return f"CalDAV account {account.username} was in Home Assistant already, so it stays as it is", False
    if kind == "form":
        _drop_flow(ha, flow_id)
    base = ((done.get("errors") or {}).get("base") if isinstance(done, dict) else None) or \
        (done.get("reason") if isinstance(done, dict) else None)
    raise HomeAssistantError({
        "invalid_auth": "the calendar server refused the user name or password",
        "cannot_connect": f"Home Assistant could not reach a calendar at {account.url}",
    }.get(base, f"Home Assistant did not add the calendar account ({base or kind})"))


def _calendar_cards(node: Any) -> list[dict]:
    """The Jarvis calendar cards anywhere in a dashboard config, however deep in stacks and sections."""
    found = []
    if isinstance(node, dict):
        if node.get("type") == "custom:jarvis-calendar-card":
            found.append(node)
        for value in node.values():
            found += _calendar_cards(value)
    elif isinstance(node, list):
        for value in node:
            found += _calendar_cards(value)
    return found


def _card_calendars(card: dict) -> list[str]:
    """The calendars a Jarvis calendar card shows: its entities, and its entity."""
    listed = card.get("entities")
    listed = [listed] if isinstance(listed, str) else list(listed or [])
    return [str(c) for c in listed + [card.get("entity")] if c]


def _point_calendar_cards(ha: HomeAssistant, chosen: list[str], have: set[str],
                          keep: Callable[[str, Any], str] | None) -> list[str]:
    """Points every Jarvis calendar card that has a calendar Home Assistant does not have (the dashboard
    ships with calendar.personal as a stand-in) at the chosen calendars: one goes in entity, more in
    entities. A card whose calendars are all there is left alone. Returns the dashboards changed."""
    changed = []
    boards = ha.ws({"type": "lovelace/dashboards/list"}) or []
    for path in [None] + [b.get("url_path") for b in boards if b.get("mode") == "storage"]:
        try:
            config = ha.ws({"type": "lovelace/config", "url_path": path})
        except HomeAssistantError:
            continue
        cards = [c for c in _calendar_cards(config) if not _card_calendars(c) or
                 any(cal not in have for cal in _card_calendars(c))]
        if not cards:
            continue
        if keep:
            keep(f"dashboard-{path or 'lovelace'}", json.loads(json.dumps(config)))
        for card in cards:
            if len(chosen) == 1:
                card["entity"] = chosen[0]
                card.pop("entities", None)
            else:
                card["entities"] = list(chosen)
                card.pop("entity", None)
        ha.ws({"type": "lovelace/config/save", "url_path": path, "config": config})
        changed.append(path or "lovelace")
    return changed


def _check_calendars(ha: HomeAssistant, chosen: list[str], progress: Callable[[str], None]) -> None:
    """Asks Home Assistant for each chosen calendar's events over one minute from now, to see that it
    answers. What comes back is thrown away unread: the installer has no business with the events."""
    now = time.time()
    span = "start={}&end={}".format(*(urllib.parse.quote(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)))
                                      for t in (now, now + 60)))
    for calendar in chosen:
        try:
            ha.request("GET", f"/api/calendars/{urllib.parse.quote(calendar)}?{span}")
        except HomeAssistantError as exc:
            progress(f"WARN: {calendar} did not answer ({exc}); the Show and the dashboard show nothing "
                     "from it until it does (docs/calendars.md)")
        else:
            progress(f"{calendar} answers")


def _caldav_entries(ha: HomeAssistant) -> list[dict]:
    """The CalDAV accounts Home Assistant has in its UI (Settings -> Devices & services)."""
    entries = ha.request("GET", "/api/config/config_entries/entry?domain=caldav") or []
    return [e for e in entries if e.get("domain") == "caldav"]


def _entities_of(ha: HomeAssistant, entry_ids: set[str]) -> set[str]:
    return {str(e.get("entity_id")) for e in ha.ws({"type": "config/entity_registry/list"}) or []
            if e.get("config_entry_id") in entry_ids}


def _replace_caldav(ha: HomeAssistant, old: list[dict], progress: Callable[[str], None]) -> None:
    """Removes the CalDAV accounts in old once the new one is in, and gives the new calendars the ids
    the old ones had (calendar.personal_2 becomes calendar.personal again), so the dashboards and
    Shows that use them keep working."""
    gone = {c for c in _entities_of(ha, {str(e.get("entry_id")) for e in old}) if c.startswith("calendar.")}
    for entry in old:
        ha.request("DELETE", f"/api/config/config_entries/entry/{entry.get('entry_id')}")
        progress(f"CalDAV account {entry.get('title') or entry.get('entry_id')} removed from Home Assistant")
    have = _calendars(ha)
    for calendar in sorted(have):
        base = re.sub(r"_\d+$", "", calendar)
        if base == calendar or base not in gone or base in have:
            continue
        try:
            ha.ws({"type": "config/entity_registry/update", "entity_id": calendar, "new_entity_id": base})
        except HomeAssistantError as exc:
            progress(f"WARN: {calendar} keeps its id ({exc}); rename it in Settings -> Entities")
            continue
        progress(f"{calendar} is {base} again, the id the old account's calendar had")


def _set_up_calendars(ha: HomeAssistant, prefix: str, opts: "DeployOptions", choose: Chooser | None,
                      progress: Callable[[str], None], sleep: Callable[[float], None],
                      clock: Callable[[], float]) -> None:
    """A CalDAV account for Home Assistant when one is given or wanted, then the calendars this Show
    shows, a quick check that each of them answers, and the dashboard's calendar card pointed at them. An account never comes in
    next to one Home Assistant has: it replaces it, or the step is skipped."""
    have = _calendars(ha)
    old = _caldav_entries(ha)
    registered = {str(e.get("entity_id")) for e in ha.ws({"type": "config/entity_registry/list"}) or []}
    in_yaml = sorted(c for c in have if c not in registered)  # configuration.yaml's have no registry entry
    account = opts.caldav
    asking = account is None and opts.ask_caldav and choose and opts.calendars is None
    if in_yaml and not old and (account is not None or asking):
        # Home Assistant can't drop those over its API, and a new account next to them would bring every
        # calendar in twice, as calendar.<name>_2.
        progress(f"Home Assistant has {', '.join(in_yaml)} from configuration.yaml, so no calendar account "
                 "is added. To replace it, remove the calendar: block there, restart Home Assistant and run "
                 "this again")
        account = None
    elif asking:
        if old:
            accounts = ", ".join(str(e.get("title") or e.get("entry_id")) for e in old)
            its = "its" if len(old) == 1 else "their"
            names = {"replace": f"replace the CalDAV account {accounts} with another one",
                     "skip": f"skip, keep {accounts} and {its} calendars: " + ", ".join(sorted(set(have.values())))}
            wanted = choose("calendar account", "", ["replace", "skip"], names) == "replace"
        else:
            names = {"yes": "yes, a CalDAV account (Nextcloud, iCloud, mailbox.org ...)", "no": "no"}
            wanted = choose("calendar account", "", ["yes", "no"], names) == "yes"
        if wanted:
            account = opts.ask_caldav()
    if account is not None:
        try:
            said, added = add_caldav(ha, account)
            progress(said)
        except HomeAssistantError as exc:
            progress(f"WARN: {exc}; add it in Settings -> Devices & services -> CalDAV (docs/calendars.md)")
        else:
            until = clock() + 30
            while added and not set(_calendars(ha)) - set(have) and clock() < until:
                sleep(3)
            now = _calendars(ha)
            new = sorted(set(now) - set(have))
            progress(f"its calendars in Home Assistant: {', '.join(new)}" if new else
                     "the account has no calendar Home Assistant can see yet")
            if old and added:
                _replace_caldav(ha, old, progress)
                now = _calendars(ha)
            have = now
    if not have:
        progress("Home Assistant has no calendar, so the Show shows none; docs/calendars.md says how to add one")
        return
    if opts.calendars:
        chosen = [c.strip() for c in opts.calendars.split(",") if c.strip()]
        unknown = [c for c in chosen if c not in have]
        if unknown:
            progress(f"WARN: Home Assistant has no {', '.join(unknown)}; the Show is given "
                     + ("the others" if len(unknown) < len(chosen) else "no calendar"))
        chosen = [c for c in chosen if c in have]
    elif opts.calendars is None and choose:
        options = (["all"] if len(have) > 1 else []) + sorted(have)
        names = {"all": f"all {len(have)} of them", **{c: f"{n} ({c})" for c, n in have.items() if n != c}}
        picked = choose("calendars", "", options, names)
        chosen = sorted(have) if picked == "all" else [picked] if picked else []
    else:
        chosen = []
    if not chosen:
        progress("the Show's calendars left as they are")
        return
    action = f"{prefix}_calendar_sources"
    if action in _services(ha):
        ha.request("POST", f"/api/services/esphome/{action}", {"calendars": ", ".join(chosen)})
        progress(f"the Show shows {', '.join(chosen)}")
    else:
        progress(f"WARN: the Show has no esphome.{action} action yet, so it was not given its calendars")
    _check_calendars(ha, chosen, progress)
    try:
        boards = _point_calendar_cards(ha, chosen, set(have), opts.keep_replaced)
    except HomeAssistantError as exc:
        progress(f"WARN: the dashboard's calendar card was not changed: {exc}")
        return
    for board in boards:
        progress(f"the calendar card on {board} now shows {', '.join(chosen)}")


def show_username(name: str) -> str:
    return "show_" + service_prefix(name)


# What Home Assistant's login flow says when it turns a sign-in down, in words.
_LOGIN_ERRORS = {"invalid_auth": "wrong username or password", "invalid_code": "wrong code",
                 "too_many_retry": "too many wrong codes", "login_expired": "it took too long",
                 "user_not_allowed": "that user may not sign in from here"}


def _login_flow(ha: HomeAssistant, username: str, password: str,
                code: Callable[[bool], str | None] | None = None) -> dict:
    """Home Assistant's own login, as a browser does it; returns its access and refresh tokens.
    code is asked for the two-factor code when the user has two-factor sign-in on (with True after a
    wrong one); None from it, or no code at all, gives up."""
    client = ha.url + "/"
    flow = ha.request("POST", "/auth/login_flow", {"client_id": client, "handler": ["homeassistant", None],
                                                   "redirect_uri": client}, auth=False) or {}
    flow_id = flow.get("flow_id")
    step = ha.request("POST", f"/auth/login_flow/{flow_id}",
                      {"client_id": client, "username": username, "password": password}, auth=False) or {}
    asked = False
    while step.get("type") == "form" and step.get("step_id") in ("select_mfa_module", "mfa"):
        if step["step_id"] == "select_mfa_module":
            options = next((f.get("options") for f in step.get("data_schema") or []
                            if f.get("name") == "multi_factor_auth_module"), None) or {}
            first = next(iter(options), None)
            module = first[0] if isinstance(first, (list, tuple)) else first
            if not module:
                raise NotSignedIn("Home Assistant asked for a two-factor method but offered none")
            body = {"client_id": client, "multi_factor_auth_module": module}
        else:
            answer = code(asked) if code else None
            if not answer:
                raise NotSignedIn("no two-factor code")
            asked = True
            body = {"client_id": client, "code": answer}
        step = ha.request("POST", f"/auth/login_flow/{step.get('flow_id') or flow_id}", body, auth=False) or {}
    if step.get("type") != "create_entry":
        reasons = list((step.get("errors") or {}).values()) or [step.get("reason") or step.get("type") or "no answer"]
        raise NotSignedIn(", ".join(_LOGIN_ERRORS.get(str(r), str(r)) for r in reasons))
    tokens = ha.request("POST", "/auth/token", {"grant_type": "authorization_code", "code": step.get("result"),
                                                "client_id": client}, auth=False, form=True)
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise HomeAssistantError(f"{username!r} signed in but Home Assistant gave no token")
    return tokens


def sign_in(url: str, username: str, password: str, *, code: Callable[[bool], str | None] | None = None,
            timeout: float = 20.0, opener: Callable[..., Any] | None = None,
            clock: Callable[[], float] = time.monotonic) -> SignedIn:
    """An admin signed in for this run. Raises NotSignedIn for a wrong password or code, and for a
    user who is not an admin (whose sign-in is ended again right away)."""
    tokens = _login_flow(HomeAssistant(url, "", timeout=timeout, opener=opener), username, password, code)
    signed = SignedIn(url, tokens, timeout=timeout, opener=opener, clock=clock)
    try:
        me = signed.ws({"type": "auth/current_user"}) or {}
        if not me.get("is_admin"):
            raise NotSignedIn(f"{username!r} is not an admin in Home Assistant, and adding the Show needs one")
    except BaseException:
        try:
            signed.sign_out()
        except HomeAssistantError:
            pass
        raise
    return signed


def _sign_in(ha: HomeAssistant, username: str, password: str) -> dict:
    """The Show's own user signing in once, to make its token."""
    try:
        return _login_flow(ha, username, password)
    except NotSignedIn as exc:
        raise HomeAssistantError(f"the Show's user {username!r} could not sign in ({exc})") from None


def make_show_user(ha: HomeAssistant, name: str, *, progress: Callable[[str], None] = print) -> str:
    """Makes the Show a Home Assistant user of its own, not an admin and only reachable from home,
    and returns a new long-lived token of that user. Its password is random and kept nowhere, so
    nobody signs in as it; the token is what the Show uses. Its older tokens are removed."""
    username = show_username(name)
    password = secrets.token_urlsafe(32)
    users = ha.ws({"type": "config/auth/list"}) or []
    user = next((u for u in users if (u.get("username") or "").lower() == username), None)
    if user is not None:
        if user.get("is_owner") or user.get("system_generated") or \
                "system-admin" in (user.get("group_ids") or []):
            raise HomeAssistantError(f"the Home Assistant user {username!r} is an admin or Home Assistant's "
                                     "own; the Show does not take it over")
        if not user.get("is_active", True):
            raise HomeAssistantError(f"the Home Assistant user {username!r} is switched off")
        try:
            ha.ws({"type": "config/auth_provider/homeassistant/admin_change_password", "user_id": user["id"],
                   "password": password})
            progress(f"Home Assistant user {username!r} is already there")
        except HomeAssistantError as exc:
            # Only the owner may set another user's password. Any admin may remove a user and make it
            # again, which costs the Show nothing: its old tokens end either way.
            if "unauthorized" not in str(exc).lower():
                raise
            ha.ws({"type": "config/auth/delete", "user_id": user["id"]})
            progress(f"Home Assistant user {username!r} made again (only the owner may change its password)")
            user = None
    if user is None:
        made = ha.ws({"type": "config/auth/create", "name": name, "group_ids": ["system-users"],
                      "local_only": True})
        user_id = made["user"]["id"]
        try:
            ha.ws({"type": "config/auth_provider/homeassistant/create", "user_id": user_id,
                   "username": username, "password": password})
        except HomeAssistantError:
            ha.ws({"type": "config/auth/delete", "user_id": user_id})
            raise
        progress(f"Home Assistant user {username!r} made (not an admin, home network only)")

    tokens = _sign_in(ha, username, password)
    try:
        own = ha.as_user(tokens["access_token"])
        label = "Jarvis Show " + time.strftime("%Y-%m-%d %H:%M:%S")
        token = own.ws({"type": "auth/long_lived_access_token", "client_name": label, "lifespan": 3650})
        if not isinstance(token, str) or not token:
            raise HomeAssistantError(f"Home Assistant made no token for {username!r}")
        old = [t["id"] for t in own.ws({"type": "auth/refresh_tokens"}) or []
               if t.get("type") == "long_lived_access_token" and t.get("client_name") != label and t.get("id")]
        for token_id in old:
            own.ws({"type": "auth/delete_refresh_token", "refresh_token_id": token_id})
        progress(f"new token for {username!r}" + (f", {len(old)} older one(s) removed" if old else ""))
    finally:
        if tokens.get("refresh_token"):
            try:
                ha.request("POST", "/auth/revoke", {"token": tokens["refresh_token"]}, auth=False, form=True)
            except HomeAssistantError as exc:
                progress(f"WARN: the installer's own sign-in as {username!r} is left in Home Assistant: {exc}")
    return token


@dataclass(frozen=True)
class DeviceSettings:
    """What the Show itself is given through its actions. Any of them may be missing."""

    dashcast: str | None = None
    dashcast_key: str | None = None
    ha_url: str | None = None
    ha_token: str | None = None
    music_assistant: str | None = None
    # Makes the Show's own Music Assistant user, for lyrics, and returns its token, or None when that
    # failed (and said why). Given the Show's name; called only once the Show can take the token.
    music_assistant_login: Callable[[str], str | None] | None = None
    # The Show gets a Home Assistant user of its own (make_show_user) and that user's token instead of
    # ha_token, and its streamed dashboard is shown as that user.
    own_user: bool = False


@dataclass(frozen=True)
class DeployOptions:
    name: str
    psk: str
    host: str | None = None
    wake_word: str | None = None   # None: keep the Show's, or ask through choose
    assistant: str | None = None   # the Assist pipeline the Show talks to; None as above
    room: str | None = None        # the area it is in, by name or id; None as above
    room_dashboard: bool | None = None  # make a small one for the room if it has none; None: ask
    voice_extras: bool | None = None    # the house's answers and music sentences (voice_kit); None: ask
    assistant_name: bool | None = None  # call the Show's pipeline ASSISTANT_NAME; None: ask
    calendars: str | None = None   # the calendar entities the Show shows, "a, b"; '': leave them; None: ask
    caldav: CalDAVAccount | None = None  # a calendar account to add to Home Assistant, without asking
    ask_caldav: Callable[[], CalDAVAccount | None] | None = None  # asks for one, once choose says yes
    # Saves an automation or script config before voice_kit replaces it: (what, config) -> where it went.
    keep_replaced: Callable[[str, Any], str] | None = None
    # A Show renamed since it was installed: move its entity ids and what uses them to the new name
    # (rename.follow); None: ask through choose, and leave them without it. renamed hears the new name.
    rename_entities: bool | None = None
    renamed: Callable[[str], None] | None = None
    settings: DeviceSettings = DeviceSettings()
    wait_seconds: float = 300.0


def _follow_rename(ha: HomeAssistant, entry_id: str, opts: DeployOptions, node: str,
                   progress: Callable[[str], None], choose: Chooser | None) -> str | None:
    """Moves the Show's entities and what uses them to its new name, when opts or choose say so; returns
    the new name then. The name is the one its device has in Home Assistant, as the room sees it."""
    from . import rename
    devices = ha.ws({"type": "config/device_registry/list"}) or []
    entities = ha.ws({"type": "config/entity_registry/list"}) or []
    own = rename.show_devices(devices, entities, entry_id)
    name = rename.device_name(own[0]) if own else ""
    if rename.slugify(name) != rename.slugify(node):
        name = node
    wanted = opts.rename_entities
    if wanted is None and choose:
        was, now = rename.slugify(opts.name), rename.slugify(name)
        wanted = choose("new name", "", ["yes", "no"], {"yes": f"yes, {was}_... becomes {now}_...",
                                                         "no": f"no, keep {was}_..."}) == "yes"
    if not wanted:
        progress(f"its entities keep the name {opts.name!r}")
        return None
    try:
        rename.follow(ha, entry_id, opts.name, name, keep=opts.keep_replaced, progress=progress)
    except HomeAssistantError as exc:
        progress(f"WARN: the move to the new name stopped part way: {exc}")
        return None
    if opts.renamed:
        opts.renamed(name)
    return name


def deploy(ha: HomeAssistant, opts: DeployOptions, *, progress: Callable[[str], None] = print,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           find_host: Callable[[], str | None] | None = None, choose: Chooser | None = None) -> str:
    """Takes the Show into Home Assistant and sets it up; returns the ESPHome entry_id."""
    deadline = clock() + opts.wait_seconds
    host = opts.host
    last = ""
    while True:
        try:
            entry_id, made = add_device(ha, name=opts.name, psk=opts.psk, host=host)
            break
        except _Unreachable as exc:
            if clock() >= deadline:
                raise HomeAssistantError(f"{exc}; gave up after {int(opts.wait_seconds)} s "
                                         "(is it on Wi-Fi, and on a network Home Assistant reaches?)") from None
            if str(exc) != last:
                progress(f"waiting: {exc}")
                last = str(exc)
            sleep(10)
            if find_host is not None and not opts.host:
                host = find_host() or host
    progress(f"ESPHome entry {'added' if made else 'already there'}: {opts.name}")
    progress("device actions " + ("allowed" if allow_actions(ha, entry_id) else "were already allowed"))

    node = node_name(ha, entry_id)
    if node and service_prefix(node) != service_prefix(opts.name):
        progress(f"Home Assistant knows the Show as {node!r} (renamed since it was installed as {opts.name!r})")
        new_name = _follow_rename(ha, entry_id, opts, node, progress, choose)
        if new_name:
            opts = replace(opts, name=new_name)
    prefix = service_prefix(node or opts.name)
    wanted = []
    s = opts.settings
    if s.dashcast and s.dashcast_key:
        wanted.append((f"{prefix}_dashboard_server", {"address": s.dashcast, "key": s.dashcast_key},
                       f"DashCast server {s.dashcast}"))
    if s.ha_url and s.own_user:
        # The token is made only once the Show can take it, since a new one ends the old ones.
        wanted.append((f"{prefix}_home_assistant", None, f"Home Assistant access {s.ha_url} as its own user"))
    elif s.ha_url and s.ha_token:
        wanted.append((f"{prefix}_home_assistant", {"url": s.ha_url, "token": s.ha_token},
                       f"Home Assistant access {s.ha_url}"))
    if s.music_assistant:
        wanted.append((f"{prefix}_sendspin_server", {"ip": s.music_assistant},
                       f"Music Assistant server {s.music_assistant}"))
    if s.music_assistant and s.music_assistant_login:
        wanted.append((f"{prefix}_music_assistant", None, "Music Assistant access for lyrics"))

    # The options change reloads the entry, so the entities and actions come back a moment later.
    entities: list[str] = []
    want_selects = bool(opts.wake_word or opts.assistant or choose)
    want_entities = want_selects or bool(s.music_assistant or opts.room or opts.room_dashboard or s.own_user
                                         or opts.voice_extras)
    while True:
        entities = entry_entities(ha, entry_id) if want_entities else []
        services = _services(ha) if wanted else set()
        missing = [svc[len(prefix) + 1:] for svc, _, _ in wanted if svc not in services]
        selects = not want_selects or _selects_ready(ha, entities)
        ready = (not want_entities or any(e.startswith("select.") for e in entities)) and not missing and selects
        if ready or clock() >= deadline:
            break
        left = max(0, int(deadline - clock()))
        waiting = (f"its actions ({', '.join(missing)})" if missing else
                   "its assistant and wake words" if not selects else "its entities")
        if waiting != last:
            progress(f"waiting for the Show to give Home Assistant {waiting}; up to {left} s more")
            last = waiting
        sleep(5)

    if opts.assistant or choose:
        progress(_select(ha, entities, "_assistant", opts.assistant, choose))
    if opts.assistant_name or (opts.assistant_name is None and choose):
        progress(_name_assistant(ha, entities, opts.assistant_name, choose, opts.keep_replaced))
    if opts.wake_word or choose:
        progress(_select(ha, entities, "_wake_word", opts.wake_word, choose))
    from . import labels
    labels.mark(ha, progress, node=node or node_slug(opts.name), devices=_entry_devices(ha, entry_id),
                sleep=sleep, clock=clock)
    if opts.room or choose:
        progress(_room(ha, entities, opts.room, choose))
    if opts.room_dashboard or (opts.room_dashboard is None and choose):
        progress(_room_dashboard(ha, entities, opts.room_dashboard, choose))
    extras = opts.voice_extras
    if extras is None and choose:
        extras = choose("voice extras", "", ["yes", "no"], {}) == "yes"
    if extras:
        from . import voice_kit
        try:
            voice_kit.set_up(ha, opts.name, entities, choose=choose, keep=opts.keep_replaced, progress=progress,
                             node=node or node_slug(opts.name), sleep=sleep, clock=clock)
        except HomeAssistantError as exc:
            progress(f"WARN: the voice extras stopped part way: {exc}")
    services = _services(ha) if wanted else set()
    own_given = False
    for svc, data, what in wanted:
        if svc not in services:
            progress(f"WARN: the Show has no esphome.{svc} action in Home Assistant yet, so {what} was not "
                     "given; run this again once the Show is online")
            continue
        if data is None and svc == f"{prefix}_music_assistant":
            token = s.music_assistant_login(node or opts.name)
            if not token:
                progress(f"WARN: {what} not set, so lyrics won't work on the Show")
                continue
            data = {"token": token}
        elif data is None:
            data = {"url": s.ha_url, "token": make_show_user(ha, opts.name, progress=progress)}
            own_given = True
        ha.request("POST", f"/api/services/esphome/{svc}", data)
        progress(f"{what} given to the Show")
    if s.music_assistant and f"{prefix}_sendspin_server" in services:
        progress(_switch_on(ha, entities, "_sendspin"))
    if own_given:
        progress(_switch_on(ha, entities, "_own_user"))
    if opts.calendars != "" and (opts.calendars or opts.caldav or choose):
        try:
            _set_up_calendars(ha, prefix, opts, choose, progress, sleep, clock)
        except HomeAssistantError as exc:
            progress(f"WARN: the calendars stopped part way: {exc}")
    return entry_id
