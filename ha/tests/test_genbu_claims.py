"""GENBU-139: a model reply that says something was done, with no tool run in the turn, is flagged."""

import pytest
from homeassistant.components.conversation.chat_log import (AssistantContent, ToolResultContent, UserContent,
                                                            async_get_chat_log)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import chat_session, llm

from custom_components.genbu import claims

MODEL = "conversation.ollama"


def turn(*said) -> tuple[list, object]:
    asked = UserContent(content="Macht die Musik im Schlafzimmer aus")
    return [asked, *said], asked.created


def test_a_claim_without_a_tool_is_flagged():
    contents, at = turn(AssistantContent(agent_id=MODEL, content="Die Musik im Schlafzimmer ist jetzt aus."))
    assert claims.unbacked(contents, at) == (True, (MODEL, "Macht die Musik im Schlafzimmer aus",
                                                    "Die Musik im Schlafzimmer ist jetzt aus."))
    for said in ("Ich habe das Licht ausgeschaltet.", "Läuft.", "Pausiert.", "The music is paused.",
                 "I turned off the light."):
        contents, at = turn(AssistantContent(agent_id=MODEL, content=said))
        assert claims.unbacked(contents, at)[1], said


def test_a_claim_after_a_tool_is_not():
    call = llm.ToolInput(tool_name="HassMediaPause", tool_args={"area": "Schlafzimmer"})
    contents, at = turn(AssistantContent(agent_id=MODEL, tool_calls=[call]),
                        ToolResultContent(agent_id=MODEL, tool_call_id=call.id, tool_name="HassMediaPause",
                                          result=llm.ToolResult(data={"success": True})),
                        AssistantContent(agent_id=MODEL, content="Die Musik ist aus."))
    assert claims.unbacked(contents, at) == (True, None)


def test_questions_local_sentences_and_unfinished_turns_are_left_alone():
    for said in ("Welches Licht meinst du?", "Das weiß ich nicht.", "Okay."):
        contents, at = turn(AssistantContent(agent_id=MODEL, content=said))
        assert claims.unbacked(contents, at) == (True, None), said
    contents, at = turn(AssistantContent(agent_id="conversation.home_assistant", content="Pausiert."))
    assert claims.unbacked(contents, at) == (True, None)
    _, at = turn()
    assert claims.unbacked([UserContent(content="anderes")], at) == (False, None)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(claims, "STEP", 0.01)
    monkeypatch.setattr(claims, "WAIT", 1)


async def test_the_finished_turn_fires_the_event(hass: HomeAssistant, quick):
    fired = []
    hass.bus.async_listen(claims.EVENT, lambda e: fired.append(e.data))
    stop = claims.async_watch(hass)
    with chat_session.async_get_chat_session(hass) as session, async_get_chat_log(hass, session) as log:
        log.async_add_user_content(UserContent(content="Mach die Musik aus"))
        log.async_add_assistant_content_without_tools(AssistantContent(agent_id=MODEL, content="Musik ist aus."))
    await hass.async_block_till_done(wait_background_tasks=True)
    stop()
    assert [(e["agent_id"], e["asked"], e["said"]) for e in fired] == [(MODEL, "Mach die Musik aus",
                                                                        "Musik ist aus.")]
