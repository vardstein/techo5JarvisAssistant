"""What Genbu makes of a Show whose Home Assistant key stopped working (GENBU-196), without Home Assistant:
which Home Assistant user the Show is, why it was turned away, and what an admin can do about it. The glue
in keycare.py feeds it what Home Assistant and the Show say, and turns the answer into a Repairs issue.

Each Show reports how its key stands in its "Home Assistant key" sensor: ok, none, gone (Home Assistant no
longer knows the key; switching a user off deletes its keys, so that is gone too), remote (its user may
only come from home and the Show came from outside) or off. A refused Show stops asking, so without this
nobody would learn of it.

Genbu never gives a Show a key on its own. Deleting a Show's token is how an admin locks out a Show that
was lost or stolen, and Home Assistant holds nothing that tells that apart from a key lost some other way.
So a new key, and a user made for a Show that has none, only ever come from an admin pressing the button
on the Repairs issue (or calling genbu.show_key). Genbu does not change users either: a Show's user is
not an admin and may only come from home, and a user that is anything else gets no key from Genbu.

Everything here is plain Python so the tests run without Home Assistant installed."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

# The Show's sensor states, the same as echod's hass.Key* constants.
OK, NONE, GONE, REMOTE, OFF = "ok", "none", "gone", "remote", "off"
REFUSED = (GONE, REMOTE, OFF)

# Why a Show has a Repairs issue, which is also the issue's translation key.
KEY_GONE = "key_gone"  # Home Assistant no longer knows its key: an admin may give it a new one
NO_USER = "no_user"  # and the Show's user is gone too: an admin may make it one, with a key
USER_OFF = "user_off"  # its user is switched off, and with that its keys deleted: an admin switches it on
NOT_A_SHOW_USER = "not_a_show_user"  # its user is an admin, Home Assistant's own, or may come from outside
NOT_ENCRYPTED = "not_encrypted"  # its ESPHome connection has no encryption key, so no key goes over it
HOME_ONLY = "home_only"  # it came from outside, and its user may only come from home
FIXABLE = (KEY_GONE, NO_USER, HOME_ONLY)
# Not an issue: Home Assistant still has the Show's key and its user may use it, so the refusal was a
# mistake on the way (a restore, a restart half done) and Genbu has the Show try it once more with
# home_assistant_retry. Not for a user switched off and on again: switching a user off deletes its tokens.
TRY_AGAIN = "try_again"

# A Show turned away is asked how its key stands when that changes, and again at most this often.
ASK_EVERY = timedelta(hours=1)


@dataclass(frozen=True)
class User:
    """A Home Assistant user, as far as a Show's key goes."""

    id: str
    usernames: tuple[str, ...] = ()  # its sign-in names, lower case
    active: bool = True
    admin: bool = False  # the owner, an admin, or a user of Home Assistant's own
    home_only: bool = True  # local_only: may only sign in from the home network
    tokens: tuple[str, ...] = ()  # the ids of its long-lived tokens


def key_name(token_id: str) -> str:
    """How a Show names its key without giving it away: the SHA-256 of its refresh token's id, the same
    as echod's hass.KeyID."""
    return hashlib.sha256(token_id.encode()).hexdigest()


def usernames(prefix: str) -> tuple[str, str]:
    """The names the installer gives a Show's user (tools/jarvis_crown show_usernames)."""
    return f"show_{prefix}", f"show_{prefix}-2"


def owner(key: str, users: list[User]) -> tuple[User, str] | None:
    """The user holding the token a Show named, and that token's id; None when nobody does."""
    if not key:
        return None
    for user in users:
        for token in user.tokens:
            if key_name(token) == key:
                return user, token
    return None


def show_user(users: list[User], recorded: str, prefix: str) -> User | None:
    """The Show's user: the one Genbu saw its key belong to, else the one with the installer's name for
    it. The recorded one wins, so a renamed Show keeps its user."""
    by_id = {u.id: u for u in users}
    if recorded and recorded in by_id:
        return by_id[recorded]
    for name in usernames(prefix):
        for user in users:
            if name in user.usernames:
                return user
    return None


def why(state: str, key: str, encrypted: bool, user: User | None) -> str | None:
    """Why a Show was turned away, as one of the issue kinds above; None when there is nothing to say.

    state is the Show's sensor, key the name of the key it holds ("" when it could not be asked), and
    user its Home Assistant user, None when it has none any more."""
    if state == REMOTE:
        return HOME_ONLY
    if state not in (GONE, OFF):
        return None
    if user is not None and not user.active:
        return USER_OFF
    if not encrypted:
        return NOT_ENCRYPTED
    if user is None:
        return NO_USER
    if user.admin or not user.home_only:
        return NOT_A_SHOW_USER
    if key and any(key_name(t) == key for t in user.tokens):
        # Home Assistant still has the key and its user may use it: a new key would fix nothing, a try would.
        return TRY_AGAIN
    return KEY_GONE


def may_renew(user: User | None, encrypted: bool) -> str | None:
    """Checked again right before a new key is made: None when it may be, else why not."""
    if not encrypted:
        return NOT_ENCRYPTED
    if user is None:
        return None  # an admin is making it a user, which is home-only and no admin
    if not user.active:
        return USER_OFF
    if user.admin or not user.home_only:
        return NOT_A_SHOW_USER
    return None


def password() -> str:
    """The password of a user Genbu makes for a Show: never kept, so nobody signs in with it."""
    return secrets.token_urlsafe(32)


def retire(tokens: tuple[str, ...], new: str) -> list[str]:
    """The long-lived tokens of the Show's user that go once the Show has stored the new one."""
    return [t for t in tokens if t != new]


def due(last: datetime | None, now: datetime) -> bool:
    """Whether a turned-away Show is asked again: each ask is a call on the Show."""
    return last is None or now - last >= ASK_EVERY


def ask(state: str, before: str | None, last: datetime | None, now: datetime) -> bool:
    """Whether Genbu asks the Show about its key now: whenever its sensor changed to ok or a refusal (and
    once after Genbu starts), and a turned-away Show again at most every ASK_EVERY."""
    if state not in (OK, *REFUSED):
        return False
    if state != before:
        return True
    return state in REFUSED and due(last, now)


def home_address(url: str) -> str | None:
    """Checks an address handed to a Show with its key: None when it may be, else why not. A Show's user
    may only sign in from home, so an address that comes in through the cloud (Nabu Casa's remote UI)
    would only get the Show turned away."""
    if not url:
        return None  # the Show keeps the address it has
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "not_an_address"
    if parts.scheme not in ("http", "https") or not host:
        return "not_an_address"
    if host == "ui.nabu.casa" or host.endswith(".ui.nabu.casa"):
        return "cloud_address"
    return None
