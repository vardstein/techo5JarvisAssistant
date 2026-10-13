"""Genbu's side of a Show whose Home Assistant key stopped working (GENBU-196). A Show that was turned
away stops asking, so it says how its key stands in its "Home Assistant key" sensor. Genbu asks it which
key it holds, works out with keys.py why it was turned away, and puts that up in Settings, Repairs, where
an admin can act on it.

A new key only ever comes from an admin: the Repairs issue's button (repairs.py, which Home Assistant only
lets admins use) or genbu.show_key, an admin-only action. See keys.py for why Genbu never does it by
itself, and docs/genbu-assistant.md."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.auth.models import User as HassUser
from homeassistant.auth.providers.homeassistant import InvalidUser
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from . import keys
from .const import DOMAIN
from .house import House, Show
from .recipe import show_prefix

LOGGER = logging.getLogger(__name__)
ISSUE = "key_"  # each Show's issue is key_<device id>
KEY_LIFE = timedelta(days=3650)  # what the installer gives a Show's key too


class KeyRefused(HomeAssistantError):
    """No new key for this Show; reason is one of keys' issue kinds, or why the Show did not take it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def issue_id(device: str) -> str:
    return f"{ISSUE}{device}"


def _user(user: HassUser) -> keys.User:
    return keys.User(
        id=user.id,
        usernames=tuple(str(c.data.get("username", "")).strip().casefold() for c in user.credentials
                        if c.auth_provider_type == "homeassistant"),
        active=user.is_active,
        admin=user.is_admin or user.is_owner or user.system_generated,
        home_only=bool(user.local_only),
        tokens=tuple(t.id for t in user.refresh_tokens.values() if t.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN),
    )


class KeyCare:
    def __init__(self, hass: HomeAssistant, house: House, save) -> None:
        self.hass, self.house, self._save = hass, house, save
        self.seen: dict[str, str] = {}  # each Show's sensor when it was last asked, by device id
        self.asked: dict = {}  # and when that was
        self.tried: set[str] = set()  # the Shows told to try their key again, until something changes
        self._lock = asyncio.Lock()

    @property
    def recorded(self) -> dict[str, dict]:
        return self.house.data["keys"]

    def again(self) -> None:
        """A user changed: every turned-away Show is asked again on the next look."""
        self.asked.clear()

    async def async_look(self, shows: list[Show]) -> None:
        """Asks each Show whose key changed, and each turned-away Show now and then, and puts up or takes
        down its Repairs issue. A look that is still going makes the next one wait for the one after."""
        if self._lock.locked():
            return
        async with self._lock:
            now = dt_util.utcnow()
            for show in shows:
                if not show.key or not show.device:
                    continue
                st = self.hass.states.get(show.key)
                state = st.state if st else ""
                if not keys.ask(state, self.seen.get(show.device), self.asked.get(show.device), now):
                    continue
                self.seen[show.device], self.asked[show.device] = state, now
                try:
                    await self._look_at(show, state)
                except HomeAssistantError as err:
                    LOGGER.info("Could not ask %s about its Home Assistant key: %s", show.name, err)
            self._drop_gone()

    async def _ask(self, show: Show) -> dict:
        """The Show's own word on its key: {"state": ..., "key": ...}. {} when it has no encryption key,
        as it only answers over an encrypted connection."""
        if not show.encrypted:
            return {}
        answer = await self.hass.services.async_call(
            "esphome", f"{show_prefix(show.node)}_home_assistant_key", {}, blocking=True, return_response=True)
        return answer if isinstance(answer, dict) else {}

    async def _users(self) -> list[HassUser]:
        return list(await self.hass.auth.async_get_users())

    def _user_of(self, show: Show, key: str, users: list[keys.User]) -> keys.User | None:
        if found := keys.owner(key, users):
            return found[0]
        return keys.show_user(users, self.recorded.get(show.device, {}).get("user", ""), show_prefix(show.node))

    def _record(self, show: Show, user_id: str) -> None:
        if self.recorded.get(show.device, {}).get("user") != user_id:
            self.recorded[show.device] = {"user": user_id}
            self._save()

    async def _look_at(self, show: Show, state: str) -> None:
        if state == keys.NONE:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(show.device))
            return
        answer = await self._ask(show)
        state, key = str(answer.get("state") or state), str(answer.get("key") or "")
        users = [_user(u) for u in await self._users()]
        user = self._user_of(show, key, users)
        if state == keys.OK:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(show.device))
            self.tried.discard(show.device)
            if found := keys.owner(key, users):
                self._record(show, found[0].id)
            return
        kind = keys.why(state, key, show.encrypted, user)
        if kind == keys.TRY_AGAIN:
            if show.device in self.tried:
                # Tried once and still turned away: another try would only be another failed login.
                return
            self.tried.add(show.device)
            LOGGER.info("%s was turned away with a key Home Assistant still has; it tries once more", show.name)
            if await self.async_retry(show) == keys.OK:
                return
            LOGGER.warning("%s is still turned away with a key Home Assistant has; is its Home Assistant "
                           "address this one?", show.name)
            return
        self.tried.discard(show.device)
        if kind is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(show.device))
            return
        hass_user = await self.hass.auth.async_get_user(user.id) if user else None
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id(show.device),
            is_fixable=kind in keys.FIXABLE,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=kind,
            translation_placeholders={"show": show.name, "user": hass_user.name if hass_user else ""},
            data={"device": show.device},
        )

    async def async_retry(self, show: Show) -> str:
        """Has the Show sign in once more with the key it has; answers with how its key stands after."""
        answer = await self.hass.services.async_call(
            "esphome", f"{show_prefix(show.node)}_home_assistant_retry", {}, blocking=True, return_response=True)
        state = str((answer or {}).get("state") or "") if isinstance(answer, dict) else ""
        if state == keys.OK:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(show.device))
        self.seen.pop(show.device, None)  # asked again on the next look, whatever it says
        return state

    async def async_renew(self, show: Show, url: str = "") -> dict:
        """Gives the Show a new key, made for its user, and makes it a user first when it has none. Only for
        an admin: the Repairs issue's button and genbu.show_key. The Show's other keys go once it has
        stored the new one, so a key copied off a lost Show stops working. Raises KeyRefused."""
        if why := keys.home_address(url):
            raise KeyRefused(why)
        answer = await self._ask(show) if show.encrypted else {}
        hass_users = await self._users()
        user = self._user_of(show, str(answer.get("key") or ""), [_user(u) for u in hass_users])
        if why := keys.may_renew(user, show.encrypted):
            raise KeyRefused(why)
        hass_user = next((u for u in hass_users if user and u.id == user.id), None) or await self._make_user(show)
        made = await self.hass.auth.async_create_refresh_token(
            hass_user, client_name=f"Genbu {show.name} {dt_util.now():%Y-%m-%d %H:%M:%S}",
            token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN, access_token_expiration=KEY_LIFE)
        try:
            answer = await self.hass.services.async_call(
                "esphome", f"{show_prefix(show.node)}_home_assistant",
                {"url": url, "token": self.hass.auth.async_create_access_token(made)},
                blocking=True, return_response=True)
        except HomeAssistantError:
            self.hass.auth.async_remove_refresh_token(made)
            raise
        if not (isinstance(answer, dict) and answer.get("stored") is True):
            self.hass.auth.async_remove_refresh_token(made)
            raise KeyRefused("not_stored")
        old = keys.retire(_user(hass_user).tokens, made.id)
        for token in list(hass_user.refresh_tokens.values()):
            if token.id in old:
                self.hass.auth.async_remove_refresh_token(token)
        self._record(show, hass_user.id)
        self.tried.discard(show.device)
        self.seen.pop(show.device, None)
        ir.async_delete_issue(self.hass, DOMAIN, issue_id(show.device))
        LOGGER.info("%s has a new Home Assistant key for %s; %d old one(s) deleted", show.name, hass_user.name,
                    len(old))
        return {"stored": True, "user": hass_user.name}

    async def _make_user(self, show: Show) -> HassUser:
        """A user for a Show that has none, the way the installer makes one: named after the Show, no admin,
        and only from home. Its sign-in name is the installer's, with a password nobody keeps, so it can't
        sign in other than with its key."""
        provider = next((p for p in self.hass.auth.auth_providers if p.type == "homeassistant"), None)
        if provider is None:
            raise KeyRefused("no_provider")
        taken = {n for u in await self._users() for n in _user(u).usernames}
        for name in keys.usernames(show_prefix(show.node)):
            if name in taken:
                continue
            try:
                await provider.async_add_auth(name, keys.password())
                break
            except InvalidUser:  # a sign-in name of nobody's, left over
                continue
        else:
            raise KeyRefused("name_taken")
        credentials = await provider.async_get_or_create_credentials({"username": name})
        user = await self.hass.auth.async_create_user(show.name, group_ids=[GROUP_ID_USER], local_only=True)
        await self.hass.auth.async_link_user(user, credentials)
        LOGGER.info("Made %s a Home Assistant user, %s, home only", show.name, name)
        return user

    def _drop_gone(self) -> None:
        """A Show whose device is gone from Home Assistant takes its issue and its record along. By the
        device, not the Shows found: while ESPHome is still starting, none are."""
        devices = dr.async_get(self.hass)
        for domain, issue in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue.startswith(ISSUE) and devices.async_get(issue.removeprefix(ISSUE)) is None:
                ir.async_delete_issue(self.hass, DOMAIN, issue)
        gone = [d for d in self.recorded if devices.async_get(d) is None]
        for device in gone:
            del self.recorded[device]
            self.seen.pop(device, None)
            self.asked.pop(device, None)
        if gone:
            self._save()
