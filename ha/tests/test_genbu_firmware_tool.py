"""genbu__firmware: who gets it, the admin check in the app, what a status says, and that installing asks
first."""

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm

from custom_components.genbu import llm as genbu_llm

from test_genbu_ha import add_show
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
    hall_up = add_show(hass, "hall", "v1.4.0-staging.60")
    kitchen = device_of(hass, kitchen_up)
    entry = await setup(hass)
    waiting(hass, hall_up, "v1.4.0-staging.60", "v1.4.1-staging.61", "Calendar fixed, see https://example.org/n")
    tool = genbu_llm.FirmwareTool(entry.runtime_data.show(kitchen))
    data = (await tool.async_call(hass, *ask(kitchen, "status"))).data
    assert data["update_waiting"] == "v1.4.1-staging.61"
    assert data["whats_new"] == "Calendar fixed, see"
    shows = {s["name"]: s for s in data["shows"]}
    assert shows["Kitchen"] == {"name": "Kitchen", "version": "v1.4.0-staging.60", "channel": "staging",
                                "online": True, "this_is_you": True}
    assert shows["Hall"]["update_to"] == "v1.4.1-staging.61"


async def test_install_asks_first_then_starts_every_show(hass: HomeAssistant):
    kitchen_up = add_show(hass, "kitchen", "v1.4.0-stable.60")
    hall_up = add_show(hass, "hall", "v1.4.0-stable.60")
    kitchen = device_of(hass, kitchen_up)
    entry = await setup(hass)
    installs = []
    hass.services.async_register("update", "install", lambda call: installs.append(call.data["entity_id"]))
    tool = genbu_llm.FirmwareTool(entry.runtime_data.show(kitchen))

    data = (await tool.async_call(hass, *ask(kitchen, "install", confirm=True))).data
    assert "up to date" in data["installed"]

    waiting(hass, kitchen_up, "v1.4.0-stable.60", "v1.4.1-stable.61")
    waiting(hass, hall_up, "v1.4.0-stable.60", "v1.4.1-stable.61")
    data = (await tool.async_call(hass, *ask(kitchen, "install"))).data
    assert "v1.4.1-stable.61" in data["ask_first"]
    await hass.async_block_till_done()
    assert installs == []

    data = (await tool.async_call(hass, *ask(kitchen, "install", confirm=True))).data
    await hass.async_block_till_done()
    assert sorted(data["installing_on"]) == ["Hall", "Kitchen"]
    assert sorted(installs) == sorted([kitchen_up, hall_up])
