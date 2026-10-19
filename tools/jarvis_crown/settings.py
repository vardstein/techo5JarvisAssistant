"""What a Show is set up with, asked for once at the start of an install.

Wi-Fi, Home Assistant, the DashCast server and the Music Assistant server: each comes from its switch
first, then (secrets only) from the desktop keyring, then from a question at the terminal. The
addresses, and the usernames signed in with, are remembered in ~/.config/jarvis-show/defaults.json,
so the second Show is mostly Enter. Secrets are never remembered by this tool, never put in an
argument list, and reach the installer only as files readable by the owner, in a folder that is gone
when the install ends.

For Home Assistant and Music Assistant the installer signs in as an admin for the run, with a
username and password (sign_ins.py), and signs out when the run ends. An admin's long-lived token,
from a file or the keyring, does the same job for runs nobody answers questions in.

Keyring entries are looked up with secret-tool (or $JARVIS_SHOW_SECRET_TOOL) under
    application jarvis-show secret <ha-token | ha-admin-token | dashcast-key | music-assistant-token>
    application jarvis-show secret wifi network <ssid>
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
import getpass
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
from typing import Callable, Iterator

from jarvis_crown import home_assistant as ha_api
from jarvis_crown import music_assistant as ma_api
from jarvis_crown import ui
from jarvis_crown.sign_ins import SignIns
from jarvis_crown.home_assistant import (check_dashcast_key, check_token, normalize_dashcast,
                                         normalize_ha_url, normalize_music_assistant)

REMEMBERED = ("wifi", "ha_url", "dashcast", "music_assistant", "ha_user", "music_assistant_user")


class SettingsError(ValueError):
    """A setting that was given is not one the Show would accept."""


@dataclass(frozen=True)
class Settings:
    # The secrets stay out of repr(), so a traceback or a debug print of the settings shows none of them.
    wifi: str | None = None
    wifi_passphrase: str | None = field(default=None, repr=False)
    ha_url: str | None = None
    ha_token: str | None = field(default=None, repr=False)
    dashcast: str | None = None
    dashcast_key: str | None = field(default=None, repr=False)
    music_assistant: str | None = None
    ha_admin_token: str | None = field(default=None, repr=False)
    # A Music Assistant admin's token, used once to make the Show a Music Assistant user of its own (for
    # lyrics) and, with --music-assistant-local-metadata, to switch its online lookups off
    # (music_assistant.py). It is kept nowhere.
    music_assistant_token: str | None = field(default=None, repr=False)
    music_assistant_local_metadata: bool = False
    # The TuneIn username Music Assistant's TuneIn provider is added with (music_assistant.add_tunein).
    music_assistant_tunein: str | None = None
    # Root's password, which the Show's USB console asks for. Only its hash reaches the Show.
    root_password: str | None = field(default=None, repr=False)
    # No ha_token: the Show is made a Home Assistant user of its own when it is added, and gets that
    # user's token then. So it is whenever there is an admin, unless --ha-token-file says otherwise.
    own_ha_user: bool = False
    # The admins signed in as for this run (sign_ins.py); the usernames are remembered, nothing else.
    ha_user: str | None = None
    music_assistant_user: str | None = None
    # That Home Assistant sign-in, a home_assistant.SignedIn, which the run ends.
    ha_sign_in: object | None = field(default=None, compare=False, repr=False)

    @property
    def ha_admin(self) -> bool:
        """Whether the Show can be added to Home Assistant by this run."""
        return bool(self.ha_url and (self.ha_sign_in or self.ha_admin_token))

    def summary(self) -> list[str]:
        """What will be set, without a single secret in it."""
        out = [f"Wi-Fi: {self.wifi}" if self.wifi else "Wi-Fi: picked on the Show's screen"]
        out.append("root password: set" if self.root_password else "root password: none (the USB console needs none)")
        if self.ha_url and self.own_ha_user:
            out.append(f"Home Assistant: {self.ha_url}, as a user of its own")
        else:
            out.append(f"Home Assistant: {self.ha_url}" if self.ha_url and self.ha_token else "Home Assistant access: not now")
        out.append(f"DashCast: {self.dashcast}" if self.dashcast else "DashCast: not now")
        out.append(f"Music Assistant: {self.music_assistant}" if self.music_assistant else "Music Assistant: not now")
        if self.music_assistant and self.music_assistant_token:
            out.append("Music Assistant: the Show gets a user of its own there, for lyrics")
            if self.music_assistant_local_metadata:
                out.append("Music Assistant: online metadata lookups switched off")
            if self.music_assistant_tunein:
                out.append(f"Music Assistant: TuneIn added as {self.music_assistant_tunein}, unless it is there")
        if self.ha_admin:
            out.append("added to Home Assistant automatically" +
                       (f", signed in as {self.ha_user} until the run ends" if self.ha_sign_in else ""))
        else:
            out.append("added to Home Assistant: by hand (no admin sign-in)")
        return out


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "jarvis-show"


# The sign-ins of the run that is going on; the installer ends them all when it stops.
RUN = SignIns(lambda: config_dir() / "sign-ins.json")


def load_defaults(path: Path | None = None) -> dict[str, str]:
    path = path or config_dir() / "defaults.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if k in REMEMBERED and isinstance(v, str)} if isinstance(data, dict) else {}


def save_defaults(settings: Settings, path: Path | None = None, *, asked: bool = True) -> None:
    """Keeps the addresses and usernames (never a secret) as the next run's defaults. A run that asked
    nothing (asked=False: --no-questions, or no terminal) leaves them alone, since it never offered them."""
    if not asked:
        return
    path = path or config_dir() / "defaults.json"
    data = {k: getattr(settings, k) for k in REMEMBERED if getattr(settings, k)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass


# Where secret-tool lives when it is not on PATH (on OpenJade desktops it ships with jplane).
SECRET_TOOL_FALLBACKS = ("~/.local/share/jplane/libsecret/bin/secret-tool",)


def secret_tool() -> str | None:
    found = os.environ.get("JARVIS_SHOW_SECRET_TOOL") or shutil.which("secret-tool")
    if found:
        return found
    for candidate in SECRET_TOOL_FALLBACKS:
        path = os.path.expanduser(candidate)
        if os.access(path, os.X_OK):
            return path
    return None


def keyring_lookup(secret: str, *, strip: bool = True, **attrs: str) -> str | None:
    """One secret from the desktop keyring, or None when there is no keyring or no such entry."""
    tool = secret_tool()
    if not tool:
        return None
    argv = [tool, "lookup", "application", "jarvis-show", "secret", secret]
    for key, value in attrs.items():
        argv += [key, value]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.rstrip("\r\n") if out.returncode == 0 else ""
    return (value.strip() if strip else value) or None


def read_secret_file(path: Path, what: str) -> str:
    """A secret from a file. Not resolved first: /dev/fd/N from <(...) has to stay what it is."""
    try:
        value = Path(os.path.expanduser(str(path))).read_text(encoding="utf-8").rstrip("\r\n").strip()
    except OSError as exc:
        raise SettingsError(f"cannot read the {what} file: {exc.strerror}") from None
    if not value:
        raise SettingsError(f"the {what} file is empty")
    return value


def _resolve(host: str) -> str:
    return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]


def _readline():
    """The line editor input() uses, where Python has one; None where it hasn't."""
    try:
        import readline
    except ImportError:
        return None
    return readline


@dataclass
class Asker:
    """The terminal, or nothing when nobody is at it (then every answer has to come from a switch).

    A question has a blank line before it and sits in OpenJade's box (ui.box_top):

          ╭─ the question ─────────────────────────
          │  what Enter does
          │  a dim line or two of help
          ╰─› the answer

    and goes through say, so a test sees the same lines a person does."""

    interactive: bool
    ask: Callable[[str], str] = input
    ask_secret: Callable[[str], str] = getpass.getpass
    say: Callable[[str], None] = print
    out: ui.Out = field(default_factory=lambda: ui.out)

    # -------------------------------------------------------------- lines between the questions

    def heading(self, text: str, help: str = "") -> None:
        if self.interactive:
            self._say(self.out.heading_lines(text) + (self.out.note_lines(help, indent=2) if help else []))

    def note(self, text: str) -> None:
        if self.interactive:
            self._say(self.out.note_lines(text))

    def warn(self, text: str) -> None:
        if self.interactive:
            self._say(self.out.lines("warn", text, paint="33"))

    def command(self, text: str) -> None:
        if self.interactive:
            self.say("      " + self.out.bold(text))

    def _say(self, lines: list[str]) -> None:
        for line in lines:
            self.say(line)

    def _question(self, label: str, enter: str, help: str) -> None:
        self._say(self.out.box_top(label))
        if enter:
            self._say(self.out.box_side(enter))
        if help:
            self._say(self.out.box_side(help))

    def _prompt(self, text: str = "") -> str:
        return self.out.box_prompt(text)

    def _filled(self, value: str) -> str:
        """input() with value already on the line, to keep, edit or clear."""
        readline = _readline()
        readline.set_startup_hook(lambda: readline.insert_text(value))
        try:
            return input(self._prompt())
        finally:
            readline.set_startup_hook()

    def _wrong(self, text: str) -> None:
        self._say(self.out.lines("warn", text, paint="33"))

    # -------------------------------------------------------------------------------- questions

    def text(self, label: str, default: str | None, check: Callable[[str], str],
             skip: str = "Enter to skip", *, help: str = "") -> str | None:
        """A value, re-asked until the Show would take it. Enter keeps the default; '-' is none."""
        if not self.interactive:
            return None
        filled = bool(default) and self.ask is input and _readline() is not None
        if filled:
            enter = "Enter keeps what is there; edit it, or clear the line to leave it out"
        elif default:
            enter = f"Enter keeps {default}, - for none"
        else:
            enter = "" if "Enter" in help else skip  # the help says what Enter does already
        self._question(label, enter, help)
        while True:
            try:
                raw = (self._filled(default) if filled else self.ask(self._prompt())).strip()
            except EOFError:
                return None
            if raw == "-" or (not raw and (filled or not default)):
                return None
            try:
                return check(raw or default or "")
            except ValueError as exc:
                self._wrong(str(exc))

    def choices(self, options: list[str], *, current: str = "", names: dict[str, str] | None = None) -> None:
        """options numbered, in two columns when there are many short ones."""
        names = names or {}
        now = self.out.dim("← now" if self.out.fancy else "(now)")
        width = len(str(len(options)))
        cells = [(f"{i:>{width}}  {names.get(o, o)}", o == current) for i, o in enumerate(options, 1)]
        column = max(len(c) for c, _ in cells) + 8
        if len(cells) > 8 and 6 + 2 * column <= self.out.width:
            half = (len(cells) + 1) // 2
            for left, right in zip(cells[:half], cells[half:] + [("", False)]):
                shown = self._cell(left, now)
                self.say(self.out.box_row(shown.ljust(column + self._hidden(shown)) + self._cell(right, now)).rstrip())
            return
        for cell in cells:
            self.say(self.out.box_row(self._cell(cell, now)))

    @staticmethod
    def _cell(cell: tuple[str, bool], now: str) -> str:
        return cell[0] + ("  " + now if cell[1] else "")

    @staticmethod
    def _hidden(text: str) -> int:
        """How many characters of text are colour codes, which take no room."""
        return len(text) - len(ui.visible(text))

    def pick(self, label: str, current: str, options: list[str], *, default: str | None = None,
             names: dict[str, str] | None = None, help: str = "") -> str | None:
        """One of options by number, name or a piece of a name. Enter takes default (None: leave it as it
        is); None also when nobody is asked."""
        if not self.interactive or not options:
            return None
        names = names or {}
        enter = f"Enter: {names.get(default, default)}" if default else "Enter leaves it as it is"
        self._question(label, "", help)
        self.choices(options, current=current, names=names)
        while True:
            try:
                raw = self.ask(self._prompt(f"number or name ({enter})")).strip()
            except EOFError:
                return None
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            want = raw.casefold()
            for found in ([o for o in options if want in (o.casefold(), names.get(o, o).casefold())],
                          [o for o in options if want in o.casefold() or want in names.get(o, o).casefold()]):
                if len(found) == 1:
                    return found[0]
            self._wrong(f"type 1 to {len(options)} or a name from the list ({enter})")

    def secret(self, label: str, check: Callable[[str], str], *, strip: bool = True,
               confirm: bool = False, help: str = "") -> str | None:
        """A hidden value. confirm asks for it twice, for one nobody can see the typo in."""
        if not self.interactive:
            return None
        self._question(label, "", help)
        while True:
            try:
                raw = self.ask_secret(self._prompt("hidden as you type, Enter to skip"))
                raw = raw.strip() if strip else raw.rstrip("\r\n")
                if not raw:
                    return None
                value = check(raw)
                if confirm and self.ask_secret(self._prompt("once more, to be sure")).rstrip("\r\n") != raw:
                    self._wrong("the two did not match; once more")
                    continue
                return value
            except EOFError:
                return None
            except ValueError as exc:
                self._wrong(str(exc))

    def yes(self, label: str, *, help: str = "") -> bool | None:
        """A yes or no that has no default: Enter alone asks again. None when nobody can answer."""
        if not self.interactive:
            return None
        self._question(label, "", help)
        while True:
            try:
                raw = self.ask(self._prompt("yes or no")).strip().casefold()
            except EOFError:
                return None
            if raw in ("y", "yes", "j", "ja"):
                return True
            if raw in ("n", "no", "nein"):
                return False
            self._wrong("type yes or no")


def check_network(value: str) -> str:
    if not 1 <= len(value.encode("utf-8")) <= 32:
        raise ValueError("a Wi-Fi network name is 1 to 32 bytes")
    return value


def check_wifi_passphrase(value: str) -> str:
    if not 8 <= len(value) <= 63:
        raise ValueError("a WPA passphrase is 8 to 63 characters")
    return value


def check_root_password(value: str) -> str:
    if not 8 <= len(value) <= 128:
        raise ValueError("a root password is 8 to 128 characters")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("a root password has no control characters")
    if value != value.strip():
        raise ValueError("a root password has no spaces at its ends")
    return value


def nearby_networks(limit: int = 12) -> list[str]:
    """The Wi-Fi networks this computer can see, strongest first (NetworkManager only; else none)."""
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return []
    try:
        out = subprocess.run([nmcli, "-t", "-e", "no", "-f", "SSID", "device", "wifi", "list"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    seen: list[str] = []
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        if line and line not in seen:
            seen.append(line)
    return seen[:limit]


def _checked(value: str | None, check: Callable[[str], str]) -> str | None:
    if value is None:
        return None
    try:
        return check(value)
    except ValueError as exc:
        raise SettingsError(str(exc)) from None


def gather(args, asker: Asker, *, defaults: dict[str, str] | None = None,
           lookup: Callable[..., str | None] = keyring_lookup,
           resolve: Callable[[str], str] = _resolve, want_wifi: bool = True,
           want_root_password: bool = False, want_show: bool = True, want_music: bool = True,
           nearby: Callable[[], list[str]] = nearby_networks,
           ha_login: Callable[..., "ha_api.SignedIn"] = ha_api.sign_in,
           ma_login: Callable[..., tuple[str, str]] = ma_api.sign_in,
           ma_logout: Callable[..., None] = ma_api.sign_out,
           ma_tunein_there: Callable[[str, str], bool] = ma_api.tunein_there,
           run: SignIns | None = None) -> Settings:
    """Everything the Show is set up with: switches first, then the keyring, then the terminal. Without
    want_show, only the admin sides of Home Assistant and Music Assistant (for taking a Show out); without
    want_music, Music Assistant is left out (for the list of Shows)."""
    d = defaults if defaults is not None else load_defaults()
    run = run if run is not None else RUN

    def ma(value: str) -> str:
        return normalize_music_assistant(value, resolve)

    def ha_token(value: str) -> str:
        return check_token(value, "the token")

    def from_file(attr: str, what: str, check: Callable[[str], str]) -> str | None:
        path = getattr(args, attr, None)
        return _checked(read_secret_file(path, what), check) if path else None

    s = Settings()
    if want_root_password:
        asker.heading("Root password", "The Show's USB console asks for it.")
        pw = from_file("root_password_file", "root password", check_root_password) or \
            _checked(lookup("root-password", strip=False), check_root_password)
        if pw is None and asker.interactive:
            pw = asker.secret("a root password for the Show", check_root_password, strip=False, confirm=True,
                              help="8 to 128 characters. It goes onto the Show only; this computer keeps no copy.")
            if pw is None:
                asker.warn("without one, whoever switches USB debugging on at the Show gets a root shell")
                asker.note("Enter again leaves it without; techo5-passwd on the Show sets one later.")
                pw = asker.secret("a root password for the Show", check_root_password, strip=False, confirm=True)
            if pw:
                _saved_it(asker)
                _keyring_hint(asker, "root password", "secret root-password")
        s = replace(s, root_password=pw)
    if want_wifi:
        asker.heading("Wi-Fi", "The Show joins it on first boot.")
        wifi = _checked(getattr(args, "wifi", None), check_network)
        if not wifi and asker.interactive:
            seen = nearby()

            def network(value: str) -> str:
                if value.isdigit() and seen:
                    if not 1 <= int(value) <= len(seen):
                        raise ValueError(f"pick 1 to {len(seen)}, or type the network's name")
                    return seen[int(value) - 1]
                return check_network(value)

            if seen:
                asker.note("Networks this computer can see; a number picks one:")
                asker.choices(seen)
            wifi = asker.text("network name (SSID)", d.get("wifi"), network,
                              skip="Enter: pick it on the Show's screen instead")
        passphrase = None
        if wifi and not getattr(args, "wifi_passphrase_file", None):
            passphrase = _checked(lookup("wifi", network=wifi, strip=False), check_wifi_passphrase)
            if passphrase is None:
                passphrase = asker.secret(f"passphrase for {wifi}", check_wifi_passphrase, strip=False, confirm=True,
                                          help="It goes onto the Show only; this computer keeps no copy.")
                if passphrase:
                    _keyring_hint(asker, "Wi-Fi", f"secret wifi network {shlex.quote(wifi)}")
            if passphrase is None and asker.interactive:
                asker.note("No passphrase: the Show asks for the network on its screen instead.")
                wifi = None
        s = replace(s, wifi=wifi, wifi_passphrase=passphrase)

    asker.heading("Home Assistant")
    ha_url = _checked(getattr(args, "ha_url", None), normalize_ha_url) or \
        asker.text("address, as the Show reaches it", d.get("ha_url"), normalize_ha_url,
                   help="Like http://homeassistant.local:8123. Leave it empty to skip Home Assistant for now.")
    token = admin = signed = user = None
    want_own = bool(getattr(args, "own_ha_user", False))
    if ha_url:
        token = from_file("ha_token_file", "Home Assistant token", ha_token)
        admin = from_file("ha_admin_token_file", "Home Assistant admin token", ha_token) or \
            _checked(lookup("ha-admin-token"), ha_token)
        if not admin:
            signed, user = _ha_sign_in(asker, ha_url, d.get("ha_user"), ha_login, run)
    own = bool(ha_url and (admin or signed) and not token)
    if want_own and not own:
        raise SettingsError("--own-ha-user needs an admin, who makes the Show's user"
                            if not token else "--own-ha-user and --ha-token-file are two different Show users")
    if ha_url and not want_show:
        pass
    elif ha_url and own:
        asker.note("The Show becomes a Home Assistant user of its own when it is added, and gets its token then.")
    elif ha_url and not token:
        token = _checked(lookup("ha-token"), ha_token) or \
            asker.secret("a long-lived token for the Show", ha_token,
                         help="Without an admin, the Show needs a token made by hand, as the user it should be "
                              "(that user's profile, Security, Long-lived access tokens), and you add the Show in "
                              "Home Assistant yourself. It goes onto the Show only; this computer keeps no copy.")
    s = replace(s, ha_url=ha_url, ha_token=token, ha_admin_token=admin, own_ha_user=own,
                ha_user=user, ha_sign_in=signed)

    if want_show:
        asker.heading("DashCast", "The dashboard, streamed to the Show.")
    dashcast = _checked(getattr(args, "dashcast", None), normalize_dashcast) or \
        (asker.text("server, host[:port]", d.get("dashcast"), normalize_dashcast) if want_show else None)
    key = None
    if dashcast:
        key = from_file("dashcast_key_file", "DashCast key", check_dashcast_key) or \
            _checked(lookup("dashcast-key"), check_dashcast_key) or \
            asker.secret("the DashCast key", check_dashcast_key,
                         help="It goes onto the Show only; this computer keeps no copy.")
        if key is None:
            asker.note("No key: DashCast is left for later.")
            dashcast = None
    s = replace(s, dashcast=dashcast, dashcast_key=key)

    if want_music:
        asker.heading("Music Assistant", "The Show plays through it as a Sendspin player.")
    music = (_checked(getattr(args, "music_assistant", None), ma) or
             asker.text("server IP or name", d.get("music_assistant"), ma)) if want_music else None
    ma_token = ma_user = None
    if music:
        local = getattr(args, "music_assistant_local_metadata", False)
        ma_token = from_file("music_assistant_token_file", "Music Assistant token", ma_check_token) or \
            _checked(lookup("music-assistant-token"), ma_check_token)
        if ma_token is None:
            ma_token, ma_user = _ma_sign_in(asker, music, d.get("music_assistant_user"), ma_login, ma_logout,
                                            run, local)
        if ma_token is None:
            ma_token = asker.secret("a Music Assistant admin's token instead (optional)", ma_check_token,
                                    help="Used for this run only, like the sign-in, and never stored anywhere. "
                                         "Make one in Music Assistant under Settings, your profile, Long-lived "
                                         "tokens. Enter skips it, but then lyrics won't work on the Show.")
        if ma_token is None and not want_show:
            asker.note("No Music Assistant admin: the Show's user and player there are left as they are.")
        elif ma_token is None:
            asker.note("No Music Assistant admin: music plays, but lyrics won't work on the Show" +
                       ("; Music Assistant keeps looking metadata up online." if local else "."))
    tunein = None
    if music and ma_token:
        tunein = _ma_tunein(asker, args, music, ma_token, ma_tunein_there, want_show)
    s = replace(s, music_assistant=music, music_assistant_token=ma_token, music_assistant_user=ma_user,
                music_assistant_local_metadata=bool(music and getattr(args, "music_assistant_local_metadata", False)),
                music_assistant_tunein=tunein)
    return s


def _ma_tunein(asker: Asker, args, host: str, token: str, there: Callable[[str, str], bool],
               want_show: bool) -> str | None:
    """The TuneIn username to add Music Assistant's TuneIn with: --music-assistant-tunein ('' leaves it
    alone), else asked, but only of a Music Assistant that has no TuneIn yet."""
    given = getattr(args, "music_assistant_tunein", None)
    if given is not None:
        return _checked(given, ma_api.check_tunein_username) if given.strip() else None
    if not (want_show and asker.interactive):
        return None
    try:
        if there(host, token):
            return None
    except ma_api.MusicAssistantError:
        return None  # the steps that need Music Assistant say what went wrong
    return asker.text("your TuneIn username, for the radio (optional)", None, ma_api.check_tunein_username,
                      skip="Enter: no TuneIn",
                      help="Music Assistant adds TuneIn with it and lists the stations that account follows; "
                           "the Show's radio page shows them. The username, not the email address. Music "
                           "Assistant keeps it in its settings; this computer does not.")


def check_username(value: str) -> str:
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("a username has no control characters")
    return value


def check_password(value: str) -> str:
    if any(ch in "\r\n" for ch in value):
        raise ValueError("a password is one line")
    return value


def check_code(value: str) -> str:
    code = value.replace(" ", "")
    if not code.isdigit() or not 4 <= len(code) <= 10:
        raise ValueError("the code is the digits your authenticator shows")
    return code


SIGN_IN_TRIES = 3


def _password(asker: Asker, user: str, help: str) -> str | None:
    return asker.secret(f"{user}'s password", check_password, strip=False, help=help)


def _ha_sign_in(asker: Asker, url: str, remembered: str | None, login: Callable[..., "ha_api.SignedIn"],
                run: SignIns) -> tuple["ha_api.SignedIn | None", str | None]:
    """An admin signed in for this run, or (None, None) when nobody is asked or they skip it."""
    user = asker.text("an admin's username", remembered, check_username,
                      skip="Enter: no admin, the Show is then added by hand",
                      help="The installer signs in as them to add the Show to Home Assistant, give it a user "
                           "of its own, and set up its room, dashboard and voice. It signs out again when the "
                           "run ends, failed or Ctrl-C too, and keeps neither the password nor the sign-in.")
    if not user:
        return None, None

    def code(again: bool) -> str | None:
        if again:
            asker.warn("that code was wrong")
        return asker.text("two-factor code", None, check_code, skip="Enter gives up signing in",
                          help="From the authenticator app you set up for Home Assistant.")

    for _ in range(SIGN_IN_TRIES):
        password = _password(asker, user, "Only for this sign-in; it is dropped right after.")
        if password is None:
            return None, None
        try:
            signed = login(url, user, password, code=code)
        except ha_api.NotSignedIn as exc:
            asker.warn(f"could not sign in: {exc}")
            continue
        except ha_api.HomeAssistantError as exc:
            raise SettingsError(f"Home Assistant: {exc}") from None
        try:
            sign_in_id = signed.sign_in_id()
        except ha_api.HomeAssistantError:
            sign_in_id = None
        ended = run.opened("Home Assistant", url, user, sign_in_id, end=signed.sign_out,
                           end_other=signed.end_other)
        if ended:
            asker.note(f"Ended {ended} sign-in{'s' if ended > 1 else ''} an earlier run had left open.")
        return signed, user
    asker.warn(f"{SIGN_IN_TRIES} tries; going on without an admin")
    return None, None


def _ma_sign_in(asker: Asker, host: str, remembered: str | None, login: Callable[..., tuple[str, str]],
                logout: Callable[..., None], run: SignIns, local: bool) -> tuple[str | None, str | None]:
    """A Music Assistant admin's token for this run and their username; (None, None) when skipped,
    (None, user) when the sign-in did not work out."""
    user = asker.text("a Music Assistant admin's username", remembered, check_username,
                      skip="Enter: no sign-in (a token is asked next)",
                      help="The installer signs in as them to make the Show a plain Music Assistant user of its "
                           "own, which the lyrics need" +
                           (", and to switch Music Assistant's online lookups off" if local else "") +
                           ". It signs out when the run ends and keeps neither the password nor the sign-in.")
    if not user:
        return None, None
    hinted = False
    for _ in range(SIGN_IN_TRIES):
        password = _password(asker, user, "Only for this sign-in; it is dropped right after.")
        if password is None:
            return None, user
        try:
            token, token_id = login(host, user, password)
        except ma_api.NotSignedIn as exc:
            asker.warn(f"could not sign in: {exc}")
            if "username or password" in str(exc) and not hinted:
                hinted = True
                asker.note("If Music Assistant made this account itself, when it was first opened from the Home "
                           "Assistant sidebar, the account has no password of its own yet. Set one in Music "
                           "Assistant under Settings, your profile (8 characters or more), or press Enter here "
                           "and give a long-lived token instead.")
            continue
        except ma_api.MusicAssistantError as exc:
            asker.warn(f"Music Assistant: {exc}")
            return None, user

        def end_other(other: str, token: str = token) -> bool:
            try:
                logout(host, token, other)
            except ma_api.MusicAssistantError:
                return False
            return True

        ended = run.opened("Music Assistant", host, user, token_id,
                           end=lambda: logout(host, token, token_id), end_other=end_other)
        if ended:
            asker.note(f"Ended {ended} sign-in{'s' if ended > 1 else ''} an earlier run had left open.")
        return token, user
    asker.warn(f"{SIGN_IN_TRIES} tries; going on without a Music Assistant admin")
    return None, user


def _saved_it(asker: Asker) -> None:
    """The password was typed, not made up here, and nothing here keeps it: the one who typed it says
    they have it saved before anything goes on, so nobody walks off thinking the installer has a copy."""
    while True:
        saved = asker.yes("Have you saved this password yourself?", help=(
            "In a password manager or wherever you keep such things. The installer keeps no copy, and the "
            "Show keeps only a hash that can't be turned back into it."))
        if saved:
            return
        if saved is None:
            raise SettingsError("the root password was not confirmed as saved; nothing was changed")
        asker.warn("Save it now, then answer yes. Lost, only techo5-passwd over SSH sets another.")


def _keyring_hint(asker: Asker, label: str, attrs: str) -> None:
    # always the plain name: a path that only exists on this computer is no use in a hint
    asker.note("To skip this next time, keep it in the desktop keyring:")
    asker.command(f"secret-tool store --label='Jarvis Show: {label}' application jarvis-show {attrs}")
    if not shutil.which("secret-tool"):
        asker.note("secret-tool is not on PATH here; it comes with libsecret (libsecret-tools on Debian and Ubuntu).")


def ma_check_token(value: str) -> str:
    return check_token(value, "the Music Assistant token")


@contextlib.contextmanager
def secret_files(settings: Settings) -> Iterator[dict[str, Path]]:
    """The secrets as owner-only files in a private folder that is removed afterwards."""
    with tempfile.TemporaryDirectory(prefix="jarvis-show-") as tmp:
        out: dict[str, Path] = {}
        for name in ("wifi_passphrase", "ha_token", "dashcast_key", "root_password"):
            value = getattr(settings, name)
            if not value:
                continue
            path = Path(tmp) / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value + "\n")
            out[name] = path
        yield out
