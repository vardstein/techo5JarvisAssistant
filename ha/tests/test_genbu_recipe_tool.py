"""The recipe search inside Genbu Assistant (1.4.0): who gets genbu__recipe, the Recipes switch, the
option that sets it up, the address taken over from Genbu Rezepte, and what a lookup does."""

import socket

import pytest
from homeassistant.components.llm import async_get_tools
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.genbu import llm as genbu_llm

from test_genbu_ha import add_show

DOMAIN = "genbu"
SEARCH = "http://10.0.0.5:8080"
RECIPE = {"title": "Tacos", "source": "kueche.example.org", "servings": "4", "minutes": 30, "image": "",
          "url": "https://kueche.example.org/tacos", "ingredients": ["500 g Hackfleisch", "8 Tortillas"],
          "steps": ["Braten.", "Füllen."]}


async def setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    await async_setup_component(hass, "repairs", {})
    await async_setup_component(hass, "llm", {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def device_of(hass: HomeAssistant, update_entity: str) -> str:
    return er.async_get(hass).async_get(update_entity).device_id


def recipe_page(hass: HomeAssistant, node: str) -> list:
    """The action a Show's firmware with the recipe page brings; returns the calls it gets."""
    calls = []
    hass.services.async_register("esphome", f"{node}_recipe_show", lambda call: calls.append(call))
    return calls


async def tool_names(hass: HomeAssistant, device: str | None, api: str = llm.LLM_API_ASSIST,
                     language: str = "de") -> list[str]:
    context = llm.LLMContext(platform="test", context=Context(), language=language, assistant="conversation",
                             device_id=device)
    return [t.name for t in (await async_get_tools(hass, context, api)).tools]


async def test_only_a_show_with_recipes_gets_the_tool(hass: HomeAssistant):
    kitchen = device_of(hass, add_show(hass, "kitchen", "v1.4.0-dev.1"))
    hall = device_of(hass, add_show(hass, "hall", "v1.3.0-stable.50"))
    recipe_page(hass, "kitchen")
    entry = await setup(hass, {"searxng_url": SEARCH + "/"})
    assert entry.runtime_data.searxng == SEARCH
    assert "genbu__recipe" in await tool_names(hass, kitchen)
    assert "genbu__recipe" not in await tool_names(hass, hall)  # no recipe page in its firmware
    assert "genbu__recipe" not in await tool_names(hass, None)  # the app, a phone
    assert "genbu__recipe" not in await tool_names(hass, "some-voice-pe")
    assert genbu_llm.async_get_tools(hass, llm.LLMContext("t", Context(), "de", None, kitchen), "other") is None

    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_recipes_{kitchen}")
    assert hass.states.get(switch).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"
    assert "genbu__recipe" not in await tool_names(hass, kitchen)

    # Switched on by hand, the hall gets it too; the screen just stays as it is.
    hall_switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_recipes_{hall}")
    await hass.services.async_call("switch", "turn_on", {"entity_id": hall_switch}, blocking=True)
    assert "genbu__recipe" in await tool_names(hass, hall)
    assert entry.runtime_data.house.switch(hall, "recipes") is True


async def test_no_search_no_tool_no_sensor_no_recipes_switch(hass: HomeAssistant):
    kitchen = device_of(hass, add_show(hass, "kitchen", "v1.4.0-dev.1"))
    recipe_page(hass, "kitchen")
    entry = await setup(hass)
    assert "genbu__recipe" not in await tool_names(hass, kitchen)
    assert hass.states.get("sensor.genbu_recipe") is None
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_recipes_{kitchen}") is None
    voice = reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_voice_updates_{kitchen}")
    assert hass.states.get(voice).state == "on"


async def test_the_options_set_the_search_up(hass: HomeAssistant):
    kitchen = device_of(hass, add_show(hass, "kitchen", "v1.4.0-dev.1"))
    recipe_page(hass, "kitchen")
    entry = await setup(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    for bad in ("searx.local", "ftp://10.0.0.5/", "http://"):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"searxng_url": bad})
        assert result["errors"] == {"searxng_url": "searxng"}, bad
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"searxng_url": SEARCH})
    while result["type"] == "form":  # whatever else the options ask, left as it is
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    assert entry.options["searxng_url"] == SEARCH
    assert hass.states.get("sensor.genbu_recipe") is not None
    assert "genbu__recipe" in await tool_names(hass, entry.runtime_data.show(kitchen).device)


@pytest.mark.parametrize(("options", "want"), [({}, SEARCH), ({"searxng_url": ""}, "")])
async def test_the_address_comes_over_from_genbu_rezepte_once(hass: HomeAssistant, options: dict, want: str):
    MockConfigEntry(domain="jarvis_recipe", data={"searxng_url": SEARCH + "/"}).add_to_hass(hass)
    entry = await setup(hass, options)
    assert entry.options["searxng_url"] == want


async def test_genbu_rezepte_stands_back(hass: HomeAssistant):
    await async_setup_component(hass, "repairs", {})
    old = MockConfigEntry(domain="jarvis_recipe", data={"searxng_url": SEARCH})
    old.add_to_hass(hass)
    assert await hass.config_entries.async_setup(old.entry_id)
    await hass.async_block_till_done()
    context = llm.LLMContext("test", Context(), "de", None, None)
    api = await llm.async_get_api(hass, "jarvis_recipe", context)
    assert [t.name for t in api.tools] == ["rezept_zeigen"]
    await setup(hass, {"searxng_url": SEARCH})
    api = await llm.async_get_api(hass, "jarvis_recipe", context)
    assert api.tools == []


async def test_a_lookup_shows_the_recipe_and_tells_the_assistant_little(hass: HomeAssistant, monkeypatch):
    kitchen = device_of(hass, add_show(hass, "kitchen", "v1.4.0-dev.1"))
    calls = recipe_page(hass, "kitchen")
    entry = await setup(hass, {"searxng_url": SEARCH})
    searched = []

    async def look_up(dish, search, fetch, lang):
        searched.append((dish, lang))
        assert entry.runtime_data.cooking == {kitchen}
        return dict(RECIPE), []

    monkeypatch.setattr(genbu_llm.rec, "look_up", look_up)
    tool = genbu_llm.RecipeTool(entry.runtime_data.show(kitchen))
    context = llm.LLMContext("test", Context(), "en-GB", None, kitchen)
    result = await tool.async_call(hass, llm.ToolInput("genbu__recipe", {"dish": " Tacos "}), context)
    await hass.async_block_till_done()
    assert searched == [("Tacos", "en")]
    assert not result.error
    assert result.data["dish"] == "Tacos" and result.data["on_screen"] is True
    assert "page_text" not in result.data
    assert len(calls) == 1
    assert hass.states.get("sensor.genbu_recipe").attributes["ingredients"] == RECIPE["ingredients"]
    assert entry.runtime_data.cooking == set()

    result = await tool.async_call(hass, llm.ToolInput("genbu__recipe", {"dish": "Tacos", "read_out": True}),
                                   context)
    assert result.data["page_text"]["steps"] == RECIPE["steps"]

    entry.runtime_data.cooking.add(kitchen)  # a lookup still running
    result = await tool.async_call(hass, llm.ToolInput("genbu__recipe", {"dish": "Tacos"}), context)
    assert result.error


async def test_the_resolver_keeps_only_public_addresses():
    resolver = genbu_llm.PublicResolver()

    async def answer(host, port, family):
        return [{"hostname": host, "host": a, "port": port, "family": family, "proto": 0, "flags": 0}
                for a in addresses]

    resolver._inner.resolve = answer
    addresses = ["93.184.215.14", "192.168.1.1"]
    assert [r["host"] for r in await resolver.resolve("example.org", 443, socket.AF_INET)] == ["93.184.215.14"]
    addresses = ["10.0.0.5", "127.0.0.1"]
    with pytest.raises(OSError):
        await resolver.resolve("rebind.example.org", 443, socket.AF_INET)
    await resolver.close()
