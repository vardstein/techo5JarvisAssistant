"""Where a Show's music plays, inside a real Home Assistant: the "Music plays on" select, the map on the
house version sensor, a Show moving rooms, and the templates the installer's scripts and automations
use reading that map."""

import sys
from datetime import timedelta
from pathlib import Path

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
           maker: str = "Arylic") -> str:
    """A Music Assistant player; maker TECHO5 makes it a Show's own."""
    entry = next(iter(hass.config_entries.async_entries("music_assistant")), None)
    if entry is None:
        entry = MockConfigEntry(domain="music_assistant", title="Music Assistant")
        entry.add_to_hass(hass)
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
    assert select.state == "Automatic (bedroomEcho8)"
    assert select.attributes["player"] == own
    assert set(select.attributes["options"]) == {"Automatic (bedroomEcho8)", "Wohnzimmer", "bedroomEcho8"}

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
        "entity_id": "select.hall_music_plays_on", "option": "Automatic (Küche)"}, blocking=True)
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
    assert hass.states.get(select).state == "Automatic (Wohnzimmer, else Jarvis Show 5)"
    assert hass.states.get(fallback).state == "Jarvis Show 5"
    assert hass.states.get(fallback).attributes["player"] == own

    # No look in between: the speaker going offline is enough.
    await flip(hass, living, "unavailable", "Wohnzimmer")
    assert music(hass)[sat] == own
    assert hass.states.get(select).state == "Automatic (Jarvis Show 5 while Wohnzimmer is offline)"

    await flip(hass, living, "idle", "Wohnzimmer")
    assert music(hass)[sat] == living

    # A pick that goes offline falls back the same way.
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Wohnzimmer"},
                                   blocking=True)
    assert hass.states.get(fallback).state == "Jarvis Show 5"  # the room's speaker is not counted twice
    await flip(hass, living, "unavailable", "Wohnzimmer")
    assert music(hass)[sat] == own
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
