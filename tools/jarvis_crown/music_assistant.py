"""Music Assistant left to the metadata a house already has.

Some houses tag their music before Music Assistant ever sees it, and want it to stop looking songs
up on the internet (MusicBrainz, fanart.tv, TheAudioDB, Wikipedia and the rest). That is a switch
on the installer, --music-assistant-local-metadata, and off unless asked for: a house that streams
from a service, or never tagged anything, gets its pictures and biographies from those lookups.

Over Music Assistant's own websocket API (port 8095, a long-lived token made in its settings):
  1. its "online metadata" switch goes off, which stops the lookups for artists, albums and songs
     from every metadata provider, MusicBrainz included, which Music Assistant cannot disable;
  2. the providers that only ever look things up online and that Music Assistant lets be disabled
     are disabled, so none of them is asked for anything else either.
Both are Music Assistant settings, turned back on in its own settings pages.

The same token also gives the Show a Music Assistant user of its own (show_token), for the lyrics of
the song playing, which Home Assistant's integration does not pass on. The user is a plain one, not
an admin; its password is random and thrown away, so only its token, on the Show, ever signs in. The
installer's token is used for that and kept nowhere.

That token is either an admin's long-lived one, or one the installer gets by signing in as an admin
with a username and password (sign_in, Music Assistant's own auth/login). A sign-in's token is
revoked again when the run ends (sign_out).
"""
from __future__ import annotations

import re
import secrets
import socket
from typing import Any, Callable

from jarvis_crown.home_assistant import ws_open

PORT = 8095

# The online metadata providers Music Assistant (2.10) lets be disabled. musicbrainz and
# playlist_metadata are not among them: it refuses to disable those.
ONLINE_PROVIDERS = ("lrclib", "fanarttv", "theaudiodb", "coverartarchive", "wikipedia", "itunes_artwork",
                    "lastfm_recommendations", "acoustid_lookup")


class MusicAssistantError(RuntimeError):
    """Music Assistant could not be reached, did not take the token, or refused a setting."""


class NotSignedIn(MusicAssistantError):
    """Music Assistant turned a sign-in down: a wrong password, or not an admin."""


class _Session:
    def __init__(self, frames: Any) -> None:
        self.frames = frames
        self.next_id = 0

    def call(self, command: str, **args: Any) -> Any:
        self.next_id += 1
        message_id = str(self.next_id)
        self.frames.send({"message_id": message_id, "command": command, "args": args})
        while True:
            answer = self.frames.recv()
            if answer.get("message_id") != message_id or answer.get("partial"):
                continue
            if "error_code" in answer:
                raise MusicAssistantError(f"{command}: {answer.get('details') or answer['error_code']}")
            return answer.get("result")


def _login(sock: Any, host: str, token: str, *, key: str | None = None) -> _Session:
    frames = ws_open(sock, host, "/ws", key=key, peer="Music Assistant")
    info = frames.recv()
    if "server_version" not in info:
        raise MusicAssistantError("that is not a Music Assistant server")
    session = _Session(frames)
    try:
        session.call("auth", token=token)
    except MusicAssistantError:
        raise MusicAssistantError("Music Assistant did not accept the token") from None
    return session


def exchange(sock: Any, host: str, token: str, *, key: str | None = None) -> list[str]:
    """Logs in on a connected socket and switches the online lookups off; returns what changed."""
    session = _login(sock, host, token, key=key)
    done = []
    session.call("config/core/save", domain="metadata", values={"enable_online_metadata": False})
    done.append("online metadata lookups off")
    for provider in session.call("config/providers") or []:
        if provider.get("domain") in ONLINE_PROVIDERS and provider.get("enabled"):
            session.call("config/providers/save", provider_domain=provider["domain"],
                         instance_id=provider["instance_id"], values={"enabled": False})
            done.append(f"{provider['domain']} disabled")
    return done


# What the Show's token is called in Music Assistant's list of the user's tokens. A new one replaces
# the ones before it, so running the installer again leaves one.
TOKEN_NAME = "Jarvis Show"


def show_username(name: str) -> str:
    """The Show's user in Music Assistant, from its name: "Jarvis Show 5" is jarvis-show-5."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(slug) < 2:
        raise MusicAssistantError(f"cannot make a Music Assistant user name from {name!r}")
    return slug


def show_exchange(sock: Any, host: str, token: str, name: str, *, key: str | None = None) -> tuple[str, bool]:
    """Logs in on a connected socket and makes the Show's user and a token for it; returns the token
    and whether the user was new."""
    session = _login(sock, host, token, key=key)
    username = show_username(name)
    user = next((u for u in session.call("auth/users") or [] if u.get("username") == username), None)
    made = user is None
    if made:
        user = session.call("auth/user/create", username=username, password=secrets.token_urlsafe(32),
                            role="user", display_name=name)
    elif user.get("role") != "user" or not user.get("enabled", True):
        raise MusicAssistantError(f"Music Assistant already has a user {username!r} that is not a plain, "
                                  "enabled user; the Show's lyrics are left out")
    user_id = user["user_id"]
    for old in session.call("auth/tokens", user_id=user_id) or []:
        if old.get("name") == TOKEN_NAME:
            session.call("auth/token/revoke", token_id=old["token_id"])
    return session.call("auth/token/create", name=TOKEN_NAME, user_id=user_id), made


def _connected(host: str, timeout: float, connect: Callable[..., Any], run: Callable[[Any, str], Any]) -> Any:
    try:
        sock = connect((host, PORT), timeout=timeout)
    except OSError as exc:
        raise MusicAssistantError(f"cannot reach Music Assistant at {host}:{PORT}: {exc}") from None
    try:
        return run(sock, f"[{host}]:{PORT}" if ":" in host else f"{host}:{PORT}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MusicAssistantError(str(exc)) from None
    finally:
        sock.close()


def local_metadata_only(host: str, token: str, *, timeout: float = 20.0,
                        connect: Callable[..., Any] = socket.create_connection) -> list[str]:
    """Music Assistant at host stops looking anything up online; returns what changed."""
    return _connected(host, timeout, connect, lambda sock, peer: exchange(sock, peer, token))


def show_token(host: str, token: str, name: str, *, timeout: float = 20.0,
               connect: Callable[..., Any] = socket.create_connection) -> tuple[str, bool]:
    """A token for the Show's own Music Assistant user, made when it is not there yet; returns it
    and whether the user was new."""
    return _connected(host, timeout, connect, lambda sock, peer: show_exchange(sock, peer, token, name))


def sign_in_exchange(sock: Any, host: str, username: str, password: str, *, name: str,
                     key: str | None = None) -> tuple[str, str]:
    """Signs in on a connected socket; returns the sign-in's token and the id it is revoked by."""
    frames = ws_open(sock, host, "/ws", key=key, peer="Music Assistant")
    if "server_version" not in frames.recv():
        raise MusicAssistantError("that is not a Music Assistant server")
    session = _Session(frames)
    got = session.call("auth/login", username=username, password=password, device_name=name)
    if not isinstance(got, dict) or not got.get("success") or not got.get("access_token"):
        why = (got or {}).get("error") if isinstance(got, dict) else None
        raise NotSignedIn(f"Music Assistant did not let {username!r} sign in ({why or 'no answer'})")
    token = got["access_token"]
    session.call("auth", token=token)
    token_id = next((t.get("token_id") for t in session.call("auth/tokens") or [] if t.get("name") == name), None)
    if not token_id or (got.get("user") or {}).get("role") != "admin":
        if token_id:
            _revoke(session, token_id)
        raise NotSignedIn(f"{username!r} is not a Music Assistant admin, and making the Show's user needs one"
                          if token_id else "Music Assistant does not list the sign-in it just made")
    return token, token_id


def _revoke(session: _Session, token_id: str) -> None:
    try:
        session.call("auth/token/revoke", token_id=token_id)
    except ValueError as exc:
        # Music Assistant closes the connections that use a token as it revokes it, which may be
        # before its answer is sent.
        if "closed" not in str(exc):
            raise


def revoke_exchange(sock: Any, host: str, token: str, token_id: str, *, key: str | None = None) -> None:
    _revoke(_login(sock, host, token, key=key), token_id)


def sign_in(host: str, username: str, password: str, *, timeout: float = 20.0,
            connect: Callable[..., Any] = socket.create_connection) -> tuple[str, str]:
    """An admin signed in for this run: a token, and its id for sign_out."""
    name = f"Jarvis Show installer {secrets.token_hex(3)}"
    return _connected(host, timeout, connect,
                      lambda sock, peer: sign_in_exchange(sock, peer, username, password, name=name))


def sign_out(host: str, token: str, token_id: str, *, timeout: float = 20.0,
             connect: Callable[..., Any] = socket.create_connection) -> None:
    """Revokes a token, the sign-in's own (token_id is its id) or an earlier one of the same user."""
    _connected(host, timeout, connect, lambda sock, peer: revoke_exchange(sock, peer, token, token_id))
