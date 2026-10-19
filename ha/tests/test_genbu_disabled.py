"""A Show disabled in Home Assistant (GENBU-261), inside a real Home Assistant: Genbu switches its user off,
which deletes its keys, so nothing of Home Assistant works for it while it is disabled. Enabled again, its
user is switched back on and the Show gets a new key by itself once it is back online. Only a proper Show
user is switched; an admin's, or one that may sign in from outside, is left alone."""

import pytest
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.auth.models import Credentials, User
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration

from tests.test_genbu_ha import DOMAIN, add_show, setup

KEY = "sensor.hall_home_assistant_key"


@pytest.fixture(autouse=True)
def esphome(hass: HomeAssistant, hass_owner_user):
    """ESPHome that sets up and unloads its entries without a device, in a house that has its owner, so
    the Show's user isn't the first one, which Home Assistant would make the owner."""
    mock_integration(hass, MockModule("esphome", async_setup_entry=_true, async_unload_entry=_true))


async def _true(hass, entry) -> bool:
    return True


class Show:
    """The Show's key actions: how its key stands, and storing a new one."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.state, self.stored = "ok", []
        hass.services.async_register("esphome", "hall_home_assistant_key", self.key,
                                     supports_response=SupportsResponse.ONLY)
        hass.services.async_register("esphome", "hall_home_assistant", self.store,
                                     supports_response=SupportsResponse.ONLY)

    async def key(self, call: ServiceCall):
        return {"state": self.state, "key": ""}

    async def store(self, call: ServiceCall):
        self.stored.append(call.data["token"])
        self.state = "ok"
        return {"stored": True}


async def a_show(hass: HomeAssistant) -> tuple[MockConfigEntry, str]:
    """The Show hall, encrypted, with its key sensor; returns its ESPHome entry and device."""
    up = add_show(hass, "hall", "v1.0.3")
    ents = er.async_get(hass)
    device = ents.async_get(up).device_id
    entry = hass.config_entries.async_get_entry(ents.async_get(up).config_entry_id)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "noise_psk": "x"})
    ents.async_get_or_create("sensor", "esphome", "aa:hall-sensor-home_assistant_key", config_entry=entry,
                             device_id=device, suggested_object_id="hall_home_assistant_key")
    hass.states.async_set(KEY, "ok")
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry, device


async def show_user(hass: HomeAssistant, admin: bool = False, local_only: bool = True) -> User:
    """The Show's user the way the installer makes it, with its key."""
    user = await hass.auth.async_create_user("show_hall", local_only=local_only,
                                             group_ids=[GROUP_ID_ADMIN if admin else GROUP_ID_USER])
    user.credentials.append(Credentials(auth_provider_type="homeassistant", auth_provider_id=None,
                                        data={"username": "show_hall"}, is_new=False))
    await hass.auth.async_create_refresh_token(user, client_name="Genbu hall",
                                               token_type="long_lived_access_token")
    return user


async def look(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


def off(entry: MockConfigEntry) -> dict:
    return entry.runtime_data.house.data["off"]


def issues(hass: HomeAssistant) -> list[str]:
    return [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN and i.startswith("key_")]


async def test_disabled_its_user_is_off_and_enabled_it_gets_a_new_key(hass: HomeAssistant):
    _, device = await a_show(hass)
    user = await show_user(hass)
    show = Show(hass)
    genbu = await setup(hass)

    dr.async_get(hass).async_update_device(device, disabled_by=dr.DeviceEntryDisabler.USER)
    await look(hass, genbu)

    user = await hass.auth.async_get_user(user.id)
    assert not user.is_active
    assert not user.refresh_tokens  # its key is dead, so dashboard and DashCast turn it away
    assert off(genbu) == {device: {"user": user.id}}
    assert issues(hass) == []  # disabled on purpose: nothing to fix

    dr.async_get(hass).async_update_device(device, disabled_by=None)
    hass.states.async_set(KEY, "off")  # back online: its key is turned away until it has a new one
    show.state = "gone"
    await look(hass, genbu)

    user = await hass.auth.async_get_user(user.id)
    assert user.is_active
    assert len(show.stored) == 1
    assert len(user.refresh_tokens) == 1
    assert off(genbu) == {}
    assert issues(hass) == []


async def test_enabled_but_offline_waits_for_the_show(hass: HomeAssistant):
    _, device = await a_show(hass)
    user = await show_user(hass)
    show = Show(hass)
    genbu = await setup(hass)
    dr.async_get(hass).async_update_device(device, disabled_by=dr.DeviceEntryDisabler.USER)
    await look(hass, genbu)

    dr.async_get(hass).async_update_device(device, disabled_by=None)
    hass.states.async_set(KEY, "unavailable")
    await look(hass, genbu)

    assert (await hass.auth.async_get_user(user.id)).is_active
    assert off(genbu) == {device: {"user": user.id, "back": True}}
    assert show.stored == []

    # Disabled again before it came back: switched off again.
    dr.async_get(hass).async_update_device(device, disabled_by=dr.DeviceEntryDisabler.USER)
    await look(hass, genbu)
    assert not (await hass.auth.async_get_user(user.id)).is_active
    assert off(genbu) == {device: {"user": user.id}}


async def test_the_esphome_entry_or_the_genbu_device_disabled_counts_too(hass: HomeAssistant):
    entry, device = await a_show(hass)
    user = await show_user(hass)
    Show(hass)
    genbu = await setup(hass)

    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await look(hass, genbu)
    assert not (await hass.auth.async_get_user(user.id)).is_active

    await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    await look(hass, genbu)
    assert (await hass.auth.async_get_user(user.id)).is_active

    # The genbu device alone: ESPHome stays connected, and the Show's sensor says its key is gone.
    ours = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, device), genbu.entry_id)
    dr.async_get(hass).async_update_device(ours.id, disabled_by=dr.DeviceEntryDisabler.USER)
    hass.states.async_set(KEY, "off")
    await look(hass, genbu)
    assert not (await hass.auth.async_get_user(user.id)).is_active
    assert issues(hass) == []


@pytest.mark.parametrize("more", [{"admin": True}, {"local_only": False}])
async def test_an_admin_or_a_user_from_outside_is_left_alone(hass: HomeAssistant, more: dict):
    _, device = await a_show(hass)
    user = await show_user(hass, **more)
    Show(hass)
    genbu = await setup(hass)

    dr.async_get(hass).async_update_device(device, disabled_by=dr.DeviceEntryDisabler.USER)
    await look(hass, genbu)
    user = await hass.auth.async_get_user(user.id)
    assert user.is_active and len(user.refresh_tokens) == 1
    assert off(genbu) == {device: {"user": ""}}

    dr.async_get(hass).async_update_device(device, disabled_by=None)
    await look(hass, genbu)
    assert off(genbu) == {}
