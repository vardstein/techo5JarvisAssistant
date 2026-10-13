"""genbu__firmware: who gets it, the admin check in the app, what a status says, and that installing asks
first."""

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm

from custom_components.genbu import llm as genbu_llm
from custom_components.genbu.house import manifest_url, tag_url

from conftest import RELEASES
from test_genbu_ha import add_show, read, set_show
from test_genbu_recipe_tool import device_of, setup, tool_names

DOMAIN = "genbu"


def waiting(hass: HomeAssistant, update_entity: str, installed: str, latest: str, summary: str = "") -> None:
    hass.states.async_set(update_entity, "on", {"installed_version": installed, "latest_version": latest,
                                                "release_summary": summary})


def ask(device: str | None, action: str, user_id: str | None = None, **args) -> tuple:
    context = llm.LLMContext("test", Context(user_id=user_id), "de", None, device)
    return llm.ToolInput("genbu__firmware", {"action": action, **args}), context


async def test_who_gets_the_firmware_tool(hass: HomeAssistant):
    kitchen = device_of(hass, add_show(hass, "kitchen", "v1.4.0-stable.60"))
    entry = await setup(hass)
    assert "genbu__firmware" in await tool_names(hass, kitchen)
    assert "genbu__firmware" in await tool_names(hass, None)  # the app; the admin check comes when it is used
    assert "genbu__firmware" not in await tool_names(hass, "some-voice-pe")
    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_voice_updates_{kitchen}")
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert "genbu__firmware" not in await tool_names(hass, kitchen)


async def test_a_house_without_shows_has_no_firmware_tool(hass: HomeAssistant):
    await setup(hass)
    assert "genbu__firmware" not in await tool_names(hass, None)


async def test_in_the_app_only_an_admin(hass: HomeAssistant, hass_admin_user, hass_read_only_user):
    add_show(hass, "kitchen", "v1.4.0-stable.60")
    await setup(hass)
    tool = genbu_llm.FirmwareTool(None)
    for user, error in ((None, True), (hass_read_only_user.id, True), (hass_admin_user.id, False)):
        result = await tool.async_call(hass, *ask(None, "status", user))
        assert result.error is error, user


async def test_status_says_versions_and_whats_new(hass: HomeAssistant):
    kitchen_up = add_show(hass, "kitchen", "v1.4.0-staging.60")
    add_show(hass, "hall", "v1.4.0-staging.60")
    kitchen = device_of(hass, kitchen_up)
    entry = await setup(hass)
    RELEASES[manifest_url("staging")] = {"version": "v1.4.1-staging.61",
                                         "title": "Calendar fixed, see https://example.org/n"}
    await read(hass, entry)
    tool = genbu_llm.FirmwareTool(entry.runtime_data.show(kitchen))
    data = (await tool.async_call(hass, *ask(kitchen, "status"))).data
    assert (data["house_version"], data["channel"]) == ("v1.4.0-staging.60", "staging")
    assert data["update_waiting"] == "v1.4.1-staging.61"
    assert data["whats_new"] == "Calendar fixed, see"
    assert (data["install_at_night"], data["version_before"]) == (False, None)
    shows = {s["name"]: s for s in data["shows"]}
    assert shows["Kitchen"] == {"name": "Kitchen", "version": "v1.4.0-staging.60", "channel": "staging",
                                "online": True, "this_is_you": True}


async def test_a_show_behind_the_house_catches_up(hass: HomeAssistant):
    """No house update, but one Show is still behind: its own update is the one waiting."""
    add_show(hass, "kitchen", "v1.4.1-stable.61")
    hall_up = add_show(hass, "hall", "v1.4.0-stable.60")
    entry = await setup(hass)
    waiting(hass, hall_up, "v1.4.0-stable.60", "v1.4.1-stable.61", "Calendar fixed")
    kitchen = entry.runtime_data.shows[0]
    data = (await genbu_llm.FirmwareTool(kitchen).async_call(hass, *ask(kitchen.device, "status"))).data
    assert data["update_waiting"] == "v1.4.1-stable.61"
    assert {s["name"]: s.get("update_to") for s in data["shows"]}["Hall"] == "v1.4.1-stable.61"


async def test_install_asks_first_then_moves_the_house(hass: HomeAssistant):
    kitchen_up = add_show(hass, "kitchen", "v1.4.0-stable.60")
    add_show(hass, "hall", "v1.4.0-stable.60")
    kitchen = device_of(hass, kitchen_up)
    entry = await setup(hass)
    tool = genbu_llm.FirmwareTool(entry.runtime_data.show(kitchen))

    data = (await tool.async_call(hass, *ask(kitchen, "install", confirm=True))).data
    assert "up to date" in data["installed"]

    RELEASES[manifest_url("stable")] = {"version": "v1.4.1-stable.61"}
    await read(hass, entry)
    data = (await tool.async_call(hass, *ask(kitchen, "install"))).data
    assert "v1.4.1-stable.61" in data["ask_first"]
    assert entry.runtime_data.house.version == "v1.4.0-stable.60"

    data = (await tool.async_call(hass, *ask(kitchen, "install", confirm=True))).data
    await hass.async_block_till_done()
    assert sorted(data["installing_on"]) == ["Hall", "Kitchen"]
    assert entry.runtime_data.house.version == "v1.4.1-stable.61"


async def test_go_back_skip_channel_and_night(hass: HomeAssistant):
    kitchen_up = add_show(hass, "kitchen", "v1.4.0-stable.60")
    kitchen = device_of(hass, kitchen_up)
    entry = await setup(hass)
    hub = entry.runtime_data
    tool = genbu_llm.FirmwareTool(hub.show(kitchen))

    async def say(action, **args):
        result = await tool.async_call(hass, *ask(kitchen, action, **args))
        await hass.async_block_till_done()
        return result.data

    assert "name one" in (await say("go_back", confirm=True))["went_back"]
    assert "nothing" in (await say("skip"))["skipped"]

    RELEASES[manifest_url("stable")] = {"version": "v1.4.1-stable.61"}
    await read(hass, entry)
    assert (await say("skip"))["skipped"] == "v1.4.1-stable.61"
    assert hub.waiting == ""

    RELEASES[manifest_url("stable")] = {"version": "v1.4.2-stable.62"}
    await read(hass, entry)
    await say("install", confirm=True)
    set_show(hass, kitchen_up, kitchen_up.replace("update.", "sensor.").replace("_firmware", "_committed_version"),
             "v1.4.2-stable.62")
    await hub.async_refresh()
    await hass.async_block_till_done()

    RELEASES[tag_url("v1.4.0-stable.60")] = {"version": "v1.4.0-stable.60"}
    assert "v1.4.0-stable.60" in (await say("go_back"))["ask_first"]
    data = await say("go_back", confirm=True)
    assert data["house_version"] == "v1.4.0-stable.60" and hub.waiting == ""

    RELEASES[manifest_url("dev")] = {"version": "v1.4.3-dev.70"}
    assert "dev" in (await say("channel", channel="dev"))["ask_first"]
    data = await say("channel", channel="dev", confirm=True)
    assert data == {"channel": "dev", "update_waiting": "v1.4.3-dev.70"}
    assert hass.states.get("select.genbu_release_channel").state == "dev"

    assert (await say("night", on=True)) == {"install_at_night": True}
    assert hass.states.get("switch.genbu_install_at_night").state == "on"
