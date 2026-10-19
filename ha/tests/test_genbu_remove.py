"""Deleting a Show in Home Assistant (GENBU-223), inside a real Home Assistant: Delete on the Show's Genbu
device takes its users, automations and scripts out of the house, asks the Show to reset to setup, then
removes its ESPHome entry and says what went and what stayed. A Show that is offline or switched off in
ESPHome waits with a Repairs issue and is reset once it is back; switching it off alone removes nothing."""

from types import SimpleNamespace

import pytest
from homeassistant.auth.models import Credentials
from homeassistant.components import persistent_notification
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, MockUser, mock_integration

from tests.test_genbu_ha import DOMAIN, add_show, set_show, setup

@pytest.fixture(autouse=True)
def esphome(hass: HomeAssistant, tmp_path):
    """ESPHome and Music Assistant that set up and unload their entries without a device, and a config folder
    of the test's own for automations.yaml and scripts.yaml."""
    for domain in ("esphome", "music_assistant"):
        mock_integration(hass, MockModule(domain, async_setup_entry=_true, async_unload_entry=_true))
    hass.config.config_dir = str(tmp_path)


async def _true(hass, entry) -> bool:
    return True


class Show:
    """What the Show says to reset_to_setup: answers, refuses as echod does, or doesn't answer at all."""

    def __init__(self, hass: HomeAssistant, says: list[str] | None = None) -> None:
        self.says, self.calls = list(says or []), []
        hass.services.async_register("esphome", "hall_reset_to_setup", self.answer,
                                     supports_response=SupportsResponse.ONLY)

    async def answer(self, call: ServiceCall):
        self.calls.append(dict(call.data))
        if self.says:
            raise HomeAssistantError(translation_domain="esphome", translation_key="action_call_failed",
                                     translation_placeholders={"call_name": "reset_to_setup",
                                                              "error": self.says.pop(0)})
        return {"reset": True, "keep_wifi": call.data["keep_wifi"]}


class Mass:
    """Music Assistant's client, with the Show's user and player and somebody else's."""

    def __init__(self) -> None:
        self.users = [{"user_id": "u1", "username": "hall", "role": "user"},
                      {"user_id": "u2", "username": "jo", "role": "admin"}]
        self.players = [{"player_id": "p1", "name": "Hall", "provider": "sendspin",
                         "device_info": {"mac_address": "AA:BB:CC:00:11:22"}},
                        {"player_id": "p2", "name": "Kitchen", "provider": "sonos"}]
        self.done: list[tuple[str, dict]] = []

    async def send_command(self, command: str, **args):
        if command == "auth/users":
            return self.users
        if command == "players/all":
            return self.players
        self.done.append((command, args))
        return None


async def a_show(hass: HomeAssistant, online: bool = True) -> tuple[MockConfigEntry, str, str]:
    """The Show hall with its ESPHome entry set up; returns the entry, its ESPHome device and its firmware."""
    up = add_show(hass, "hall", "v1.0.3")
    ents = er.async_get(hass)
    device = ents.async_get(up).device_id
    entry = hass.config_entries.async_get_entry(ents.async_get(up).config_entry_id)
    dr.async_get(hass).async_update_device(
        device, new_connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:00:11:22")})
    entry.mock_state(hass, ConfigEntryState.LOADED)  # as if ESPHome had connected to it
    if not online:
        set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
                 "v1.0.3", online=False)
    return entry, device, up


def write(hass: HomeAssistant, name: str, items) -> None:
    with open(hass.config.path(name), "w") as f:
        f.write(dump(items))


def house_pieces(hass: HomeAssistant, device: str) -> None:
    """What the installer put in the house for the Show, and some of the house's own."""
    write(hass, "automations.yaml", [
        {"id": "techo5_turn_room_down_hall", "alias": "Hall turns the room down"},
        {"id": "lights_by_hall", "alias": "Lights", "triggers": [{"device_id": device}]},
        {"id": "unrelated", "alias": "Someone's own"},
    ])
    write(hass, "scripts.yaml", {"hall_morning": {"alias": "Hall's morning"}, "tea": {"alias": "Tea"}})
    label = lr.async_get(hass).async_create("Genbu Show: hall")
    ents = er.async_get(hass)
    ents.async_get_or_create("automation", "automation", "techo5_turn_room_down_hall")
    ents.async_get_or_create("automation", "automation", "unrelated")
    script = ents.async_get_or_create("script", "script", "hall_morning")
    ents.async_update_entity(script.entity_id, labels={label.label_id})
    ents.async_get_or_create("script", "script", "tea")


def users(hass: HomeAssistant) -> tuple[MockUser, MockUser]:
    """The Show's user from the installer, and the house's owner somebody named like its second one."""
    def named(username: str, **more) -> MockUser:
        user = MockUser(name=username, **more).add_to_hass(hass)
        user.credentials.append(Credentials(auth_provider_type="homeassistant", auth_provider_id=None,
                                            data={"username": username}, is_new=False))
        return user
    return named("show_hall"), named("show_hall-2", is_owner=True)


def music(hass: HomeAssistant) -> Mass:
    entry = MockConfigEntry(domain="music_assistant", title="Music Assistant")
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(mass=Mass())
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry.runtime_data.mass


async def delete(hass: HomeAssistant, hass_ws_client, device: str) -> dict:
    """Delete on the Show's Genbu device, the way the device page does it."""
    genbu = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    ours = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, device), genbu)
    assert ours is not None, "the Show has no Genbu device to delete"
    await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "config/device_registry/remove", "device_id": ours.id})
    answer = await client.receive_json()
    await hass.async_block_till_done(wait_background_tasks=True)
    return answer


def notification(hass: HomeAssistant) -> str:
    note = persistent_notification._async_get_or_create_notifications(hass).get(f"{DOMAIN}_removed_hall")
    return note["message"] if note else ""


def leaving(hass: HomeAssistant) -> dict:
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data.house.data["leaving"]


async def test_a_show_online_is_reset_and_removed(hass: HomeAssistant, hass_ws_client):
    entry, device, _ = await a_show(hass)
    house_pieces(hass, device)
    plain, owner = users(hass)
    mass = music(hass)
    show = Show(hass)
    await setup(hass)

    assert (await delete(hass, hass_ws_client, device))["success"]

    assert show.calls == [{"confirm": "reset", "keep_wifi": True}]
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert dr.async_get(hass).async_get(device) is None
    assert leaving(hass) == {}
    assert not ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    # The Show's own user goes; an owner named like it stays.
    assert await hass.auth.async_get_user(plain.id) is None
    assert await hass.auth.async_get_user(owner.id) is not None
    # Ours and the labelled script go; what only uses the Show, and the house's own, stay.
    assert [a["id"] for a in load_yaml(hass.config.path("automations.yaml"))] == ["lights_by_hall", "unrelated"]
    assert list(load_yaml(hass.config.path("scripts.yaml"))) == ["tea"]
    ents = er.async_get(hass)
    assert ents.async_get_entity_id("automation", "automation", "techo5_turn_room_down_hall") is None
    assert ents.async_get_entity_id("script", "script", "hall_morning") is None
    assert ents.async_get_entity_id("script", "script", "tea")
    assert lr.async_get(hass).async_get_label_by_name("Genbu Show: hall") is None
    # Music Assistant: the Show's plain user and its player, nobody else's.
    assert mass.done == [("auth/user/delete", {"user_id": "u1"}), ("config/players/remove", {"player_id": "p1"})]

    said = notification(hass)
    assert said.startswith("**Hall was removed.**")
    assert "- Home Assistant user 'show_hall'\n" in said
    assert "- Home Assistant user 'show_hall-2': an admin, never removed here" in said
    assert "it restarts in setup mode, on the same Wi-Fi" in said
    assert "- Music Assistant user 'hall'" in said and "- Music Assistant player 'Hall'" in said
    assert "- script hall_morning" in said and "- automation techo5_turn_room_down_hall" in said
    assert "uses it; left as it is" not in said  # the automation component isn't loaded here
    assert "A copy of what went is in .storage/genbu.removed.hall." in said
    # The copy has what went, without anybody's tokens.
    kept = await Store(hass, 1, f"{DOMAIN}.removed.hall").async_load()
    assert [a["id"] for a in kept["automations"].values()] == ["techo5_turn_room_down_hall"]
    assert list(kept["scripts"]) == ["hall_morning"]
    assert {u["name"] for u in kept["home_assistant_users"]} == {"show_hall", "show_hall-2"}
    assert "token" not in str(kept).lower()


async def test_what_only_uses_the_show_stays_and_is_named(hass: HomeAssistant, hass_ws_client):
    """An automation of the house's own that uses the Show isn't ours to remove: it stays, and the
    notification names it."""
    _, device, up = await a_show(hass)
    Show(hass)
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "lights_by_hall", "alias": "Lights by the hall", "triggers": [{"trigger": "state", "entity_id": up}],
         "actions": []}]})
    await setup(hass)

    await delete(hass, hass_ws_client, device)

    assert "- automation.lights_by_the_hall uses it; left as it is" in notification(hass)
    assert hass.states.get("automation.lights_by_the_hall") is not None


async def test_an_offline_show_waits_and_is_reset_when_back(hass: HomeAssistant, hass_ws_client):
    entry, device, up = await a_show(hass, online=False)
    show = Show(hass)
    await setup(hass)

    assert (await delete(hass, hass_ws_client, device))["success"]

    assert show.calls == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    assert "hall" in leaving(hass) and not notification(hass)
    # Out of the house meanwhile: no settings, no music, no key care.
    hub = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert [s.node for s in hub.shows] == []

    # The issue doesn't outlive a restart by itself; Genbu puts it back on its first look.
    ir.async_delete_issue(hass, DOMAIN, "leaving_hall")
    genbu = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_reload(genbu.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    assert show.calls == []
    hub = genbu.runtime_data

    set_show(hass, up, up.replace("update.", "sensor.").replace("_firmware", "_committed_version"), "v1.0.3")
    await hub.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert show.calls == [{"confirm": "reset", "keep_wifi": True}]
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert not ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    assert notification(hass).startswith("**Hall was removed.**")


async def test_a_system_that_cant_keep_the_wifi_is_reset_without_it(hass: HomeAssistant, hass_ws_client):
    _, device, _ = await a_show(hass)
    show = Show(hass, says=["nothing was changed: this device's system can't keep its Wi-Fi through a reset; "
                            "update it first, or reset without keeping the Wi-Fi"])
    await setup(hass)

    await delete(hass, hass_ws_client, device)

    assert show.calls == [{"confirm": "reset", "keep_wifi": True}, {"confirm": "reset", "keep_wifi": False}]
    assert "Wi-Fi erased too (its system can't keep it)" in notification(hass)


async def test_a_system_that_cant_be_reset_is_removed_and_says_so(hass: HomeAssistant, hass_ws_client):
    entry, device, _ = await a_show(hass)
    show = Show(hass, says=["nothing was changed: this device's system can't be reset from Home Assistant"])
    await setup(hass)

    await delete(hass, hass_ws_client, device)

    assert len(show.calls) == 1
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert "its system can't be reset from Home Assistant; reset it on its own screen" in notification(hass)


async def switch(hass: HomeAssistant, entry: MockConfigEntry, on: bool) -> None:
    """The ESPHome entry switched off or on, as its menu does; the mock ESPHome doesn't load or unload."""
    await hass.config_entries.async_set_disabled_by(entry.entry_id, None if on else ConfigEntryDisabler.USER)
    entry.mock_state(hass, ConfigEntryState.LOADED if on else ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_switched_off_waits_and_is_reset_once_switched_on(hass: HomeAssistant, hass_ws_client, caplog):
    """Switched off in ESPHome is switched off: Delete takes it out of the house, and the reset waits until it
    is switched on again, even with the Show itself online."""
    entry, device, up = await a_show(hass)
    show = Show(hass)
    await setup(hass)
    await switch(hass, entry, on=False)

    await delete(hass, hass_ws_client, device)

    assert show.calls == []
    assert "Hall is not reset yet: switched off in ESPHome" in caplog.text
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    assert not notification(hass)

    await switch(hass, entry, on=True)
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert show.calls == [{"confirm": "reset", "keep_wifi": True}]
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert notification(hass).startswith("**Hall was removed.**")


async def test_switching_off_alone_removes_nothing(hass: HomeAssistant):
    entry, device, _ = await a_show(hass)
    show = Show(hass)
    await setup(hass)
    await switch(hass, entry, on=False)

    assert show.calls == [] and leaving(hass) == {}
    assert dr.async_get(hass).async_get(device) is not None


async def test_forget_anyway_removes_a_show_that_never_comes_back(hass: HomeAssistant, hass_ws_client):
    entry, device, _ = await a_show(hass, online=False)
    show = Show(hass)
    await setup(hass)
    await delete(hass, hass_ws_client, device)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    assert issue and issue.is_fixable

    flows = repairs_flow_manager(hass)
    flow = await flows.async_init(DOMAIN, data={"issue_id": "leaving_hall"})
    assert (flow["type"], flow["step_id"]) == ("form", "confirm")
    await flows.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done(wait_background_tasks=True)

    assert show.calls == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert not ir.async_get(hass).async_get_issue(DOMAIN, "leaving_hall")
    assert "it was forgotten without a reset" in notification(hass)


async def test_the_genbu_assistant_device_cant_be_deleted(hass: HomeAssistant):
    from custom_components.genbu import async_remove_config_entry_device
    genbu = await setup(hass)
    house = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, genbu.entry_id), genbu.entry_id)
    assert house is not None
    assert await async_remove_config_entry_device(hass, genbu, house) is False
