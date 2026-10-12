"""Where a Show's music plays, inside a real Home Assistant: the "Music plays on" select, the map on the
house version sensor, a Show moving rooms, and the templates the installer's scripts and automations
use reading that map."""

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import entity_sources
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from tests.test_genbu_ha import DOMAIN, add_show, setup

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from jarvis_crown import voice_kit  # noqa: E402


def show(hass: HomeAssistant, node: str, area: str | None) -> tuple[str, str]:
    """A Show in a room with its voice assistant; returns its device id and satellite."""
    up = add_show(hass, node, "v1.0.3-stable.52")
    entities = er.async_get(hass)
    device = entities.async_get(up).device_id
    entry = entities.async_get(up).config_entry_id
    sat = entities.async_get_or_create("assist_satellite", "esphome", f"aa:{node}-assist_satellite",
                                       config_entry=hass.config_entries.async_get_entry(entry),
                                       device_id=device, suggested_object_id=f"{node}_assist_satellite")
    hass.states.async_set(sat.entity_id, "idle")
    if area:
        dr.async_get(hass).async_update_device(device, area_id=room(hass, area))
    return device, sat.entity_id


def room(hass: HomeAssistant, name: str) -> str:
    areas = ar.async_get(hass)
    return (areas.async_get_area_by_name(name) or areas.async_create(name)).id


def player(hass: HomeAssistant, object_id: str, name: str, area: str | None = None,
           maker: str = "Arylic", mac: str | None = None) -> str:
    """A Music Assistant player; maker TECHO5 makes it a Show's own. The MAC is what Music Assistant
    has for its device, which Home Assistant only has through Music Assistant's client."""
    entry = next(iter(hass.config_entries.async_entries("music_assistant")), None)
    if entry is None:
        entry = MockConfigEntry(domain="music_assistant", title="Music Assistant")
        entry.add_to_hass(hass)
        entry.runtime_data = SimpleNamespace(mass=SimpleNamespace(players=SimpleNamespace(players=[])))
    entry.runtime_data.mass.players.players.append(SimpleNamespace(
        player_id=f"up{object_id}", device_info=SimpleNamespace(mac_address=mac)))
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("music_assistant", f"up{object_id}")},
        manufacturer=maker, name=name)
    if area:
        dr.async_get(hass).async_update_device(device.id, area_id=room(hass, area))
    e = er.async_get(hass).async_get_or_create("media_player", "music_assistant", f"up{object_id}",
                                               config_entry=entry, device_id=device.id,
                                               suggested_object_id=object_id)
    hass.states.async_set(e.entity_id, "idle", {"friendly_name": name})
    # What integration_entities() goes by, filled when a loaded platform adds the entity.
    entity_sources(hass)[e.entity_id] = {"domain": "music_assistant", "config_entry": entry.entry_id}
    return e.entity_id


def music(hass: HomeAssistant) -> dict:
    return hass.states.get("sensor.genbu_house_version").attributes["music_players"]


async def look(hass: HomeAssistant) -> None:
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_refresh()
    await hass.async_block_till_done()


async def flip(hass: HomeAssistant, entity: str, state: str, name: str) -> None:
    """A player goes offline or comes back, after the 2 s the looks are held back by."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    hass.states.async_set(entity, state, {"friendly_name": name})
    await hass.async_block_till_done()


async def test_a_show_plays_in_its_own_room(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    own = player(hass, "bedroomecho8", "bedroomEcho8", maker="TECHO5")
    device, sat = show(hass, "bedroomecho8", "Bedroom")
    await setup(hass, main="bedroomecho8")

    # Nothing else in the bedroom: the Show itself, never the living room.
    assert music(hass) == {device: own, sat: own}
    select = hass.states.get("select.bedroomecho8_music_plays_on")
    assert select.state == "Automatic: no speaker in Bedroom (bedroomEcho8)"
    assert select.attributes["player"] == own
    assert set(select.attributes["options"]) == {"Automatic: no speaker in Bedroom (bedroomEcho8)", "Wohnzimmer", "bedroomEcho8"}

    # A speaker turns up in the bedroom: that is the room's speaker now.
    bed = player(hass, "bedroom_speaker", "Schlafzimmer", "Bedroom")
    await look(hass)
    assert music(hass)[sat] == bed
    assert living not in music(hass).values()


async def test_a_show_moved_to_another_room_plays_there(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    player(hass, "kitchen", "Küche", "Kitchen")
    device, sat = show(hass, "hall", "Kitchen")
    await setup(hass, main="hall")
    assert music(hass)[sat] == "media_player.kitchen"
    dr.async_get(hass).async_update_device(device, area_id=room(hass, "Living Room"))
    await look(hass)
    assert music(hass)[sat] == living
    assert hass.states.get("select.hall_music_plays_on").state == "Automatic: speaker in Living Room (Wohnzimmer)"


async def test_picked_by_hand_and_back_to_automatic(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    kitchen = player(hass, "kitchen", "Küche", "Kitchen")
    device, sat = show(hass, "hall", "Kitchen")
    await setup(hass, main="hall")
    await hass.services.async_call("select", "select_option", {
        "entity_id": "select.hall_music_plays_on", "option": "Wohnzimmer"}, blocking=True)
    await hass.async_block_till_done()
    assert music(hass)[sat] == living
    assert hass.states.get("select.hall_music_plays_on").state == "Wohnzimmer"
    await hass.services.async_call("select", "select_option", {
        "entity_id": "select.hall_music_plays_on", "option": "Automatic: speaker in Kitchen (Küche)"}, blocking=True)
    await hass.async_block_till_done()
    assert music(hass)[sat] == kitchen


async def test_an_offline_speaker_falls_back_to_the_show_at_once(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    own = player(hass, "genbushow5", "Jarvis Show 5", maker="OpenJade")
    _, sat = show(hass, "genbushow5", "Living Room")
    await setup(hass, main="genbushow5")
    select = "select.genbushow5_music_plays_on"
    fallback = "sensor.genbushow5_music_falls_back_to"
    assert music(hass)[sat] == living
    assert hass.states.get(select).state == "Automatic: speaker in Living Room (Wohnzimmer)"
    assert hass.states.get(fallback).state == "Jarvis Show 5"
    assert hass.states.get(fallback).attributes["player"] == own

    # No look in between: the speaker going offline is enough.
    await flip(hass, living, "unavailable", "Wohnzimmer")
    assert music(hass)[sat] == own
    assert hass.states.get(select).state == "Automatic: Wohnzimmer is offline (Jarvis Show 5)"

    await flip(hass, living, "idle", "Wohnzimmer")
    assert music(hass)[sat] == living

    # A pick that goes offline falls back the same way.
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Wohnzimmer"},
                                   blocking=True)
    assert hass.states.get(fallback).state == "Jarvis Show 5"  # the room's speaker is not counted twice
    await flip(hass, living, "unavailable", "Wohnzimmer")
    assert music(hass)[sat] == own
    assert hass.states.get(select).state == "Wohnzimmer (offline)"
    assert "Wohnzimmer" not in hass.states.get(select).attributes["options"]
    await flip(hass, living, "idle", "Wohnzimmer")
    assert hass.states.get(select).state == "Wohnzimmer"


async def test_the_select_follows_the_show(hass: HomeAssistant):
    player(hass, "kitchen", "Küche", "Kitchen")
    device, _ = show(hass, "hall", "Kitchen")
    await setup(hass, main="hall")
    devices = dr.async_get(hass)
    mine = devices.async_get_device_by_identifier((DOMAIN, device), hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    assert mine.via_device_id == device
    assert mine.name == "Hall"
    on_it = {e.entity_id for e in er.async_entries_for_device(er.async_get(hass), mine.id)}
    assert on_it == {"select.hall_music_plays_on", "sensor.hall_music_falls_back_to"}

    devices.async_update_device(device, name_by_user="Flur")
    await look(hass)
    assert devices.async_get(mine.id).name == "Flur"

    await hass.services.async_call("select", "select_option", {
        "entity_id": "select.hall_music_plays_on", "option": "Küche"}, blocking=True)
    devices.async_remove_device(device)
    await look(hass)
    assert devices.async_get(mine.id) is None
    assert hass.states.get("select.hall_music_plays_on") is None
    assert hass.states.get("sensor.hall_music_falls_back_to") is None
    hub = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert hub.house.music_choice(device) == ""


async def test_a_renamed_show_still_falls_back_to_itself(hass: HomeAssistant):
    # A rename reaches the Show: echod takes the new name as its node name and gives it Sendspin, so
    # neither its node nor its player's name is what it was. Its MAC stays.
    player(hass, "livingroom", "Wohnzimmer", "Living Room")
    own = player(hass, "a1b2c3d4", "Jarvis Show 5", maker="TECHO5", mac="AA:BB:CC:2C:1E:D7")
    device, _ = show(hass, "genbushow5", "Living Room")
    devices = dr.async_get(hass)
    devices.async_update_device(device, new_connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:2c:1e:d7")})
    await setup(hass, main="genbushow5")
    fallback = "sensor.genbushow5_music_falls_back_to"
    assert hass.states.get(fallback).attributes["player"] == own

    devices.async_update_device(device, name_by_user="Test Show")
    esphome = hass.config_entries.async_get_entry(devices.async_get(device).config_entry_id)
    hass.config_entries.async_update_entry(esphome, data={**esphome.data, "device_name": "test-show"})
    ma_device = er.async_get(hass).async_get(own).device_id
    devices.async_update_device(ma_device, name="Test Show")
    await look(hass)
    assert hass.states.get(fallback).state == "Jarvis Show 5"
    assert hass.states.get(fallback).attributes["player"] == own
    assert hass.states.get("select.genbushow5_music_plays_on").state == "Automatic: speaker in Living Room (Wohnzimmer)"
    devices.async_update_device(device, area_id=None)
    await look(hass)
    assert hass.states.get("select.genbushow5_music_plays_on").state == "Automatic: no room set (Jarvis Show 5)"


def render(hass: HomeAssistant, text: str, **variables) -> str:
    return Template(text, hass).async_render(variables, parse_result=False).strip()


async def test_the_scripts_play_where_the_show_was_asked(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    own = player(hass, "bedroomecho8", "bedroomEcho8", maker="TECHO5")
    device, sat = show(hass, "bedroomecho8", "Bedroom")
    _, other = show(hass, "hall", "Living Room")
    await setup(hass, main="hall")

    # The automations: whichever Show heard the sentence.
    assert render(hass, voice_kit.ROOM_PLAYER, trigger={"device_id": device}) == own
    # The LLM's scripts: whichever Show is listening right now.
    hass.states.async_set(sat, "processing")
    assert render(hass, voice_kit.TARGET_PLAYER, players=[living], player="") == own
    hass.states.async_set(sat, "idle")
    hass.states.async_set(other, "responding")
    assert render(hass, voice_kit.TARGET_PLAYER, players=[living], player="") == living
    # Typed in the app, nobody listening: the house's first player.
    hass.states.async_set(other, "idle")
    assert render(hass, voice_kit.TARGET_PLAYER, players=[living], player="") == living


async def test_a_show_with_no_speaker_anywhere_gets_none(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    _, sat = show(hass, "bedroomecho8", "Bedroom")
    await setup(hass, main="bedroomecho8")
    hass.states.async_set(sat, "listening")
    # Not the living room: the script says so instead.
    assert render(hass, voice_kit.TARGET_PLAYER, players=[living], player="") == ""


async def test_the_turn_down_follows_the_room(hass: HomeAssistant):
    import yaml

    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))
    blueprint = Path(__file__).resolve().parents[2] / "blueprints/automation/turn-room-down.yaml"
    speakers = yaml.load(blueprint.read_text(), Loader=Loader)["variables"]["speakers"]

    player(hass, "livingroom", "Wohnzimmer", "Living Room")
    own = player(hass, "hall", "hall", "Living Room", maker="TECHO5")
    arylic = MockConfigEntry(domain="linkplay")
    arylic.add_to_hass(hass)
    d = dr.async_get(hass).async_get_or_create(config_entry_id=arylic.entry_id, identifiers={("linkplay", "x")},
                                               manufacturer="Arylic", name="Wohnzimmer")
    dr.async_get(hass).async_update_device(d.id, area_id=room(hass, "Living Room"))
    native = er.async_get(hass).async_get_or_create("media_player", "linkplay", "x", config_entry=arylic,
                                                    device_id=d.id, suggested_object_id="wohnzimmer")
    hass.states.async_set(native.entity_id, "playing")
    _, sat = show(hass, "hall", "Living Room")
    await setup(hass, main="hall")

    got = Template(speakers, hass).async_render({"satellite": sat, "picked": []})
    assert got == [native.entity_id]  # the speaker's own entity, not the Show, not Music Assistant's copy
    assert own not in got
    assert Template(speakers, hass).async_render({"satellite": sat, "picked": ["media_player.x"]}) == ["media_player.x"]
    _, nowhere = show(hass, "attic", None)
    assert Template(speakers, hass).async_render({"satellite": nowhere, "picked": []}) == []


async def test_spoken_titles_leave_out_the_brackets(hass: HomeAssistant):
    track = {"name": "Viva Colonia (Da simmer dabei, dat is prima!) [Live]", "uri": "library://track/1",
             "artists": [{"name": "Höhner"}]}
    pick = Template(voice_kit.MOOD_PICK, hass).async_render({
        "plan": {"kind": "any", "terms": ["viva"]}, "s1": {"tracks": [track]}, "s2": {}, "s3": {}, "rnd": {}})
    assert pick["title"] == "Viva Colonia"
    assert pick["uri"] == "library://track/1"  # the track itself stays the one that was found
    said = render(hass, voice_kit.MOOD_MESSAGE, pick=pick, plan={"kind": "any", "terms": ["viva"]})
    assert said == "Spiele Viva Colonia von Höhner und ähnliche Lieder."

    # Nothing but brackets: better the whole name than nothing.
    pick = Template(voice_kit.MOOD_PICK, hass).async_render({
        "plan": {"kind": "playlist", "terms": ["intro"]}, "s1": {"playlists": [{"name": "(Intro)", "uri": "p"}]},
        "s2": {}, "s3": {}, "rnd": {}})
    assert pick["title"] == "(Intro)"

    # The random track the plain "play music" script starts.
    steps = voice_kit.play_random(voice_kit.House(music_entry="ma"))["sequence"]
    title = next(s["variables"]["title"] for s in steps if "title" in s.get("variables", {}))
    assert render(hass, title, random_track={"items": [track]}) == "Viva Colonia"


async def test_what_plays_in_the_room_is_what_stop_pauses(hass: HomeAssistant):
    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    kitchen = player(hass, "kitchen", "Küche", "Kitchen")
    own = player(hass, "hall", "hall", "Living Room", maker="TECHO5")
    linkplay = MockConfigEntry(domain="linkplay")
    linkplay.add_to_hass(hass)
    d = dr.async_get(hass).async_get_or_create(config_entry_id=linkplay.entry_id, identifiers={("linkplay", "x")},
                                               manufacturer="Arylic", name="Wohnzimmer")
    dr.async_get(hass).async_update_device(d.id, area_id=room(hass, "Living Room"))
    native = er.async_get(hass).async_get_or_create("media_player", "linkplay", "x", config_entry=linkplay,
                                                    device_id=d.id, suggested_object_id="wohnzimmer_native").entity_id
    device, _ = show(hass, "hall", "Living Room")
    await setup(hass, main="hall")

    def playing(**trigger) -> list:
        return Template(voice_kit.ROOM_PLAYING, hass).async_render(
            {"trigger": trigger, "target_player": render(hass, voice_kit.ROOM_PLAYER, trigger=trigger)})

    assert playing(device_id=device) == []
    hass.states.async_set(kitchen, "playing")
    assert playing(device_id=device) == []  # never another room's
    # Music Assistant's player and the speaker's own entity both play: only Music Assistant's is paused.
    hass.states.async_set(living, "playing", {"media_title": "Bohemian Rhapsody (Remastered 2011)",
                                              "media_artist": "Queen"})
    hass.states.async_set(native, "playing")
    assert playing(device_id=device) == [living]
    # The speaker played from elsewhere, not through Music Assistant.
    hass.states.async_set(living, "idle")
    assert playing(device_id=device) == [native]
    # The Show's own player, when the room's music goes to it.
    hass.states.async_set(native, "idle")
    hass.states.async_set(own, "playing")
    assert playing(device_id=device) == [own]

    hass.states.async_set(own, "idle")
    hass.states.async_set(living, "playing", {"media_title": "Bohemian Rhapsody (Remastered 2011)",
                                              "media_artist": "Queen"})
    assert render(hass, voice_kit.NOW_PLAYING, playing=[living]) == "Gerade läuft Bohemian Rhapsody von Queen."
    hass.states.async_set(living, "playing", {})
    assert render(hass, voice_kit.NOW_PLAYING, playing=[living]) == "Hier läuft schon Musik."


async def test_stop_stop_pauses_the_music_in_the_room(hass: HomeAssistant):
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_mock_service

    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    kitchen = player(hass, "kitchen", "Küche", "Kitchen")
    device, _ = show(hass, "hall", "Living Room")
    await setup(hass, main="hall")
    paused = async_mock_service(hass, "media_player", "media_pause")
    house = voice_kit.House(german=True, music_entry="ma", players=[living, kitchen])
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "jarvis_local_music_stop", **voice_kit.local_music_stop(house)}]})
    await hass.async_block_till_done()

    async def say(text: str) -> str:
        r = await conversation.async_converse(hass, text, None, Context(), language="de", device_id=device)
        return r.response.speech["plain"]["speech"]

    hass.states.async_set(living, "playing")
    hass.states.async_set(kitchen, "playing")
    assert await say("Stopp, stopp!") == "Pausiert."
    assert [c.data["entity_id"] for c in paused] == [[living]]
    hass.states.async_set(living, "paused")
    assert await say("Hör auf") == "Hier läuft gerade nichts."
    assert len(paused) == 1


async def test_die_musik_starts_some_or_says_what_plays(hass: HomeAssistant):
    from homeassistant.components import conversation
    from homeassistant.core import Context, SupportsResponse
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_mock_service

    living = player(hass, "livingroom", "Wohnzimmer", "Living Room")
    device, _ = show(hass, "hall", "Living Room")
    await setup(hass, main="hall")
    started = async_mock_service(hass, "script", "play_random_music", supports_response=SupportsResponse.OPTIONAL,
                                 response={"message": "Spiele Yesterday von The Beatles und ähnliche Lieder."})
    house = voice_kit.House(german=True, music_entry="ma", players=[living])
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "jarvis_local_random_music", **voice_kit.local_music(house)}]})
    await hass.async_block_till_done()

    async def say(text: str) -> str:
        r = await conversation.async_converse(hass, text, None, Context(), language="de", device_id=device)
        return r.response.speech["plain"]["speech"]

    assert await say("Die Musik.") == "Spiele Yesterday von The Beatles und ähnliche Lieder."
    assert [c.data["player"] for c in started] == [living]
    hass.states.async_set(living, "playing", {"media_title": "Yesterday", "media_artist": "The Beatles"})
    assert await say("Musik bitte") == "Gerade läuft Yesterday von The Beatles."
    assert len(started) == 1
    # The other sentences still start music whatever plays.
    assert await say("Spiel die Musik") == "Spiele Yesterday von The Beatles und ähnliche Lieder."
    assert len(started) == 2


async def test_the_wecker_asks_back_and_keeps_listening(hass: HomeAssistant):
    """When the Wecker can't read the time it asks for it, and the asking goes last (GENBU-117). Home
    Assistant 2026.10 never asks its own agent's answers to continue, whatever they end with, so the
    Show decides by the reply's last sentences; and a chat model's reply only continues when it ends
    with a question."""
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from homeassistant.setup import async_setup_component

    device, _ = show(hass, "hall", "Living Room")
    entities = er.async_get(hass)
    entities.async_get_or_create("button", "esphome", "aa:hall-stop_alarm", device_id=device,
                                 suggested_object_id="hall_stop_alarm")
    await setup(hass, main="hall")
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "jarvis_wecker", **voice_kit.wecker()}]})
    await hass.async_block_till_done()

    for text, want in [("Stell einen Wecker für 0 Minuten", "Das habe ich nicht verstanden. Wie lange?"),
                       ("Stell einen Wecker auf 25 Uhr", "Die Uhrzeit habe ich nicht verstanden. Für wann?")]:
        r = await conversation.async_converse(hass, text, None, Context(), language="de", device_id=device)
        assert r.response.speech["plain"]["speech"] == want
        assert want.endswith("?")
