"""The installer's "Genbu Assistant" step (GENBU-109) against Home Assistant's own Assist pipelines: the
rename goes through as the installer sends it, and every device that was on the old name follows."""

import asyncio
import sys
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from jarvis_crown import home_assistant as ha_api  # noqa: E402


class Bridge:
    """The installer's Home Assistant client, as far as the step uses it, over the test's own websocket and
    states. The step is plain blocking code, so it runs on a worker thread and hops over to the loop."""

    def __init__(self, hass: HomeAssistant, ws, loop: asyncio.AbstractEventLoop):
        self.hass, self.client, self.loop = hass, ws, loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def ws(self, message: dict):
        async def call():
            await self.client.send_json_auto_id(message)
            reply = await self.client.receive_json()
            if not reply["success"]:
                raise ha_api.HomeAssistantError(str(reply.get("error")))
            return reply["result"]
        return self._run(call())

    def request(self, method: str, path: str, body=None):
        if method == "GET" and path.startswith("/api/states/"):
            state = self.hass.states.get(path[len("/api/states/"):])
            return state.as_dict() if state else None
        if method == "POST" and path == "/api/services/select/select_option":
            return self._run(self.hass.services.async_call("select", "select_option", body, blocking=True))
        raise AssertionError(f"unexpected {method} {path}")


async def test_the_assistant_is_renamed_and_the_devices_on_it_follow(hass: HomeAssistant, hass_ws_client):
    from homeassistant.components.assist_pipeline.select import AssistPipelineSelect
    from pytest_homeassistant_custom_component.common import setup_test_component_platform

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "config", {})
    assert await async_setup_component(hass, "assist_pipeline", {})
    ws = await hass_ws_client(hass)
    made = []
    for name in ("Jarvis", "Basic"):
        await ws.send_json_auto_id({
            "type": "assist_pipeline/pipeline/create", "name": name, "language": "de",
            "conversation_engine": "conversation.home_assistant", "conversation_language": "de",
            "stt_engine": None, "stt_language": None, "tts_engine": None, "tts_language": None, "tts_voice": None,
            "wake_word_entity": None, "wake_word_id": None, "prefer_local_intents": True})
        reply = await ws.receive_json()
        assert reply["success"], reply
        made.append(reply["result"]["id"])
    jarvis_id = made[0]

    show = AssistPipelineSelect(hass, "esphome", "aa:bb")
    satellite = AssistPipelineSelect(hass, "wyoming", "cc:dd")
    other = AssistPipelineSelect(hass, "wyoming", "ee:ff")
    for entity, eid in ((show, "select.genbu_show_5_assistant"), (satellite, "select.kitchen_assistant"),
                        (other, "select.office_assistant")):
        entity.entity_id = eid
    setup_test_component_platform(hass, "select", [show, satellite, other])
    assert await async_setup_component(hass, "select", {"select": {"platform": "test"}})
    await hass.async_block_till_done()
    for eid, option in (("select.genbu_show_5_assistant", "Jarvis"), ("select.kitchen_assistant", "Jarvis"),
                        ("select.office_assistant", "Basic")):
        await hass.services.async_call("select", "select_option", {"entity_id": eid, "option": option},
                                       blocking=True)

    kept = {}

    def keep(what, config):
        kept[what] = config
        return f"/backups/{what}.json"

    bridge = Bridge(hass, ws, asyncio.get_running_loop())
    said = await hass.async_add_executor_job(
        ha_api._name_assistant, bridge, ["select.genbu_show_5_assistant"], True, None, keep)
    await hass.async_block_till_done()

    assert said.startswith("the assistant Jarvis is now called Genbu Assistant"), said
    assert kept["assist-pipeline-Jarvis"]["pipeline"]["name"] == "Jarvis"
    assert kept["assist-pipeline-Jarvis"]["selects_on_it"] == ["select.genbu_show_5_assistant",
                                                              "select.kitchen_assistant"]
    # The same pipeline, under its new name, with nothing else about it changed.
    await ws.send_json_auto_id({"type": "assist_pipeline/pipeline/get", "pipeline_id": jarvis_id})
    renamed = (await ws.receive_json())["result"]
    assert renamed["name"] == "Genbu Assistant"
    assert {k: v for k, v in renamed.items() if k != "name"} == \
        {k: v for k, v in kept["assist-pipeline-Jarvis"]["pipeline"].items() if k != "name"}
    # Home Assistant put both back on "preferred" when the name went; the step put them on the new one.
    assert hass.states.get("select.genbu_show_5_assistant").state == "Genbu Assistant"
    assert hass.states.get("select.kitchen_assistant").state == "Genbu Assistant"
    assert hass.states.get("select.office_assistant").state == "Basic"

    # A second run finds it done.
    again = await hass.async_add_executor_job(
        ha_api._name_assistant, bridge, ["select.genbu_show_5_assistant"], True, None, keep)
    assert again == "the assistant is already called Genbu Assistant"
