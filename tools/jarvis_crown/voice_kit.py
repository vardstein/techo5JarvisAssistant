"""The Home Assistant pieces around the Shows that are not the Show itself, for others to have too.

What the Shows use in Home Assistant beyond their own device, set up the same way in any house:

- two blueprints from blueprints/automation: turn-room-down (the room's music goes almost quiet while a
  Show listens and answers) and volume-where-the-music-is ("Lautstärke auf 30" sets the music in the room);
- per Show, an automation from turn-room-down with the Show, its room's speakers and its near-miss event;
- answers that never need the language model: where we are, how warm it is in a room or outside,
  tomorrow's weather, the volume, and the Wecker set on the Show that was asked;
- with Music Assistant: random music, music by mood or artist, and radio by name, started without the
  model and answered with what plays; and stopping it in the words Home Assistant's own sentences miss
  ("Stopp, stopp", "Hör auf", "Mach die Musik aus").

The sentences come in German and in English (GENBU-120), each set made when an Assist pipeline speaks
that language; a house with neither gets only the per-Show automation. The English ones are their own
automations (ids ending in _en) and a second volume blueprint, and they use the same music scripts as
the German ones, saying what those found in English. What the house has is looked up: rooms and how each
language says "in" them, each room's temperature sensor, the weather entity, Music Assistant's entry
and players. Nothing is made for what the house lacks.

Every item is looked at first: missing is made, the same is left, and one that differs (edited by hand,
or from an older installer) is only replaced when the person says so, after its old configuration was
saved through keep. The name an item shows under doesn't count: a house keeps the names it gave them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
import time
from typing import Any, Callable

from . import labels
from .home_assistant import (Chooser, HomeAssistant, HomeAssistantError, NotFound, node_slug, service_prefix,
                             slug_key, ASSISTANT_NAME)

BLUEPRINT_FOLDER = "techo5"
BLUEPRINT_SOURCE = "https://github.com/vardstein/techo5JarvisAssistant/blob/stable/blueprints/automation/"
# Ours from any branch: one saved while the branch had another name (jarvis-crown-v1) is still ours to update.
_OUR_BLUEPRINT = re.compile(r"^https://github[.]com/vardstein/techo5JarvisAssistant/blob/[^/]+(?:/[^/]+)*?"
                            r"/blueprints/automation/(?P<name>[^/]+)$")
BLUEPRINT_DIR = Path(__file__).resolve().parents[2] / "blueprints" / "automation"
TURN_ROOM_DOWN = "turn-room-down.yaml"
VOLUME = "volume-where-the-music-is.yaml"
VOLUME_EN = "volume-where-the-music-is-en.yaml"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
OURS = "techo5_turn_room_down_"   # our per-Show automation ids; the rest of the id is the Show's prefix
CLIMATE = "jarvis_room_climate"   # the model's room-climate tool, the one script it needs exposed
# The music scripts meant for the model, exposed on every run (decided, docs/genbu-assistant.md §4). Not
# jarvis_music_start, which only they call.
MUSIC_TOOLS = ("jarvis_play_radio", "jarvis_play_mood", "play_random_music")
_ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

# Rooms the German sentences know: what an area may be called (its id or name, either language, as
# slug_key has it), how German says "in it", and what someone says for it.
ROOMS = (
    (("livingroom", "wohnzimmer", "wohnbereich", "lounge"), "im Wohnzimmer", ("wohnzimmer", "wohnbereich")),
    (("kitchen", "kuche", "kueche"), "in der Küche", ("küche", "kueche")),
    (("bedroom", "schlafzimmer"), "im Schlafzimmer", ("schlafzimmer",)),
    (("bath", "bathroom", "bad", "badezimmer"), "im Bad", ("bad", "badezimmer")),
    (("office", "study", "buro", "buero", "arbeitszimmer"), "im Büro", ("büro", "buero", "arbeitszimmer")),
    (("floor", "hall", "hallway", "corridor", "flur", "diele"), "im Flur", ("flur", "diele")),
    (("balcony", "balkon"), "auf dem Balkon", ("balkon",)),
    (("kidsroom", "childrensroom", "nursery", "kinderzimmer"), "im Kinderzimmer", ("kinderzimmer",)),
    (("diningroom", "esszimmer"), "im Esszimmer", ("esszimmer",)),
    (("guestroom", "gastezimmer", "gaestezimmer"), "im Gästezimmer", ("gästezimmer", "gaestezimmer")),
    (("garden", "garten"), "im Garten", ("garten",)),
    (("garage",), "in der Garage", ("garage",)),
    (("basement", "cellar", "keller"), "im Keller", ("keller",)),
    (("terrace", "patio", "terrasse"), "auf der Terrasse", ("terrasse",)),
)

# The same rooms for the English sentences: how English says "in" them, and what someone says for them.
EN_ROOMS = (
    (("livingroom", "wohnzimmer", "wohnbereich", "lounge"), "in the living room", ("living room", "lounge")),
    (("kitchen", "kuche", "kueche"), "in the kitchen", ("kitchen",)),
    (("bedroom", "schlafzimmer"), "in the bedroom", ("bedroom",)),
    (("bath", "bathroom", "bad", "badezimmer"), "in the bathroom", ("bathroom", "bath")),
    (("office", "study", "buro", "buero", "arbeitszimmer"), "in the office", ("office", "study")),
    (("floor", "hall", "hallway", "corridor", "flur", "diele"), "in the hallway", ("hallway", "hall", "corridor")),
    (("balcony", "balkon"), "on the balcony", ("balcony",)),
    (("kidsroom", "childrensroom", "nursery", "kinderzimmer"), "in the kids' room",
     ("kids room", "kids' room", "kid's room", "children's room", "nursery")),
    (("diningroom", "esszimmer"), "in the dining room", ("dining room",)),
    (("guestroom", "gastezimmer", "gaestezimmer"), "in the guest room", ("guest room",)),
    (("garden", "garten"), "in the garden", ("garden",)),
    (("garage",), "in the garage", ("garage",)),
    (("basement", "cellar", "keller"), "in the basement", ("basement", "cellar")),
    (("terrace", "patio", "terrasse"), "on the terrace", ("terrace", "patio")),
)


@dataclass
class House:
    """What the pieces need to know about the house."""

    german: bool = False
    english: bool = False
    areas: dict[str, str] = field(default_factory=dict)          # id -> name
    temperatures: dict[str, str] = field(default_factory=dict)   # area id -> its temperature sensor
    humidities: dict[str, str] = field(default_factory=dict)     # area id -> its humidity sensor
    weather: str | None = None
    agent: str | None = None                                     # the assistant pipeline's model agent
    music_entry: str | None = None                               # Music Assistant's config entry
    players: list[str] = field(default_factory=list)             # its players that are in a room
    genres: dict[str, str] = field(default_factory=dict)         # Music Assistant's genres, URI -> name
    # From the registries, for the per-Show automation.
    entity_area: dict[str, str] = field(default_factory=dict)
    entity_device: dict[str, str] = field(default_factory=dict)
    entity_platform: dict[str, str] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)


def _registries(ha: HomeAssistant) -> tuple[list[dict], dict[str, str | None]]:
    """The usable entities (not hidden, disabled or diagnostic) and each device's area."""
    devices = {d.get("id"): d.get("area_id") for d in ha.ws({"type": "config/device_registry/list"}) or []}
    return list(ha.ws({"type": "config/entity_registry/list"}) or []), devices


def look(ha: HomeAssistant) -> House:
    """What this Home Assistant has, for the pieces to be made from."""
    house = House()
    try:
        listed = ha.ws({"type": "assist_pipeline/pipeline/list"}) or {}
    except HomeAssistantError:
        listed = {}
    languages = [str(p.get("language") or "").lower() for p in listed.get("pipelines") or [] if isinstance(p, dict)]
    house.german = any(lang.startswith("de") for lang in languages)
    house.english = any(lang.startswith("en") for lang in languages)
    house.agent = assistant_agent(listed)
    house.areas = {a.get("area_id"): a.get("name") or a.get("area_id")
                   for a in ha.ws({"type": "config/area_registry/list"}) or [] if a.get("area_id")}
    entities, devices = _registries(ha)
    states = {s.get("entity_id"): s for s in ha.request("GET", "/api/states") or []}
    house.states = {e: str(s.get("state")) for e, s in states.items()}
    climate_devices = {e.get("device_id") for e in entities
                       if str(e.get("entity_id", "")).startswith("climate.") and e.get("device_id")}
    temps: dict[str, list[str]] = {}
    hums: dict[str, list[str]] = {}
    ma_players: list[tuple[str, str]] = []
    for e in entities:
        eid = str(e.get("entity_id") or "")
        if not _ENTITY_RE.fullmatch(eid) or e.get("hidden_by") or e.get("disabled_by"):
            continue
        area = e.get("area_id") or devices.get(e.get("device_id")) or ""
        if area:
            house.entity_area[eid] = area
        if e.get("device_id"):
            house.entity_device[eid] = e["device_id"]
        house.entity_platform[eid] = str(e.get("platform") or "")
        attrs = (states.get(eid) or {}).get("attributes") or {}
        if e.get("entity_category"):
            continue
        if eid.startswith("sensor.") and area and attrs.get("device_class") == "temperature" \
                and attrs.get("unit_of_measurement") in ("°C", "°F") \
                and e.get("device_id") not in climate_devices:
            temps.setdefault(area, []).append(eid)
        # A thermostat's own sensors are left out: in a room that has one of its own, the thermostat sits
        # at the radiator and reads it, not the room (GENBU-139). Its readings are the fallback.
        if eid.startswith("sensor.") and area and attrs.get("device_class") == "humidity" \
                and attrs.get("unit_of_measurement") == "%" and e.get("device_id") not in climate_devices:
            hums.setdefault(area, []).append(eid)
        if eid.startswith("media_player.") and e.get("platform") == "music_assistant" and area:
            ma_players.append((house.areas.get(area, area).casefold(), eid))
    house.temperatures = {a: sorted(found)[0] for a, found in temps.items()}
    house.humidities = {a: sorted(found)[0] for a, found in hums.items()}
    weathers = sorted(e for e in states if str(e).startswith("weather."))
    house.weather = "weather.forecast_home" if "weather.forecast_home" in weathers else \
        (weathers[0] if weathers else None)
    for entry in ha.ws({"type": "config_entries/get"}) or []:
        if entry.get("domain") == "music_assistant" and entry.get("state") == "loaded" \
                and _ID_RE.fullmatch(str(entry.get("entry_id") or "")):
            house.music_entry = entry["entry_id"]
            break
    house.players = [e for _, e in sorted(ma_players)]
    return house


def assistant_agent(listed: dict) -> str | None:
    """The conversation agent of the Genbu Assistant pipeline, or of the preferred one, when it is not
    Home Assistant's own. That one answers by its sentences already, so there is nothing to hand on."""
    pipelines = [p for p in listed.get("pipelines") or [] if isinstance(p, dict)]
    pipeline = next((p for p in pipelines if p.get("name") == ASSISTANT_NAME), None) or \
        next((p for p in pipelines if p.get("id") and p.get("id") == listed.get("preferred_pipeline")), None)
    agent = str((pipeline or {}).get("conversation_engine") or "")
    if not _ENTITY_RE.fullmatch(agent) and not _ID_RE.fullmatch(agent):
        return None
    return None if agent in ("conversation.home_assistant", "homeassistant") else agent


def room_words(areas: dict[str, str], rooms: tuple = ROOMS) -> tuple[dict[str, str], dict[str, str]]:
    """For the house's areas: how German (or English, with EN_ROOMS) says "in" each one it knows, and
    the words for them."""
    phrases: dict[str, str] = {}
    words: dict[str, str] = {}
    for area, name in sorted(areas.items()):
        keys = {slug_key(area), slug_key(name), slug_key(name, True)}
        for names, phrase, said in rooms:
            if keys & set(names):
                phrases[area] = phrase
                for w in said:
                    words.setdefault(w, area)
                break
    return phrases, words


def _fill(template: str, **values: Any) -> str:
    """The template with each __NAME__ replaced by its value as a Jinja literal."""
    for name, value in values.items():
        template = template.replace(f"__{name.upper()}__", json.dumps(value, ensure_ascii=False, sort_keys=True))
    return template


# ------------------------------------------------------------------------------------------- answers

WHERE_ARE_WE = r"""{% set de = __PHRASES__ %}{% set a = area_id(trigger.device_id) if trigger.device_id else none %}{% if a in de %}Wir sind {{ de[a] }}.{% elif a %}Wir sind in {{ area_name(a) }}.{% else %}Das weiß ich nicht, ich bin keinem Raum zugeordnet.{% endif %}"""


def where_are_we(house: House) -> dict:
    phrases, _ = room_words(house.areas)
    return {
        "alias": "Genbu - Where are we",
        "description": 'Answers "Wo sind wir?" locally with the room of the device that was asked.',
        "triggers": [{"trigger": "conversation", "command": [
            "wo (sind wir|bin ich) [gerade|hier|jetzt]",
            "in welchem raum (sind wir|bin ich|bist du) [gerade|hier|jetzt]",
            "wo (bist|stehst) du [gerade|hier|jetzt]",
            "welcher raum ist das"]}],
        "actions": [{"set_conversation_response": _fill(WHERE_ARE_WE, phrases=phrases)}],
        "mode": "parallel",
    }


def greeting(house: House) -> dict:
    # Home Assistant's own sentences answer "Hallo" and the like before the model is asked, even with the
    # model as the pipeline's agent ("Hallo vom Home Assistant."). A sentence trigger comes before them,
    # so this one hands the greeting on to the assistant's model, which answers as itself.
    return {
        "alias": "Genbu - Greeting",
        "description": "Hands greetings like \"Hallo\" to the assistant's model. Home Assistant would "
                       "answer them itself otherwise.",
        "triggers": [{"trigger": "conversation", "command": [
            "(hallo|hi|hey|guten tag|guten morgen|guten abend|servus|moin) [genbu|home assistant]"]}],
        "actions": [
            {"action": "conversation.process", "data": {
                "agent_id": house.agent, "text": "{{ trigger.sentence }}",
                "language": "{{ trigger.user_input.language | default('de', true) }}"},
             "response_variable": "said", "continue_on_error": True},
            {"set_conversation_response":
                "{{ said.response.speech.plain.speech | default('Hallo.', true) if said is defined else 'Hallo.' }}"}],
        "mode": "parallel",
    }


# The area a sentence means: the one named in {raum}, by its German word or its own name, or else
# the room of the device that heard it. Leaves it in a, none when there is no such room.
ROOM_PICK = r"""{% set raum = (trigger.slots.raum | default('')) | lower | trim %}{% set ns = namespace(a=rooms.get(raum) if raum else (area_id(trigger.device_id) if trigger.device_id else none)) %}{% for x in areas() if raum and ns.a is none and area_name(x) | lower == raum %}{% set ns.a = x %}{% endfor %}{% set a = ns.a %}"""

ROOM_TEMPERATURE = (r"""{% set rooms = __WORDS__ %}{% set sensors = __SENSORS__ %}{% set de = __PHRASES__ %}{% if 'drau' in (trigger.sentence | lower) %}{% set t = state_attr(__WEATHER__, 'temperature') | float(none) %}{% if t is none %}Das Wetter weiß ich gerade nicht.{% else %}{% set v = '%.1f' | format(t) %}Draußen sind es {{ (v[:-2] if v.endswith('.0') else v) | replace('.', ',') }} Grad.{% endif %}{% else %}""" + ROOM_PICK
                    + r"""{% set t = states(sensors[a]) | float(none) if a in sensors else none %}{% if t is none and a %}{% set t = area_entities(a) | select('match', 'climate\\.') | map('state_attr', 'current_temperature') | reject('none') | map('float') | list | first | default(none) %}{% endif %}{% if t is not none %}{% set v = '%.1f' | format(t) %}{% set v = v[:-2] if v.endswith('.0') else v %}{% set w = de.get(a, 'in ' ~ area_name(a)) %}{{ w[0] | upper }}{{ w[1:] }} sind es {{ v | replace('.', ',') }} Grad.{% elif a %}Für {{ area_name(a) }} hab ich gerade keinen Wert.{% else %}Den Raum kenne ich nicht.{% endif %}{% endif %}""")


def room_temperature(house: House) -> dict:
    phrases, words = room_words(house.areas)
    command = [
        "wie (warm|kalt) ist es [gerade|jetzt] (im|in der|in dem) {raum}",
        "wie ist die temperatur [gerade|jetzt] (im|in der|in dem) {raum}",
        "wie viel grad (sind|hat) es [gerade|jetzt] (im|in der|in dem) {raum}",
        "wie (warm|kalt) ist es [gerade|jetzt] hier [drin|drinnen]",
        "wie ist die temperatur [gerade|jetzt] hier [drin|drinnen]",
        "wie viel grad (sind|hat) es [gerade|jetzt] hier [drin|drinnen]",
        "wie (warm|kalt) ist es [gerade|jetzt]",
        "wie ist die temperatur [gerade|jetzt]",
    ]
    if house.weather:
        command += ["wie (warm|kalt) ist es [gerade|jetzt] (draußen|draussen)",
                    "wie viel grad (sind|hat) es [gerade|jetzt] (draußen|draussen)"]
    return {
        "alias": "Genbu - Room temperature",
        "description": "Answers room temperature questions locally from the room sensor and names the room, "
                       "so it can't be mistaken for the weather.",
        "triggers": [{"trigger": "conversation", "command": command}],
        "actions": [{"set_conversation_response": _fill(
            ROOM_TEMPERATURE, words=words, sensors=house.temperatures, phrases=phrases,
            weather=house.weather or "")}],
        "mode": "parallel",
    }


ROOM_HUMIDITY = (r"""{% set rooms = __WORDS__ %}{% set sensors = __SENSORS__ %}{% set de = __PHRASES__ %}{% if 'drau' in (trigger.sentence | lower) %}{% set h = state_attr(__WEATHER__, 'humidity') | float(none) %}{% if h is none %}Das Wetter weiß ich gerade nicht.{% else %}Draußen sind es {{ h | round(0) | int }} Prozent Luftfeuchtigkeit.{% endif %}{% else %}""" + ROOM_PICK
                 + r"""{% set h = states(sensors[a]) | float(none) if a in sensors else none %}{% if h is none and a %}{% set h = area_entities(a) | select('match', 'climate\\.') | map('state_attr', 'current_humidity') | reject('none') | map('float') | list | first | default(none) %}{% endif %}{% if h is not none %}{% set w = de.get(a, 'in ' ~ area_name(a)) %}{{ w[0] | upper }}{{ w[1:] }} sind es {{ h | round(0) | int }} Prozent Luftfeuchtigkeit.{% elif a %}Für {{ area_name(a) }} hab ich gerade keine Luftfeuchtigkeit.{% else %}Den Raum kenne ich nicht.{% endif %}{% endif %}""")


def room_humidity(house: House) -> dict:
    phrases, words = room_words(house.areas)
    places = ["(im|in der|in dem) {raum}", "hier [drin|drinnen]"]
    if house.weather:
        places.append("(draußen|draussen)")
    command = [sentence.format(place=place) for place in places for sentence in (
        "wie (feucht|trocken) ist es [gerade|jetzt] {place}",
        "wie [hoch] ist die (luftfeuchtigkeit|luftfeuchte|feuchtigkeit) [gerade|jetzt] {place}",
        "wie viel (luftfeuchtigkeit|luftfeuchte|feuchtigkeit) (ist|hat es|haben wir) [gerade|jetzt] {place}")]
    # No place named means the Show's room, as "hier" does. "das" is what speech to text made of "es".
    command += ["wie (feucht|trocken) ist (es|das) [gerade|jetzt]",
                "wie [hoch] ist die (luftfeuchtigkeit|luftfeuchte|feuchtigkeit) [gerade|jetzt]"]
    return {
        "alias": "Genbu - Room humidity",
        "description": "Answers humidity questions locally from the room's own sensor, or the thermostat's "
                       "reading where there is none.",
        "triggers": [{"trigger": "conversation", "command": command}],
        "actions": [{"set_conversation_response": _fill(
            ROOM_HUMIDITY, words=words, sensors=house.humidities, phrases=phrases,
            weather=house.weather or "")}],
        "mode": "parallel",
    }


# For the model: what each room measures. A room's own sensor comes first, the thermostat's reading of
# the room next, and the temperature the heating is set to never, because a 4B model asked how warm it
# is took the setpoint or the thermostat at the radiator over the room sensor (GENBU-139). The values
# are spoken German already, so the model only has to repeat them. The rooms come warmest first, because
# the same model asked whether 17,2 Grad is colder than 17,5 said no; the order answers that for it.
ROOM_CLIMATE = r"""{% set temps = __TEMPERATURES__ %}{% set hums = __HUMIDITIES__ %}{% set de = __NAMES__ %}{% set ns = namespace(rooms=[], warm=[]) %}{% for a in areas() %}{% set cl = area_entities(a) | select('match', 'climate\\.') | list %}{% set t = states(temps[a]) | float(none) if a in temps else none %}{% if t is none %}{% set t = cl | map('state_attr', 'current_temperature') | reject('none') | map('float', none) | reject('none') | list | first | default(none) %}{% endif %}{% set h = states(hums[a]) | float(none) if a in hums else none %}{% if h is none %}{% set h = cl | map('state_attr', 'current_humidity') | reject('none') | map('float', none) | reject('none') | list | first | default(none) %}{% endif %}{% set n = de.get(a, area_name(a)) %}{% set r = dict(raum=n) %}{% if t is not none %}{% set v = '%.1f' | format(t) %}{% set r = dict(r, temperatur=(v[:-2] if v.endswith('.0') else v) | replace('.', ',') ~ ' Grad') %}{% set ns.warm = ns.warm + [[t, n]] %}{% endif %}{% if h is not none %}{% set r = dict(r, luftfeuchtigkeit=(h | round(0) | int) ~ ' Prozent') %}{% endif %}{% if t is not none or h is not none %}{% set ns.rooms = ns.rooms + [[-t if t is not none else 999, ns.rooms | count, r]] %}{% endif %}{% endfor %}{% set out = dict(raeume_vom_waermsten_zum_kaeltesten=ns.rooms | sort | map(attribute='2') | list, hinweis='Gemessene Werte. Ein Raum weiter oben in der Liste ist wärmer als einer weiter unten. Die eingestellte Zieltemperatur der Heizung ist nicht die Raumtemperatur.') %}{% if ns.warm | count > 1 %}{% set w = ns.warm | sort %}{% set out = dict(out, waermster_raum=w[-1][1], kaeltester_raum=w[0][1]) %}{% endif %}{{ out }}"""


def room_climate(house: House) -> dict:
    phrases, _ = room_words(house.areas)
    # The room as German names it, "Küche" from "in der Küche", so the model says the word it heard.
    names = {area: phrase.rsplit(" ", 1)[1] for area, phrase in phrases.items()}
    return {
        "alias": "Raumklima",
        "description": "Liefert für jeden Raum die gemessene Temperatur und Luftfeuchtigkeit, dazu den wärmsten "
                       "und den kältesten Raum. Für jede Frage nach Temperatur, Wärme, Kälte oder "
                       "Luftfeuchtigkeit in Räumen und für Vergleiche zwischen Räumen. Nenne die Werte genau so, "
                       "wie sie im Ergebnis stehen. Die eingestellte Temperatur der Heizung ist nicht die "
                       "Raumtemperatur.",
        "mode": "parallel",
        "sequence": [
            {"variables": {"result": _fill(ROOM_CLIMATE, temperatures=house.temperatures,
                                           humidities=house.humidities, names=names)}},
            {"stop": "Raumklima gelesen.", "response_variable": "result"},
        ],
    }


FORECAST =r"""{% set de = {'sunny': 'sonnig', 'clear-night': 'klar', 'partlycloudy': 'teils bewölkt', 'cloudy': 'bewölkt', 'rainy': 'Regen', 'pouring': 'starker Regen', 'snowy': 'Schnee', 'snowy-rainy': 'Schneeregen', 'fog': 'Nebel', 'hail': 'Hagel', 'lightning': 'Gewitter', 'lightning-rainy': 'Gewitter und Regen', 'windy': 'windig', 'windy-variant': 'windig und bewölkt', 'exceptional': 'Unwetter'} %}{% set s = trigger.sentence | lower %}{% set n = 2 if 'übermorgen' in s or 'uebermorgen' in s else 1 %}{% set day = (now() + timedelta(days=n)).date() %}{% set f = fc[__WEATHER__].forecast | selectattr('datetime', 'search', day | string) | list | first | default(none) %}{% set word = 'Übermorgen' if n == 2 else 'Morgen' %}{% if f is none %}Für {{ word | lower }} hab ich keine Vorhersage.{% else %}{{ word }} {{ de.get(f.condition, f.condition) }}, {{ f.templow | round(0) | int }} bis {{ f.temperature | round(0) | int }} Grad.{% set p = f.precipitation | float(0) %}{% if p >= 0.5 %} Etwa {{ p | round(0) | int if p >= 1.5 else 1 }} Millimeter Regen.{% elif 'regn' in s or 'schirm' in s %} Regen ist keiner angesagt.{% endif %}{% endif %}"""


def forecast(house: House) -> dict:
    return {
        "alias": "Genbu - Forecast",
        "description": "Answers tomorrow's and the day after's weather locally from the forecast, instead of "
                       "the model guessing it.",
        "triggers": [{"trigger": "conversation", "command": [
            "wie (wird|ist) das wetter (morgen|übermorgen)",
            "wie wird (morgen|übermorgen) das wetter",
            "was sagt der wetterbericht für (morgen|übermorgen)",
            "(regnet|wird) es (morgen|übermorgen) [regnen]",
            "wie (warm|kalt) wird es (morgen|übermorgen)",
            "brauche ich (morgen|übermorgen) einen (schirm|regenschirm)"]}],
        "actions": [
            {"action": "weather.get_forecasts", "target": {"entity_id": house.weather},
             "data": {"type": "daily"}, "response_variable": "fc"},
            {"set_conversation_response": _fill(FORECAST, weather=house.weather)}],
        "mode": "parallel",
    }


def volume(house: House) -> dict:
    _, words = room_words(house.areas)
    return {
        "alias": "Genbu - Volume",
        "description": f"Blueprint {BLUEPRINT_FOLDER}/{VOLUME}.",
        "use_blueprint": {"path": f"{BLUEPRINT_FOLDER}/{VOLUME}", "input": {"rooms": words}},
    }


def _number_words() -> dict[str, int]:
    """German number words up to 59, as Whisper writes small numbers out ("in einer Minute", "um
    sieben"), each spelled the way it may come."""
    ones = ["", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun"]
    words = {w: n for n, w in enumerate(ones) if w}
    words.update({"null": 0, "eins": 1, "eine": 1, "einer": 1, "einem": 1, "einen": 1, "zwo": 2,
                  "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15,
                  "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19})
    for tens, t in [("zwanzig", 20), ("dreißig", 30), ("dreissig", 30), ("vierzig", 40), ("fünfzig", 50)]:
        words[tens] = t
        for n in range(1, 10):
            words[f"{ones[n]}und{tens}"] = t + n
    return words


NUMBER_WORDS = _number_words()

# A Show is told apart from other voice devices by its stop-alarm entity. Its actions are named after its
# node, which echod makes from the device's name the way layout.Slug does: a-z and 0-9 kept, any run of
# other characters one separator, none at the ends.
WECKER_VARIABLES = {
    "techo5": "{{ trigger.device_id is not none and device_entities(trigger.device_id) "
              "| select('search', 'stop_alarm') | list | count > 0 }}",
    "node": "{{ device_attr(trigger.device_id, 'name') | lower | regex_replace('[^a-z0-9]+', '_') "
            "| regex_replace('^_+|_+$', '') if techo5 else '' }}",
    # What was said for the time, with number words as digits: "in einer Minute" is "in 1 minute".
    "spoken": "{%- set words = " + repr(NUMBER_WORDS) + " -%}{%- set ns = namespace(out=[]) -%}"
              "{%- for w in ((trigger.slots.when | default('')) | string | lower | replace('.', ':')).split() -%}"
              "{%- set ns.out = ns.out + [words.get(w | trim(',:;!?'), w) | string] -%}"
              "{%- endfor -%}{{ ns.out | join(' ') }}",
    "nums": "{{ spoken | regex_findall('[0-9]{1,2}') | map('int') | list }}",
    "length": "{{ spoken is search('minute|stunde') }}",
    # A length of time in minutes, or -1 when none was heard.
    "minutes": "{%- if spoken is search('dreiviertel ?stunde') -%}45{%- elif spoken is search('viertel ?stunde') -%}15"
               "{%- elif spoken is search('anderthalb|eineinhalb') and 'stunde' in spoken -%}90"
               "{%- elif spoken is search('halben? stunde') -%}30{%- elif not nums and 'stunde' in spoken -%}60"
               "{%- elif not nums -%}-1{%- elif 'stunde' in spoken -%}"
               "{{ nums[0] * 60 + (nums[1] if nums | count > 1 else 0) }}{%- else -%}{{ nums[0] }}{%- endif -%}",
    # "halb acht" is 7:30, "viertel vor acht" and "dreiviertel acht" 7:45, "viertel nach sieben" 7:15.
    # Whisper hears "viertel nach" as "vierte Nacht" now and then.
    "half": "{{ spoken is search('halb') }}",
    "before": "{{ spoken is search('viertel? vor|dreiviertel') }}",
    "past": "{{ spoken is search('viertel nach|vierte nacht') }}",
    "evening": "{{ spoken is search('abend|nachmittag|nachts') }}",
    "h": "{{ (nums[0] - 1 if half or before else nums[0]) if nums else -1 }}",
    "m": "{{ 30 if half else 45 if before else 15 if past else (nums[1] if nums | count > 1 else 0) }}",
    "hh": "{{ h + 12 if evening and 0 <= h < 12 else h }}",
    # Rounded to the nearest minute, so "für 1 Minute" doesn't ring a few seconds later.
    "at": "{%- if length -%}{{ (now() + timedelta(minutes=minutes | int, seconds=30)).strftime('%H:%M') }}"
          "{%- else -%}{{ '%02d:%02d' | format(hh | int, m | int) }}{%- endif -%}",
}


def wecker() -> dict:
    return {
        "alias": "Genbu - Wecker",
        "description": 'Stellt den Wecker auf dem Gerät, das gefragt wurde, ohne das Sprachmodell: "stell einen '
                       'Wecker auf 7 Uhr", "weck mich in 20 Minuten". Das Modell würde sonst nur sagen, der '
                       'Wecker sei gestellt.',
        "triggers": [{"trigger": "conversation", "command": [
            "(stell|stelle) [mir] [einen|den|meinen] wecker (auf|für|um|in) {when}",
            # "Weg mich" is how Whisper often writes "Weck mich".
            "(weck|wecke|weg) mich [bitte] (um|in) {when}",
            "wecker (auf|für|um|in) {when}"]}],
        "actions": [
            {"variables": dict(WECKER_VARIABLES)},
            {"choose": [
                {"conditions": "{{ not techo5 }}", "sequence": [
                    {"set_conversation_response": "Sag das bitte dem Gerät, auf dem der Wecker klingeln soll."}]},
                {"conditions": "{{ length and not (1 <= minutes | int <= 1440) }}", "sequence": [
                    {"set_conversation_response": "Das habe ich nicht verstanden. Wie lange?"}]},
                {"conditions": "{{ not length and (h < 0 or hh > 23 or m > 59) }}", "sequence": [
                    {"set_conversation_response": "Die Uhrzeit habe ich nicht verstanden. Für wann?"}]}],
             "default": [
                 {"action": "esphome.{{ node }}_alarm_set",
                  "data": {"time": "{{ at }}", "days": "once", "label": "Wecker"}},
                 {"set_conversation_response":
                  "{%- if length -%}Wecker klingelt in {{ minutes }} "
                  "{{ 'Minute' if minutes | int == 1 else 'Minuten' }}, um {{ at }} Uhr."
                  "{%- else -%}Wecker steht auf {{ at }} Uhr.{%- endif -%}"}]}],
        "mode": "queued",
    }


# Who started, as said: "Küche", "Küche und Flur", "Küche, Flur und Bad".
GENBU_UPDATING = r"""{%- set u = started.updating if started is mapping else [] -%}{%- if u -%}Die Updates starten auf {% if u | count == 1 %}{{ u[0] }}{% else %}{{ u[:-1] | join(', ') }} und {{ u[-1] }}{% endif %}.{%- else -%}Alle Shows sind auf dem neuesten Stand.{%- endif -%}"""


def genbu_update() -> dict:
    """GENBU-125: told at any Show, every Show with an update installs it, even when the one told has
    none itself. The genbu integration does the work (genbu.install_updates); a yes to a Show's own
    question asks it the same way."""
    return {
        "alias": "Genbu - Updates installieren",
        "description": 'Startet die Updates auf allen Shows, die eins haben, egal welche gefragt wurde: '
                       '"installiere die Updates", "aktualisiere die Shows". Braucht die Genbu-Integration.',
        "triggers": [{"trigger": "conversation", "command": [
            "(installier|installiere|starte|start|mach|mache) [bitte] [alle|die] updates",
            "(aktualisier|aktualisiere|update) [bitte] [alle|die] shows"]}],
        "actions": [
            {"choose": [
                {"conditions": "{{ states('sensor.genbu_house_version') in ['unknown', 'unavailable'] }}",
                 "sequence": [{"set_conversation_response":
                               "Genbu läuft gerade nicht in Home Assistant, deshalb kann ich keine Updates starten."}]}],
             "default": [
                 {"action": "genbu.install_updates", "data": {}, "response_variable": "started"},
                 {"set_conversation_response": GENBU_UPDATING}]}],
        "mode": "single",
    }


# ------------------------------------------------------------------------------- answers in English

# The same answers for a house with an English pipeline. Each English sentence differs from every German
# one, so a house with both languages has both sets side by side; the few words both languages share
# ("hi", "hey", "stop stop") are only in the English set when there is no German one.

WHERE_ARE_WE_EN = r"""{% set en = __PHRASES__ %}{% set a = area_id(trigger.device_id) if trigger.device_id else none %}{% if a in en %}We're {{ en[a] }}.{% elif a %}We're in {{ area_name(a) }}.{% else %}I don't know, I'm not assigned to a room.{% endif %}"""


def where_are_we_en(house: House) -> dict:
    phrases, _ = room_words(house.areas, EN_ROOMS)
    return {
        "alias": "Genbu - Where are we (English)",
        "description": 'Answers "Where are we?" locally with the room of the device that was asked.',
        "triggers": [{"trigger": "conversation", "command": [
            "where (are we|am i) [right now|now]",
            "(which|what) room (are we|am i|are you) in [right now|now]",
            "where are you [right now|now]",
            "(which|what) room is this"]}],
        "actions": [{"set_conversation_response": _fill(WHERE_ARE_WE_EN, phrases=phrases)}],
        "mode": "parallel",
    }


def greeting_en(house: House) -> dict:
    words = "hello|good morning|good afternoon|good evening" + ("" if house.german else "|hi|hey")
    return {
        "alias": "Genbu - Greeting (English)",
        "description": "Hands greetings like \"Hello\" to the assistant's model. Home Assistant would answer "
                       "them itself otherwise.",
        "triggers": [{"trigger": "conversation", "command": [f"({words}) [genbu|home assistant]"]}],
        "actions": [
            {"action": "conversation.process", "data": {
                "agent_id": house.agent, "text": "{{ trigger.sentence }}",
                "language": "{{ trigger.user_input.language | default('en', true) }}"},
             "response_variable": "said", "continue_on_error": True},
            {"set_conversation_response":
                "{{ said.response.speech.plain.speech | default('Hello.', true) if said is defined else 'Hello.' }}"}],
        "mode": "parallel",
    }


ROOM_TEMPERATURE_EN = (r"""{% set rooms = __WORDS__ %}{% set sensors = __SENSORS__ %}{% set en = __PHRASES__ %}{% if 'outside' in (trigger.sentence | lower) %}{% set t = state_attr(__WEATHER__, 'temperature') | float(none) %}{% if t is none %}I don't know the weather right now.{% else %}{% set v = '%.1f' | format(t) %}It's {{ v[:-2] if v.endswith('.0') else v }} degrees outside.{% endif %}{% else %}""" + ROOM_PICK
                       + r"""{% set t = states(sensors[a]) | float(none) if a in sensors else none %}{% if t is none and a %}{% set t = area_entities(a) | select('match', 'climate\\.') | map('state_attr', 'current_temperature') | reject('none') | map('float') | list | first | default(none) %}{% endif %}{% if t is not none %}{% set v = '%.1f' | format(t) %}It's {{ v[:-2] if v.endswith('.0') else v }} degrees {{ en.get(a, 'in ' ~ area_name(a)) }}.{% elif a %}I don't have a reading for {{ area_name(a) }} right now.{% else %}I don't know that room.{% endif %}{% endif %}""")


def room_temperature_en(house: House) -> dict:
    phrases, words = room_words(house.areas, EN_ROOMS)
    asked = ("how (warm|cold|hot) is it [right now|now] {place}",
             "(what's|what is) the temperature [right now|now] {place}",
             "how many degrees is it [right now|now] {place}")
    places = ["(in|on) the {raum}", "in here"] + (["outside"] if house.weather else [])
    command = [sentence.format(place=place) for place in places for sentence in asked]
    # No place named means the Show's room, as "in here" does.
    command += ["how (warm|cold|hot) is it [right now|now]", "(what's|what is) the temperature [right now|now]"]
    return {
        "alias": "Genbu - Room temperature (English)",
        "description": "Answers room temperature questions locally from the room sensor and names the room, "
                       "so it can't be mistaken for the weather.",
        "triggers": [{"trigger": "conversation", "command": command}],
        "actions": [{"set_conversation_response": _fill(
            ROOM_TEMPERATURE_EN, words=words, sensors=house.temperatures, phrases=phrases,
            weather=house.weather or "")}],
        "mode": "parallel",
    }


ROOM_HUMIDITY_EN = (r"""{% set rooms = __WORDS__ %}{% set sensors = __SENSORS__ %}{% set en = __PHRASES__ %}{% if 'outside' in (trigger.sentence | lower) %}{% set h = state_attr(__WEATHER__, 'humidity') | float(none) %}{% if h is none %}I don't know the weather right now.{% else %}It's {{ h | round(0) | int }} percent humidity outside.{% endif %}{% else %}""" + ROOM_PICK
                    + r"""{% set h = states(sensors[a]) | float(none) if a in sensors else none %}{% if h is none and a %}{% set h = area_entities(a) | select('match', 'climate\\.') | map('state_attr', 'current_humidity') | reject('none') | map('float') | list | first | default(none) %}{% endif %}{% if h is not none %}It's {{ h | round(0) | int }} percent humidity {{ en.get(a, 'in ' ~ area_name(a)) }}.{% elif a %}I don't have a humidity reading for {{ area_name(a) }} right now.{% else %}I don't know that room.{% endif %}{% endif %}""")


def room_humidity_en(house: House) -> dict:
    phrases, words = room_words(house.areas, EN_ROOMS)
    asked = ("how (humid|dry) is it [right now|now] {place}",
             "(what's|what is) the humidity [right now|now] {place}",
             "how high is the humidity [right now|now] {place}")
    places = ["(in|on) the {raum}", "in here"] + (["outside"] if house.weather else [])
    command = [sentence.format(place=place) for place in places for sentence in asked]
    command += ["how (humid|dry) is it [right now|now]", "(what's|what is) the humidity [right now|now]",
                "how high is the humidity [right now|now]"]
    return {
        "alias": "Genbu - Room humidity (English)",
        "description": "Answers humidity questions locally from the room's own sensor, or the thermostat's "
                       "reading where there is none.",
        "triggers": [{"trigger": "conversation", "command": command}],
        "actions": [{"set_conversation_response": _fill(
            ROOM_HUMIDITY_EN, words=words, sensors=house.humidities, phrases=phrases,
            weather=house.weather or "")}],
        "mode": "parallel",
    }


FORECAST_EN = r"""{% set en = {'sunny': 'sunny', 'clear-night': 'clear', 'partlycloudy': 'partly cloudy', 'cloudy': 'cloudy', 'rainy': 'rain', 'pouring': 'heavy rain', 'snowy': 'snow', 'snowy-rainy': 'sleet', 'fog': 'fog', 'hail': 'hail', 'lightning': 'thunderstorms', 'lightning-rainy': 'thunderstorms and rain', 'windy': 'windy', 'windy-variant': 'windy and cloudy', 'exceptional': 'severe weather'} %}{% set s = trigger.sentence | lower %}{% set n = 2 if 'day after' in s else 1 %}{% set day = (now() + timedelta(days=n)).date() %}{% set f = fc[__WEATHER__].forecast | selectattr('datetime', 'search', day | string) | list | first | default(none) %}{% set word = 'The day after tomorrow' if n == 2 else 'Tomorrow' %}{% if f is none %}I don't have a forecast for {{ word | lower }}.{% else %}{{ word }}: {{ en.get(f.condition, f.condition) }}, {{ f.templow | round(0) | int }} to {{ f.temperature | round(0) | int }} degrees.{% set p = f.precipitation | float(0) %}{% set mm = p | round(0) | int if p >= 1.5 else 1 %}{% if p >= 0.5 %} About {{ mm }} {{ 'millimeter' if mm == 1 else 'millimeters' }} of rain.{% elif 'rain' in s or 'umbrella' in s %} No rain expected.{% endif %}{% endif %}"""


def forecast_en(house: House) -> dict:
    day = "(tomorrow|the day after tomorrow)"
    return {
        "alias": "Genbu - Forecast (English)",
        "description": "Answers tomorrow's and the day after's weather locally from the forecast, instead of "
                       "the model guessing it.",
        "triggers": [{"trigger": "conversation", "command": [
            f"(what's|what is|how's|how is) the weather [going to be] [like] {day}",
            f"(what's|what is) the (forecast|weather forecast) for {day}",
            f"(will it|is it going to) rain {day}",
            f"how (warm|cold|hot) (will it be|is it going to be) {day}",
            f"do i need an umbrella {day}"]}],
        "actions": [
            {"action": "weather.get_forecasts", "target": {"entity_id": house.weather},
             "data": {"type": "daily"}, "response_variable": "fc"},
            {"set_conversation_response": _fill(FORECAST_EN, weather=house.weather)}],
        "mode": "parallel",
    }


def volume_en(house: House) -> dict:
    _, words = room_words(house.areas, EN_ROOMS)
    return {
        "alias": "Genbu - Volume (English)",
        "description": f"Blueprint {BLUEPRINT_FOLDER}/{VOLUME_EN}.",
        "use_blueprint": {"path": f"{BLUEPRINT_FOLDER}/{VOLUME_EN}", "input": {"rooms": words}},
    }


def _number_words_en() -> dict[str, int]:
    """English number words up to fifty, and "a"/"an" as one ("in an hour", "a quarter past"). Twenty-one
    and the like come as two words, joined in the template."""
    words = {w: n for n, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen".split())}
    words.update({"a": 1, "an": 1, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50})
    return words


NUMBER_WORDS_EN = _number_words_en()

# The alarm for English: the Show is found as for the Wecker, the time is read the English way. "half past
# seven" is 7:30, "quarter to eight" 7:45, "ten past seven" 7:10, "7 pm" and "seven in the evening" 19:00.
WECKER_VARIABLES_EN = {
    "techo5": WECKER_VARIABLES["techo5"],
    "node": WECKER_VARIABLES["node"],
    # What was said for the time, with number words as digits: "in a minute" is "in 1 minute", "seven
    # thirty five" is "7 35", "7.30pm" is "7:30 pm".
    "spoken": r"{%- set words = " + repr(NUMBER_WORDS_EN) + r" -%}{%- set ns = namespace(out=[]) -%}"
              r"{%- set w = (trigger.slots.when | default('')) | string | lower"
              r" | regex_replace('([0-9])([ap])[.]?\\s?m\\b[.]?', '\\1 \\2m')"
              r" | regex_replace('\\b([ap])[.]\\s?m\\b[.]?', '\\1m') | replace('.', ':') | replace('-', ' ') -%}"
              r"{%- for t in w.split() -%}{%- set ns.out = ns.out + [words.get(t | trim(',:;!?'), t) | string] -%}"
              r"{%- endfor -%}{{ ns.out | join(' ') | regex_replace('\\b([2-5])0 ([1-9])\\b', '\\1\\2') }}",
    "nums": "{{ spoken | regex_findall('[0-9]{1,2}') | map('int') | list }}",
    "length": "{{ spoken is search('minute|hour') }}",
    # A length of time in minutes, or -1 when none was heard.
    "minutes": r"{%- if spoken is search('(3|three) quarters? of (1 )?hour') -%}45"
               r"{%- elif spoken is search('quarter (of (1 )?)?hour') -%}15"
               r"{%- elif 'hour' in spoken and spoken is search('and (1 )?half') -%}{{ (nums[0] if nums else 1) * 60 + 30 }}"
               r"{%- elif spoken is search('half (1 )?hour') -%}30"
               r"{%- elif not nums and 'hour' in spoken -%}60{%- elif not nums -%}-1{%- elif 'hour' in spoken -%}"
               r"{{ nums[0] * 60 + (nums[1] if nums | count > 1 else 0) }}{%- else -%}{{ nums[0] }}{%- endif -%}",
    "before": r"{{ spoken is search('\\b(to|till|before)\\b') }}",
    # Minutes counted from the hour: "half past", "quarter to", "ten past"; "half seven" is 7:30 as well.
    "rel": r"{{ before or spoken is search('\\b(past|after|half)\\b') }}",
    "q": "{{ 15 if 'quarter' in spoken else 30 if 'half' in spoken else (nums[0] if nums | count > 1 else 0) }}",
    "pm": r"{{ spoken is search('\\bpm\\b|evening|afternoon|tonight|night') }}",
    "am": r"{{ spoken is search('\\bam\\b|morning') }}",
    "h": "{{ (nums[-1] - 1 if before else nums[-1]) if rel and nums else nums[0] if nums else -1 }}",
    "m": "{{ (60 - q | int if before else q | int) if rel and nums else (nums[1] if nums | count > 1 else 0) }}",
    "hh": "{{ h + 12 if pm and 0 <= h < 12 else 0 if am and h == 12 else h }}",
    "at": WECKER_VARIABLES["at"],
}


def alarm_en() -> dict:
    return {
        "alias": "Genbu - Alarm (English)",
        "description": 'Sets the alarm on the device that was asked, without the language model: "set an alarm '
                       'for 7 am", "wake me up in 20 minutes". The model would only say it had set one.',
        "triggers": [{"trigger": "conversation", "command": [
            "(set|make|create) [me] [(an|a|the|my)] alarm (for|at|to|in) {when}",
            "wake me [up] [please] (at|in) {when}",
            "alarm (for|at|in) {when}"]}],
        "actions": [
            {"variables": dict(WECKER_VARIABLES_EN)},
            {"choose": [
                {"conditions": "{{ not techo5 }}", "sequence": [
                    {"set_conversation_response": "Please say that to the device the alarm should ring on."}]},
                {"conditions": "{{ length and not (1 <= minutes | int <= 1440) }}", "sequence": [
                    {"set_conversation_response": "I didn't catch that. For how long?"}]},
                {"conditions": "{{ not length and (h < 0 or hh > 23 or m > 59) }}", "sequence": [
                    {"set_conversation_response": "I didn't catch the time. For when?"}]}],
             "default": [
                 {"action": "esphome.{{ node }}_alarm_set",
                  "data": {"time": "{{ at }}", "days": "once", "label": "Alarm"}},
                 {"set_conversation_response":
                  "{%- if length -%}The alarm goes off in {{ minutes }} "
                  "{{ 'minute' if minutes | int == 1 else 'minutes' }}, at {{ at }}."
                  "{%- else -%}Alarm set for {{ at }}.{%- endif -%}"}]}],
        "mode": "queued",
    }


GENBU_UPDATING_EN = r"""{%- set u = started.updating if started is mapping else [] -%}{%- if u -%}Updates are starting on {% if u | count == 1 %}{{ u[0] }}{% else %}{{ u[:-1] | join(', ') }} and {{ u[-1] }}{% endif %}.{%- else -%}All Shows are up to date.{%- endif -%}"""


def genbu_update_en() -> dict:
    return {
        "alias": "Genbu - Install updates (English)",
        "description": 'Starts the update on every Show that has one, whichever was asked: "install the '
                       'updates", "update the Shows". Needs the Genbu integration.',
        "triggers": [{"trigger": "conversation", "command": [
            "[please] (install|start|run) [all] [the] updates [please]",
            "[please] update [all] [the] shows [please]"]}],
        "actions": [
            {"choose": [
                {"conditions": "{{ states('sensor.genbu_house_version') in ['unknown', 'unavailable'] }}",
                 "sequence": [{"set_conversation_response":
                               "Genbu isn't running in Home Assistant right now, so I can't start updates."}]}],
             "default": [
                 {"action": "genbu.install_updates", "data": {}, "response_variable": "started"},
                 {"set_conversation_response": GENBU_UPDATING_EN}]}],
        "mode": "single",
    }


# --------------------------------------------------------------------------------------------- music

# Where music asked for at a Show plays: what the genbu integration says for it (its "Music plays on",
# by device id and by assist_satellite), else Music Assistant's player in its room. Never another room.
SHOW_PLAYER = r"""{% set m = state_attr('sensor.genbu_house_version', 'music_players') or {} %}{% set a = area_id(asker) if asker else none %}{{ (m.get(asker) if asker and m is mapping else '') or ((area_entities(a) if a else []) | select('in', integration_entities('music_assistant')) | select('match', 'media_player\\.') | select('has_value') | first | default('')) }}"""

# The player music goes to in the scripts the model calls: the one asked for; else the Show that is
# talking right now (the scripts are not told who asked, but only that Show's assistant is busy);
# else, with no Show talking (typed in the app), the first of the house's players that is there, in
# the order look() found them. With two Shows talking at once it can't tell, and says so.
TARGET_PLAYER = (r"""{% set busy = states.assist_satellite | rejectattr('state', 'in', ['idle', 'unavailable', 'unknown']) | map(attribute='entity_id') | list %}{% set ok = players | select('has_value') | list %}{% if player | default('', true) %}{{ player }}{% elif busy | count == 1 %}{% set asker = busy[0] %}"""
                 + SHOW_PLAYER + r"""{% elif busy | count == 0 %}{{ (ok + players) | first | default('') }}{% endif %}""")

# In the local music automation: where music asked for at the device that heard it plays.
ROOM_PLAYER = r"""{% set asker = trigger.device_id | default('', true) %}""" + SHOW_PLAYER

NO_PLAYER = "Hier gibt es keinen Lautsprecher für Musik. Stell ihn in Home Assistant bei der Show unter Musik spielt auf ein."

# Every player in the Show's line is offline (the room's speaker unplugged, and Music Assistant can't
# reach the Show's own), so target_player is one that can't play.
PLAYER_OFFLINE = "Der Lautsprecher hier ist gerade nicht erreichbar."

# The Show talking right now, as in TARGET_PLAYER; '' with none or two at once.
ASKER = r"""{% set busy = states.assist_satellite | rejectattr('state', 'in', ['idle', 'unavailable', 'unknown']) | map(attribute='entity_id') | list %}{{ busy[0] if busy | count == 1 else '' }}"""

# Whether Genbu has genbu.music_route (1.4.3): an action Home Assistant doesn't know stops a script even
# with continue_on_error, so it is only called where it is.
ROUTED = "state_attr('sensor.genbu_house_version', 'music_route') | default(false, true)"

# What the model is told about the speaker facts in each music script's answer (GENBU-161).
SPEAKER_NOTE = (" In der Antwort steht unter speaker, wo die Musik spielt. Ist speaker.tell wahr, kurz sagen, dass "
                "der Lautsprecher aus speaker.skipped gerade aus ist und die Musik jetzt woanders spielt (own: hier "
                "auf der Show, sonst auf speaker.name); sonst den ausgefallenen Lautsprecher nur nennen, wenn danach "
                "gefragt wird. Steht speaker.asked_off da, spielt nichts: sagen, dass dieser Lautsprecher aus ist, "
                "und anbieten, stattdessen auf speaker.offer zu spielen.")

PLAYER_FIELD = {"name": "Player", "description": "Music Assistant player; empty for the usual one.",
                "selector": {"entity": {"filter": [{"integration": "music_assistant", "domain": "media_player"}]}}}

RADIO_HEARD = r"""{% set stop = ['spiel', 'spiele', 'viel', 'biele', 'hör', 'höre', 'hoer', 'hoere', 'mach', 'mache', 'doch', 'mal', 'bitte', 'den', 'das', 'die', 'sender', 'an', 'ein', 'einschalten'] %}
{{ ((name | default('') | string | lower | regex_replace('[^a-z0-9äöüß]+', ' ')
    | regex_replace('(^| )(spiele?|viel|biele|höre?|hoere?)radio', '\\1radio')).split() | reject('in', stop) | list | join(' ')) }}"""

RADIO_BEST = r"""{% set generic = ['radio', 'hit', 'fm', 'sender', 'the', 'der', 'die', 'das'] %}
{% set h = heard | trim %}
{% set hs = h | replace(' ', '') %}
{% set items = stations.get('items', []) %}
{% set ns = namespace(name='', uri='', score=0) %}
{% if h in ['', 'radio'] and items %}
  {% set ns.name = items[0].name %}{% set ns.uri = items[0].uri %}{% set ns.score = 100 %}
{% endif %}
{% for s in items if ns.score < 100 %}
  {% set n = (s.name | lower | regex_replace('[^a-z0-9äöüß]+', ' ')) | trim %}
  {% set nsq = n | replace(' ', '') %}
  {% set words = n.split() | reject('in', generic) | select('ne', '') | list %}
  {% set found = words | select('in', hs) | list | count %}
  {% set sc = 100 if nsq and nsq in hs
         else 80 if hs | length >= 3 and hs in nsq
         else ((50 + 50 * found / (words | count)) | round(0) | int) if found
         else 0 %}
  {% if sc > ns.score %}{% set ns.name = s.name %}{% set ns.uri = s.uri %}{% set ns.score = sc %}{% endif %}
{% endfor %}
{{ {'name': ns.name, 'uri': ns.uri, 'score': ns.score} }}"""


# Whether a voice assistant near the player is thinking or speaking: the ones in the player's area, or
# every one when the player has none.
SATELLITES_BUSY_EXPR = (
    "states.assist_satellite | selectattr('state', 'in', ['processing', 'responding'])"
    " | map(attribute='entity_id') | select('in', area_entities(area_id(player)) if area_id(player)"
    " else states.assist_satellite | map(attribute='entity_id') | list) | list | count > 0")
SATELLITES_BUSY = "{{ " + SATELLITES_BUSY_EXPR + " }}"


def music_start() -> dict:
    return {
        "alias": "Genbu Musik starten",
        "description": "Startet Musik auf einem Player, ohne dass der Aufrufer warten muss. Nur für die "
                       "Genbu-Musikskripte, nicht für Assist freigeben.",
        "mode": "restart",
        "fields": {
            "player": {"description": "Player-Entität."},
            "media_id": {"description": "URI aus Music Assistant."},
            "media_type": {"description": "track, artist, playlist, album oder radio."},
            "radio": {"description": "Ähnliche Lieder anhängen."},
            "mix": {"description": "Weitere URIs, deren ähnliche Lieder mit hineingemischt werden (mit radio)."},
            # Not "shuffle": Home Assistant has a template function by that name, which an unset field
            # would read as.
            "shuffled": {"description": "Zufällige Reihenfolge."},
            "enqueue": {"description": "replace (Standard) oder play."},
        },
        "sequence": [
            # The answer first. Asked by voice, this starts while the voice assistant is still working
            # out what to say, and the music would talk over it. So it waits until no voice assistant in
            # the player's room is thinking or speaking (any of them, if the player has no room). That
            # follows the answer however long it is: Home Assistant reports a satellite speaking until
            # its reply has played. The second look is for the moment between thinking and speaking,
            # when it can briefly read as idle.
            {"if": [{"condition": "template", "value_template": SATELLITES_BUSY}],
             "then": [{"wait_template": "{{ not (" + SATELLITES_BUSY_EXPR + ") }}",
                       "timeout": 45, "continue_on_timeout": True},
                      {"delay": {"milliseconds": 800}},
                      {"wait_template": "{{ not (" + SATELLITES_BUSY_EXPR + ") }}",
                       "timeout": 30, "continue_on_timeout": True}]},
            {"if": [{"condition": "template", "value_template": "{{ shuffled | default(false) | bool }}"}],
             "then": [{"action": "media_player.shuffle_set", "target": {"entity_id": "{{ player }}"},
                       "data": {"shuffle": True}, "continue_on_error": True}]},
            # Played plainly first, so it starts at once. With radio mode Music Assistant builds the
            # whole mix of similar songs before the first note (15-20 s); added behind the playing
            # track, it builds it there instead and leaves the track playing. The mix keeps the queue
            # going on its own, and if it cannot be made the track still plays. The mix's other seeds go in
            # the same call, so Music Assistant builds it once: each becomes a source of the queue's mix,
            # weighted equally, a genre included (Music Assistant makes a genre's mix from its songs and
            # similar ones). One it cannot find is left out.
            {"action": "music_assistant.play_media",
             "data": {"media_id": "{{ media_id }}", "media_type": "{{ media_type }}",
                      "enqueue": "{{ enqueue | default('replace') }}"},
             "target": {"entity_id": "{{ player }}"}},
            {"if": [{"condition": "template", "value_template": "{{ radio | default(false) | bool }}"}],
             "then": [{"action": "music_assistant.play_media",
                       "data": {"media_id": "{{ [media_id] + (mix | default([], true) | list) }}",
                                "media_type": "{{ media_type }}", "enqueue": "add", "radio_mode": True},
                       "target": {"entity_id": "{{ player }}"}, "continue_on_error": True}]},
        ],
    }


def _start(media_id: str, media_type: str, **more: str) -> dict:
    # Started without waiting: Music Assistant can take many seconds to return (15-20 s with radio
    # mode), and the answer waits for the calling script.
    return {"action": "script.turn_on", "target": {"entity_id": "script.jarvis_music_start"},
            "data": {"variables": {"player": "{{ target_player }}", "media_id": media_id,
                                   "media_type": media_type, **more}}}


def _where() -> list[dict]:
    """Where the music goes, asked of Genbu where it can tell (GENBU-161): it knows which speakers in the
    Show's line are off, which it passed over, and whether to mention that now. route, set to {} in the
    script's first step so the answer lands there, carries those facts into the result as speaker."""
    return [{"if": [{"condition": "template",
                     "value_template": "{{ (" + ROUTED + ") and (asker or player | default('', true)) | length > 0 }}"}],
             "then": [{"action": "genbu.music_route",
                       "data": {"show": "{{ asker }}", "player": "{{ player | default('', true) }}"},
                       "response_variable": "route", "continue_on_error": True}]},
            {"variables": {"target_player": "{{ route.plays_on if route.plays_on is defined else target_player }}"}},
            {"if": [{"condition": "template", "value_template": "{{ route.asked_off is defined }}"}],
             "then": [{"variables": {"result": {
                          "success": False, "speaker": "{{ route }}",
                          "message": "{{ route.asked_off.name }} ist gerade nicht erreichbar."}}},
                      {"stop": "Player offline.", "response_variable": "result"}]},
            {"if": [{"condition": "template",
                     "value_template": "{{ not target_player and not route.skipped | default([]) }}"}],
             "then": [{"variables": {"result": {"success": False, "message": NO_PLAYER}}},
                      {"stop": "Kein Player.", "response_variable": "result"}]},
            {"if": [{"condition": "template", "value_template": "{{ not target_player or not has_value(target_player) }}"}],
             "then": [{"variables": {"result": {"success": False, "message": PLAYER_OFFLINE, "speaker": "{{ route }}"}}},
                      {"stop": "Player offline.", "response_variable": "result"}]}]


def play_radio(house: House) -> dict:
    entry = house.music_entry
    return {
        "alias": "Radiosender abspielen",
        "description": 'Spielt einen Radiosender über Music Assistant. Immer verwenden, wenn ein Radiosender '
                       'oder einfach "Radio" gewünscht wird, auch wenn der Name verhört klingt ("Biele Hit '
                       'Radio FFH" heißt Hitradio FFH). Den gehörten Namen unverändert in name weitergeben. '
                       'Die Antwort nennt den Sender, der jetzt läuft, oder die Sender, die es gibt.' + SPEAKER_NOTE,
        "mode": "restart",
        "fields": {
            "name": {"name": "Sender", "description": "Der Sendername so, wie er gehört wurde. Leer für den "
                                                      "üblichen Sender.", "selector": {"text": {}}},
            "player": PLAYER_FIELD,
        },
        "sequence": [
            {"variables": {"players": house.players, "target_player": TARGET_PLAYER, "heard": RADIO_HEARD,
                           "asker": ASKER, "route": {}}},
            *_where(),
            {"action": "music_assistant.get_library", "data": {
                "config_entry_id": entry, "media_type": "radio", "limit": 500}, "response_variable": "stations"},
            # The closest station: its whole name in what was heard ("biele hit radio ffh" has
            # "hitradioffh"), what was heard in its name ("ffh"), or failing that how many of its own
            # words were heard. "Radio" alone is the first station.
            {"variables": {"best": RADIO_BEST}},
            # Not one of ours: maybe a provider knows it.
            {"if": [{"condition": "template", "value_template": "{{ best.score < 50 and heard | trim != '' }}"}],
             "then": [
                 {"action": "music_assistant.search", "data": {
                     "config_entry_id": entry, "name": "{{ heard }}", "media_type": "radio", "limit": 5},
                  "response_variable": "found", "continue_on_error": True},
                 {"variables": {"best": "{% set r = (found | default({})).get('radio', []) if found is defined "
                                        "and found is mapping else [] %}{{ {'name': r[0].name, 'uri': r[0].uri, "
                                        "'score': 50} if r else best }}"}}]},
            {"if": [{"condition": "template", "value_template": "{{ best.score < 50 }}"}],
             "then": [
                 {"variables": {"result": {
                     "success": False,
                     "stations": "{{ stations.get('items', []) | map(attribute='name') | list }}",
                     "message": "{% set n = stations.get('items', []) | map(attribute='name') | list %}"
                                "{% if n %}Den Sender kenne ich nicht. Ich habe {{ n | join(', ') }}."
                                "{% else %}Ich habe keine Radiosender.{% endif %}"}}},
                 {"stop": "Sender nicht gefunden.", "response_variable": "result"}]},
            _start("{{ best.uri }}", "radio"),
            {"variables": {"result": {"success": True, "station": "{{ best.name }}", "speaker": "{{ route }}",
                                      "message": "Spiele {{ best.name }}."}}},
            {"stop": "Sender läuft.", "response_variable": "result"},
        ],
    }


# The library may have no genres, so a mood becomes words to look for in names: playlists first ("laut"
# finds one with "metal" in its name), then artists, then tracks. "von X" is an artist; anything else is
# looked up as said.
MOOD_PLAN = r"""{% set stop = ['spiel', 'spiele', 'viel', 'biele', 'doch', 'mal', 'einfach', 'bitte', 'was', 'etwas', 'irgendwas', 'irgendwelche', 'irgendeine', 'ein', 'eine', 'bisschen', 'musik', 'lieder', 'songs', 'song', 'mir', 'uns', 'die', 'der', 'das', 'den', 'an', 'mach', 'mache', 'gute', 'schöne', 'schönes', 'schön', 'gutes', 'tolles', 'neue', 'neues', 'mehr', 'so', 'meine'] %}
{% set t = (query | default('') | string | lower | regex_replace('[^a-z0-9äöüß]+', ' ')).split() | reject('in', stop) | list | join(' ') %}
{% if t | regex_search('^von ') %}{{ {'kind': 'artist', 'terms': [t[4:]]} }}
{% elif t | regex_search('\\b(flott|schnell|tanz|party|fetz|laune|fröhlich|froh|happy|schwung|disco)') %}{{ {'kind': 'tracks', 'terms': ['dance', 'party', 'disco']} }}
{% elif t | regex_search('\\b(ruhig|entspann|chill|leise|sanft|gemütlich|kuschel|romant|langsam|schlaf)') %}{{ {'kind': 'tracks', 'terms': ['love', 'chill', 'night']} }}
{% elif t | regex_search('\\b(laut|hart|heftig|krach|wütend|aggress|rock)|\\bmetal(?!lica)') %}{{ {'kind': 'playlist', 'terms': ['metal', 'rock', 'hard']} }}
{% elif t | regex_search('\\b90|neunzig') %}{{ {'kind': 'playlist', 'terms': ['90s', '90er', 'neunziger']} }}
{% elif t | regex_search('lieblings|favorit') %}{{ {'kind': 'playlist', 'terms': ['All favorited tracks']} }}
{% elif t %}{{ {'kind': 'any', 'terms': [t]} }}
{% else %}{{ {'kind': 'random', 'terms': []} }}{% endif %}"""

# Spoken titles drop "(Remastered 2011)" or "[Live]" parts until the music pipeline cleans the tags itself.
SPOKEN = r"regex_replace(' *[(][^)]*[)]| *\\[[^]]*\\]', '') | trim"

MOOD_PICK = r"""{% set ns = namespace(pl=[], ar=[], tr=[], p=none, a=none) %}
{% for r in [s1, s2, s3] %}
  {% set ns.pl = ns.pl + r.get('playlists', []) %}
  {% set ns.ar = ns.ar + r.get('artists', []) %}
  {% set ns.tr = ns.tr + r.get('tracks', []) %}
{% endfor %}
{% set wants = plan.terms | map('lower') | map('regex_replace', '[^a-z0-9äöüß]+', '') | select | list %}
{% for want in wants %}
  {% for p in ns.pl if ns.p is none %}
    {% set n = p.name | lower | regex_replace('[^a-z0-9äöüß]+', '') %}
    {% if n and (want in n or n in want) %}{% set ns.p = p %}{% endif %}
  {% endfor %}
  {% for a in ns.ar if ns.a is none %}
    {% set n = a.name | lower | regex_replace('[^a-z0-9äöüß]+', '') %}
    {% if n and (want in n or n in want) %}{% set ns.a = a %}{% endif %}
  {% endfor %}
{% endfor %}
{% set k = plan.kind %}
{% if k in ['playlist', 'any'] and ns.p %}
  {{ {'media_type': 'playlist', 'uri': ns.p.uri, 'title': (ns.p.name | regex_replace(' *[(][^)]*[)]| *\\[[^]]*\\]', '') | trim) or ns.p.name, 'artist': '', 'radio': false, 'shuffle': true, 'fallback': false} }}
{% elif k in ['artist', 'any'] and ns.a %}
  {{ {'media_type': 'artist', 'uri': ns.a.uri, 'title': '', 'artist': ns.a.name, 'radio': false, 'shuffle': true, 'fallback': false} }}
{% else %}
  {% set fb = k == 'random' or not ns.tr %}
  {% set pool = rnd.get('items', []) if fb else ns.tr %}
  {% if pool %}
    {% set t = pool | random %}
    {% set ar = t.artists | default([]) %}
    {{ {'media_type': 'track', 'uri': t.uri, 'title': (t.name | regex_replace(' *[(][^)]*[)]| *\\[[^]]*\\]', '') | trim) or t.name, 'artist': (ar[0].name if ar and ar[0] is mapping else ar[0] if ar else ''), 'radio': true, 'shuffle': false, 'fallback': fb and k != 'random'} }}
  {% else %}
    {{ {'media_type': 'none'} }}
  {% endif %}
{% endif %}"""

MOOD_MESSAGE = r"""{% if pick.media_type == 'playlist' %}Spiele die Playlist {{ pick.title }}.
{% elif pick.media_type == 'artist' %}Spiele Musik von {{ pick.artist }}.
{% elif pick.fallback %}{{ 'Von ' ~ (plan.terms[0] | title) ~ ' habe ich nichts.' if plan.kind == 'artist' else 'Dazu habe ich nichts gefunden.' }} Ich spiele {{ pick.title }}{{ ' von ' ~ pick.artist if pick.artist }}.
{% else %}Spiele {{ pick.title }}{{ ' von ' ~ pick.artist if pick.artist }} und ähnliche Lieder.{% endif %}"""


def play_mood(house: House) -> dict:
    entry = house.music_entry

    def search(n: int, term: str) -> dict:
        return {"action": "music_assistant.search", "data": {
            "name": term, "config_entry_id": entry, "media_type": ["playlist", "artist", "track"],
            "limit": 25, "library_only": True}, "response_variable": f"s{n}"}
    return {
        "alias": "Musik nach Stimmung abspielen",
        "description": 'Spielt Musik aus der eigenen Bibliothek nach Stimmung, Art, Jahrzehnt, Künstler oder '
                       'Playlist, zum Beispiel "was Flottes", "was Ruhiges", "laute Musik", "90er", "meine '
                       'Lieblingslieder" oder "von Helene Fischer". Den Wunsch wörtlich in query weitergeben. '
                       'Für Radiosender stattdessen Radiosender abspielen verwenden. Die Antwort sagt, was jetzt '
                       'läuft.' + SPEAKER_NOTE,
        "mode": "restart",
        "fields": {
            "query": {"name": "Wunsch", "description": 'Was gewünscht wurde, wörtlich, z. B. "was flottes" oder '
                                                       '"von queen".', "selector": {"text": {}}},
            "player": PLAYER_FIELD,
        },
        "sequence": [
            {"variables": {"players": house.players, "target_player": TARGET_PLAYER, "plan": MOOD_PLAN,
                           "asker": ASKER, "route": {}}},
            *_where(),
            # Three lookups whatever the plan, so the answers are there below; each takes about 10 ms.
            search(1, "{{ plan.terms[0] | default('-') }}"),
            search(2, "{{ plan.terms[1] | default(plan.terms[0] | default('-')) }}"),
            search(3, "{{ plan.terms[2] | default(plan.terms[0] | default('-')) }}"),
            {"action": "music_assistant.get_library", "data": {
                "config_entry_id": entry, "media_type": "track", "limit": 1, "order_by": "random"},
             "response_variable": "rnd"},
            {"variables": {"pick": MOOD_PICK}},
            {"if": [{"condition": "template", "value_template": "{{ pick.media_type == 'none' }}"}],
             "then": [{"variables": {"result": {"success": False,
                                                "message": "Ich finde gerade keine Musik in der Bibliothek."}}},
                      {"stop": "Keine Musik.", "response_variable": "result"}]},
            _start("{{ pick.uri }}", "{{ pick.media_type }}", radio="{{ pick.radio }}", shuffled="{{ pick.shuffle }}"),
            {"variables": {"result": {"success": True, "title": "{{ pick.title }}", "artist": "{{ pick.artist }}",
                                      "speaker": "{{ route }}", "message": MOOD_MESSAGE}}},
            {"stop": "Musik läuft.", "response_variable": "result"},
        ],
    }


# The genres mixed into random music, by the time of day: calm ones at night, lighter ones in the
# morning, the big ones by day, and the evening's warmer ones. Keys are genre names folded by _genre_key.
GENRE_HOURS = {
    "night": ["ambient", "classical", "jazz", "soul", "blues", "folk", "newage"],          # 22-6
    "morning": ["soul", "funk", "folk", "pop", "swing", "jazz", "reggae", "country"],    # 6-11
    "day": ["pop", "rock", "electronic", "hiphop", "funk", "disco", "latin", "dance", "reggae"],  # 11-18
    "evening": ["jazz", "soul", "rb", "blues", "swing", "funk", "disco"],                # 18-22
}
GENRE_SLOT = ("{% set h = now().hour %}{{ 'night' if h >= 22 or h < 6 else 'morning' if h < 11 "
              "else 'day' if h < 18 else 'evening' }}")
_GENRE_ALIASES = {"klassik": "classical", "elektro": "electronic", "electronica": "electronic",
                  "rap": "hiphop", "rnb": "rb", "rhythmandblues": "rb"}
_GENRE_SPOKEN = {"classical": "Klassik", "electronic": "Elektro", "rb": "R und B", "newage": "New Age"}


def _genre_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return _GENRE_ALIASES.get(key, key)


def genre_hours(genres: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """The house's genres for each time of day, as GENRE_HOURS has them, with the name the answer says."""
    keyed = {}
    for uri, name in sorted(genres.items()):
        keyed.setdefault(_genre_key(name), (uri, name))
    return {slot: [{"uri": keyed[k][0], "name": _GENRE_SPOKEN.get(k, keyed[k][1])} for k in keys if k in keyed]
            for slot, keys in GENRE_HOURS.items()}


# Random music is three random songs from the library: the first plays at once, and all three seed the
# mix of similar songs that follows, so it does not stay near one song. With the house's genres known, a
# genre that fits the time of day is a fourth seed, about a quarter of what plays.
RANDOM_SEEDS = 3


def play_random(house: House) -> dict:
    first = "random_track['items'][0]"
    hours = genre_hours(house.genres) if house.genres else {}
    genre = [{"variables": {"genre_hours": hours, "genre_slot": GENRE_SLOT}},
             {"variables": {"genre": "{% set g = genre_hours.get(genre_slot) or [] %}"
                                     "{{ g | random if g else {} }}"}}] if any(hours.values()) else \
            [{"variables": {"genre": {}}}]
    return {
        "alias": "Play Random Music",
        "description": "Startet sofort zufällige Musik auf dem Lautsprecher im Raum. Dieses Werkzeug ist für "
                       "unspezifische Wünsche wie 'Spiele Musik', 'Mach Musik an' oder 'Spiel irgendwas'. Nicht "
                       "nach Künstler oder Genre fragen. Die Antwort sagt, welches Lied läuft." + SPEAKER_NOTE,
        "mode": "restart",
        "fields": {"player": PLAYER_FIELD},
        "sequence": [
            {"variables": {"players": house.players, "target_player": TARGET_PLAYER, "asker": ASKER, "route": {}}},
            *_where(),
            {"action": "music_assistant.get_library", "data": {
                "config_entry_id": house.music_entry, "limit": RANDOM_SEEDS, "media_type": "track",
                "order_by": "random"},
             "response_variable": "random_track"},
            {"if": [{"condition": "template",
                     "value_template": "{{ random_track.get('items', []) | length == 0 }}"}],
             "then": [{"variables": {"result": {"message": "Keine Musik in der Music-Assistant-Bibliothek gefunden.",
                                                "success": False}}},
                      {"response_variable": "result", "stop": "Keine Musik verfügbar."}]},
            *genre,
            _start("{{ " + first + ".uri }}", "track", radio=True, enqueue="play",
                   mix="{{ (random_track['items'][1:] | map(attribute='uri') | select | list)"
                       " + ([genre.uri] if genre.uri is defined else []) }}"),
            {"variables": {
                "title": "{{ (" + first + ".name | " + SPOKEN + ") or " + first + ".name }}",
                "artist": "{% set ar = " + first + ".artists | default([]) %}{{ ar[0].name if ar and ar[0] is "
                          "mapping else ar[0] if ar else '' }}",
                "result": {"message": "Spiele {{ title }}{{ ' von ' ~ artist if artist }}"
                                      "{{ ', dazu ähnliche Lieder und etwas ' ~ genre.name if genre.name is defined "
                                      "else ' und ähnliche Lieder' }}.",
                           "title": "{{ title }}", "artist": "{{ artist }}", "success": True,
                           "speaker": "{{ route }}"}}},
            {"response_variable": "result", "stop": "Wiedergabe gestartet."},
        ],
    }


LOCAL_MUSIC_KIND = r"""{% if trigger.id == 'toggle' %}toggle{% elif 'station' in trigger.slots or said | regex_search('(radio|sender)\\b') %}radio{% elif 'mood' in trigger.slots and trigger.slots.mood | lower | trim in ['leiser', 'lauter'] %}volume{% elif 'mood' in trigger.slots %}mood{% else %}random{% endif %}"""

# The media players playing in the room of the device that heard it, and the target player; Music
# Assistant's own first, so a speaker it plays through is not paused twice over.
ROOM_PLAYING = r"""{% set a = area_id(trigger.device_id) if trigger.device_id else none %}{% set p = ((area_entities(a) if a else []) | select('match', 'media_player\\.') | list) + ([target_player] if target_player else []) %}{% set p = p | unique | select('is_state', 'playing') | list %}{% set ma = p | select('in', integration_entities('music_assistant')) | list %}{{ ma or p }}"""

NOW_PLAYING = (r"""{% set t = state_attr(playing[0], 'media_title') or '' %}{% set ar = state_attr(playing[0], 'media_artist') or '' %}{% set t = (t | """
               + SPOKEN + r""") or t %}{% if t %}Gerade läuft {{ t }}{{ ' von ' ~ ar if ar }}.{% else %}Hier läuft schon Musik.{% endif %}""")

IN_ROOM = "(im|in der|in dem|auf der|auf dem) {raum}"

# "Mach die Musik im Schlafzimmer aus": the room named, '' when there is no such room.
STOP_AREA = "{% set rooms = __WORDS__ %}" + ROOM_PICK + "{{ a or '' }}"

# What plays in the room named: its media players, and the music player of each Show standing there
# (a Music Assistant player often has no room of its own); Music Assistant's own first, as above.
NAMED_PLAYING = r"""{% set m = state_attr('sensor.genbu_house_version', 'music_players') or {} %}{% set ns = namespace(p=[]) %}{% for k, v in (m.items() if m is mapping else []) if area and v and area_id(k) == area %}{% set ns.p = ns.p + [v] %}{% endfor %}{% set p = ((area_entities(area) if area else []) | select('match', 'media_player\\.') | list) + ns.p %}{% set p = p | unique | select('is_state', 'playing') | list %}{% set ma = p | select('in', integration_entities('music_assistant')) | list %}{{ ma or p }}"""

STOP_NOTHING = r"""{% set de = __PHRASES__ %}{% if trigger.id != 'room' %}Hier läuft gerade nichts.{% elif not area %}Den Raum kenne ich nicht.{% else %}{% set w = de.get(area, 'in ' ~ area_name(area)) %}{{ w[0] | upper }}{{ w[1:] }} läuft gerade nichts.{% endif %}"""

STOP_NOTHING_EN = r"""{% set en = __PHRASES__ %}{% if trigger.id != 'room' %}Nothing's playing here.{% elif not area %}I don't know that room.{% else %}Nothing's playing {{ en.get(area, 'in ' ~ area_name(area)) }}.{% endif %}"""


def _stop_actions(house: House, words: dict, phrases: dict, paused: str, nothing: str) -> list[dict]:
    return [
        {"variables": {"room_player": ROOM_PLAYER, "players": house.players, "target_player": LOCAL_TARGET}},
        # One step, so both are set for what follows: a variable set inside an if stays there.
        {"variables": {"area": "{% if trigger.id == 'room' %}" + _fill(STOP_AREA, words=words) + "{% endif %}",
                       "playing": "{% if trigger.id == 'room' %}" + NAMED_PLAYING + "{% else %}" + ROOM_PLAYING
                                  + "{% endif %}"}},
        {"if": "{{ playing | count > 0 }}",
         "then": [{"action": "media_player.media_pause", "target": {"entity_id": "{{ playing }}"},
                   "continue_on_error": True},
                  {"set_conversation_response": paused}],
         "else": [{"set_conversation_response": _fill(nothing, phrases=phrases)}]},
    ]


# Asked at a Show whose speaker is off and the music plays elsewhere (GENBU-161): said before the answer
# the first time, then at most once an hour, as genbu.music_route decides (route.tell).
SKIPPED = r"""{% set n = route.skipped | default([]) | map(attribute='name') | list %}{% if route.tell | default(false) and n and route.plays_on | default('') and r is defined and r is mapping and r.success | default(false) %}"""
SKIP_NOTE = SKIPPED + r"""{{ n | join(' und ') }} {{ 'ist' if n | count == 1 else 'sind' }} gerade aus, deshalb spiele ich {{ 'hier' if route.own else 'auf ' ~ route.name }}. {% endif %}"""
SKIP_NOTE_EN = SKIPPED + r"""{{ n | join(' and ') }} {{ 'is' if n | count == 1 else 'are' }} off right now, so I'm playing {{ 'here' if route.own else 'on ' ~ route.name }}. {% endif %}"""


def _routed(script: str, data: dict) -> list[dict]:
    """Asks Genbu where the music goes from the Show that heard it, then has the script play there."""
    return [{"if": "{{ trigger.device_id | default('', true) and (" + ROUTED + ") }}",
             "then": [{"action": "genbu.music_route", "data": {"show": "{{ trigger.device_id }}"},
                       "response_variable": "route", "continue_on_error": True}]},
            {"action": script, "data": {**data, "player": "{{ route.plays_on if route.plays_on | default('', true) "
                                                          "else room_player }}"},
             "response_variable": "r", "continue_on_error": True}]


# Asked at a device, only its own player; typed, the house's usual one.
LOCAL_TARGET = ("{% set ok = players | select('has_value') | list %}"
                "{{ room_player if trigger.device_id else room_player or (ok + players) | first | default('') }}")


def local_music(house: House) -> dict:
    def answered(script: str, data: dict, fallback: str) -> list[dict]:
        return [*_routed(script, data),
                {"set_conversation_response": SKIP_NOTE + "{{ r.message if r is defined and r is mapping and "
                                              "r.message is defined else '" + fallback + "' }}"}]

    def step(action: str) -> dict:
        return {"action": action, "target": {"entity_id": "{{ target_player }}"}, "continue_on_error": True}
    return {
        "alias": "Genbu - Local Music",
        "description": 'Musik ohne das Sprachmodell: zufällig, nach Stimmung ("spiel was Flottes", "viel laute '
                       'Musik", "Musik von ..."), oder Radio ("Biele Hit Radio FFH"), im Raum des Geräts, das '
                       'gefragt wurde. Sagt, was jetzt läuft. Alle Sätze stehen in einem Auslöser, damit nie '
                       'zwei Läufe gleichzeitig starten; die Sätze bleiben eng, weil ein Satz, der hier passt, '
                       'nie beim Sprachmodell ankommt. Nur "die Musik" hat einen eigenen Auslöser: läuft hier '
                       'schon etwas, sagt es nur, was.',
        "mode": "single",
        "triggers": [{"trigger": "conversation", "command": [
            "[ich] [bitte] (spiel|spiele|spielen) [doch] [mal] [einfach] [bitte] "
            "[(irgendwelche|irgendeine|ein bisschen|etwas|die)] musik [ab] [bitte]",
            "spiel[e] [doch] [mal] [einfach] (was|etwas|irgendwas)",
            # Whisper often writes the two words as one: "Spielmusik", "Spielradio FFH".
            "(spielmusik|spielemusik) [bitte]",
            "spiel[e] musik spiel[e] musik",
            "[bitte] musik (spielen|abspielen|ab) [bitte]",
            "(mach|mache) [doch] [mal] [bitte] [etwas] musik",
            "[mach|mache] [doch] [mal] [(die|etwas)] musik an",
            "musik an",
            "(spiel|spiele|viel|biele) [doch] [mal] [einfach] (was|etwas|irgendwas) {mood}",
            "(spiel|spiele|viel|biele) [doch] [mal] [einfach] [(was|etwas|ein bisschen)] {mood} musik",
            "(spiel|spiele|viel|biele) [doch] [mal] [einfach] [(was|etwas)] musik von {mood}",
            "(spiel|spiele|viel|biele) [doch] [mal] (lieder|songs) von {mood}",
            "[(spiel|spiele|viel|biele|mach|mache|hör|höre)] [doch] [mal] [das] radio [an]",
            "(spiel|spiele|viel|biele|hör|höre) [doch] [mal] {station} radio",
            "(spiel|spiele|viel|biele|hör|höre) [doch] [mal] {station} radio {rest}",
            "(spiel|spiele|viel|biele|hör|höre) [doch] [mal] radio {station}",
            "(spiel|spiele|viel|biele|hör|höre) [doch] [mal] [den] sender {station}",
            "(spielradio|spieleradio|hörradio|höreradio) [bitte]",
            "(spielradio|spieleradio|hörradio|höreradio) {station}"]},
            # Just "die Musik": start some if nothing plays here, else only say what is on.
            {"trigger": "conversation", "id": "toggle", "command": ["[bitte] [die] musik [bitte]"]}],
        "actions": [
            {"variables": {"said": "{{ trigger.sentence | lower }}", "kind": LOCAL_MUSIC_KIND,
                           "room_player": ROOM_PLAYER, "players": house.players,
                           "target_player": LOCAL_TARGET, "route": {}}},
            {"choose": [
                {"conditions": "{{ kind == 'toggle' }}", "sequence": [
                    {"variables": {"playing": ROOM_PLAYING}},
                    {"if": "{{ playing | count > 0 }}",
                     "then": [{"set_conversation_response": NOW_PLAYING}],
                     "else": answered("script.play_random_music", {}, "Musik läuft.")}]},
                # "spiel etwas leiser" fits the mood sentence but means the volume.
                {"conditions": "{{ kind == 'volume' }}", "sequence": [
                    {"if": "{{ trigger.slots.mood | lower | trim == 'leiser' }}",
                     "then": [step("media_player.volume_down")], "else": [step("media_player.volume_up")]},
                    {"set_conversation_response": "Okay."}]},
                {"conditions": "{{ kind == 'radio' }}", "sequence": answered(
                    "script.jarvis_play_radio", {"name": "{{ said }}"}, "Das Radio geht gerade nicht.")},
                {"conditions": "{{ kind == 'mood' }}", "sequence": answered(
                    "script.jarvis_play_mood", {"query": "{{ said }}"}, "Das hat gerade nicht geklappt.")}],
             "default": answered("script.play_random_music", {}, "Musik läuft.")},
        ],
    }


def local_music_stop(house: House) -> dict:
    """Stopping the music in the words the built-in sentences miss. "Stopp", "Musik aus" or "Stopp die
    Musik" are left to Home Assistant's own pause, which already gets them right; a sentence here would
    win over it, since sentence triggers are tried first. A room named is this one's, as Home Assistant
    misses the Shows' music players, which often have no room."""
    phrases, words = room_words(house.areas)
    return {
        "alias": "Genbu - Local Music Stop",
        "description": 'Hält die Musik im Raum des Geräts an, das gefragt wurde, ohne das Sprachmodell: '
                       '"Stopp, stopp", "Hör auf", "Mach die Musik aus", oder in dem Raum, der genannt wird: '
                       '"Mach die Musik im Schlafzimmer aus". Einfaches "Stopp" oder "Musik aus" erledigt Home '
                       'Assistant selbst.',
        "mode": "single",
        "triggers": [{"trigger": "conversation", "command": [
            "(stopp|stop|halt|pause) (stopp|stop|halt|pause) [(stopp|stop|halt|pause)]",
            "[bitte] (hör|höre|hoer) [doch] [mal] [bitte] auf [mit der musik] [bitte]",
            "[bitte] aufhören [bitte]",
            "(mach|mache|macht) [doch] [mal] [bitte] [die] musik aus [bitte]"]},
            # GENBU-139: "Macht die Musik im Schlafzimmer aus" went to the model, which said it had.
            {"trigger": "conversation", "id": "room", "command": [
                "(mach|mache|macht) [doch] [mal] [bitte] [die] musik " + IN_ROOM + " aus [bitte]",
                "(mach|mache|macht) [doch] [mal] [bitte] " + IN_ROOM + " [die] musik aus [bitte]",
                "[bitte] (stopp|stoppe|pausier|pausiere) [bitte] die musik " + IN_ROOM + " [bitte]"]}],
        "actions": _stop_actions(house, words, phrases, "Pausiert.", STOP_NOTHING),
    }


# ------------------------------------------------------------------------------------- music in English

# The English sentences call the same scripts as the German ones, so a house with both languages has one
# set of scripts. A mood is passed on as the German word MOOD_PLAN knows ("upbeat" is "flott", "music by
# Queen" is "von queen"); a station name without the words around it. The scripts answer in German, so
# the English answer is phrased from what they found.
MOOD_EN = r"""{% set m = (trigger.slots.mood | default('')) | string | lower | regex_replace('[^a-z0-9 ]+', ' ') | trim %}{% set stop = ['some', 'any', 'a', 'little', 'bit', 'of', 'something', 'anything', 'more', 'my', 'the', 'music', 'songs', 'song', 'please', 'good', 'nice', 'new'] %}{% if said is search('\\b(music|songs|something|anything) by ') or m is search('^by ') %}von {{ m | regex_replace('^by ', '') }}{% elif m is search('\\b(upbeat|fast|danc|party|happy|cheerful|disco|energ)') %}flott{% elif m is search('\\b(calm|relax|chill|quiet|soft|slow|sleep|romantic|cosy|cozy)') %}ruhig{% elif m is search('\\b(loud|hard|heavy|angry|rock)|\\bmetal(?!lica)') %}laut{% elif m is search('\\b90|nineties') %}90er{% elif m is search('favou?rite') %}lieblings{% else %}{{ m.split() | reject('in', stop) | join(' ') }}{% endif %}"""

LOCAL_MUSIC_KIND_EN = r"""{% if trigger.id == 'toggle' %}toggle{% elif 'station' in trigger.slots or said is search('\\b(radio|station)\\b') %}radio{% elif query | trim %}mood{% else %}random{% endif %}"""

# "play the station rock antenne please" is "rock antenne".
STATION_EN = r"""{{ said | regex_replace('^(please )?(play|turn on|turn|put on|listen to)( the)?( station)?\\s*', '') | regex_replace('\\s*(on|please)$', '') }}"""

MUSIC_ANSWER_EN = (r"""{% if r is not defined or r is not mapping %}__FALLBACK__{% elif r.speaker is defined and r.speaker is mapping and r.speaker.asked_off is defined %}{{ r.speaker.asked_off.name }} can't be reached right now.{% elif r.message is defined and r.message == '""" + NO_PLAYER + r"""' %}There's no music speaker here. Pick one in Home Assistant under the Show's Music plays on.{% elif r.message is defined and r.message == '""" + PLAYER_OFFLINE + r"""' %}The speaker here can't be reached right now.{% elif kind == 'radio' %}{% if r.success | default(false) %}Playing {{ r.station }}.{% elif r.stations is defined and r.stations %}I don't know that station. I have {{ r.stations | join(', ') }}.{% elif r.stations is defined %}I don't have any radio stations.{% else %}__FALLBACK__{% endif %}{% elif not r.success | default(false) %}I can't find any music in the library right now.{% elif r.title and (r.artist or kind != 'mood') %}Playing {{ r.title }}{{ ' by ' ~ r.artist if r.artist }} and similar songs.{% elif r.title %}Playing the playlist {{ r.title }}.{% elif r.artist %}Playing music by {{ r.artist }}.{% else %}__FALLBACK__{% endif %}""")

NOW_PLAYING_EN = (r"""{% set t = state_attr(playing[0], 'media_title') or '' %}{% set ar = state_attr(playing[0], 'media_artist') or '' %}{% set t = (t | """
                  + SPOKEN + r""") or t %}{% if t %}Now playing {{ t }}{{ ' by ' ~ ar if ar }}.{% else %}Music is already playing here.{% endif %}""")


def local_music_en(house: House) -> dict:
    def answered(script: str, data: dict, fallback: str) -> list[dict]:
        return [*_routed(script, data),
                {"set_conversation_response": SKIP_NOTE_EN + MUSIC_ANSWER_EN.replace("__FALLBACK__", fallback)}]
    return {
        "alias": "Genbu - Local Music (English)",
        "description": 'Music without the language model: random, by mood ("play something upbeat", "play '
                       'music by ..."), or radio ("play Rock Antenne radio"), in the room of the device that '
                       'was asked. Says what plays now. All sentences are in one trigger, so two runs never '
                       'start at once. Just "the music" has its own trigger: when something already plays '
                       'here, it only says what.',
        "mode": "single",
        "triggers": [{"trigger": "conversation", "command": [
            "[please] play [(some|any)] music [please]",
            "play (something|anything) [please]",
            "put [some] music on",
            "put on [some] music",
            "(start|turn on) [(the|some)] music",
            "turn [the] music on",
            "play (something|anything) {mood}",
            "play [(some|a bit of|a little)] {mood} music",
            "play [some] (music|songs) by {mood}",
            "play something by {mood}",
            "(play|turn on|put on) [the] radio",
            "turn the radio on",
            "play {station} radio",
            "play {station} radio {rest}",
            "play radio {station}",
            "play [the] station {station}",
            "listen to {station} radio",
            "listen to radio {station}",
            "listen to [the] radio"]},
            {"trigger": "conversation", "id": "toggle", "command": ["[please] [the] music [please]"]}],
        "actions": [
            {"variables": {"said": "{{ trigger.sentence | lower | regex_replace('[^a-z0-9 ]+', ' ') "
                                   "| regex_replace(' +', ' ') | trim }}",
                           "query": MOOD_EN, "kind": LOCAL_MUSIC_KIND_EN,
                           "room_player": ROOM_PLAYER, "players": house.players,
                           "target_player": LOCAL_TARGET, "route": {}}},
            {"choose": [
                {"conditions": "{{ kind == 'toggle' }}", "sequence": [
                    {"variables": {"playing": ROOM_PLAYING}},
                    {"if": "{{ playing | count > 0 }}",
                     "then": [{"set_conversation_response": NOW_PLAYING_EN}],
                     "else": answered("script.play_random_music", {}, "Music's on.")}]},
                {"conditions": "{{ kind == 'radio' }}", "sequence": answered(
                    "script.jarvis_play_radio", {"name": STATION_EN}, "The radio isn't working right now.")},
                {"conditions": "{{ kind == 'mood' }}", "sequence": answered(
                    "script.jarvis_play_mood", {"query": "{{ query }}"}, "That didn't work right now.")}],
             "default": answered("script.play_random_music", {}, "Music's on.")},
        ],
    }


def local_music_stop_en(house: House) -> dict:
    """Stopping the music in the words the built-in English sentences miss. "Stop" and "pause" are Home
    Assistant's own; "stop stop" is German as well, so it is only here in a house without German."""
    command = ["[please] (turn|switch) (off the music|the music off) [please]",
               "[please] stop it [please]"]
    if not house.german:
        command.append("(stop|pause) (stop|pause) [(stop|pause)]")
    phrases, words = room_words(house.areas, EN_ROOMS)
    return {
        "alias": "Genbu - Local Music Stop (English)",
        "description": 'Pauses the music in the room of the device that was asked, without the language model: '
                       '"turn off the music", "stop it", or in the room named: "turn off the music in the bedroom". '
                       'Plain "stop" is left to Home Assistant itself.',
        "mode": "single",
        "triggers": [{"trigger": "conversation", "command": command},
                     {"trigger": "conversation", "id": "room", "command": [
                         "[please] (turn|switch) (off the music|the music off) (in|on) the {raum} [please]",
                         "[please] (stop|pause) the music (in|on) the {raum} [please]"]}],
        "actions": _stop_actions(house, words, phrases, "Paused.", STOP_NOTHING_EN),
    }


# ------------------------------------------------------------------------------------------ per Show

def show_speakers(house: House, satellite: str) -> tuple[str, list[str]]:
    """The Show's room and the speakers turn-room-down.yaml turns down there right now: the room's media players that are there, less
    any on a device with a voice assistant (the Shows turn their own down). A speaker that both Music
    Assistant and its own integration have an entity for is taken through its own one."""
    area = house.entity_area.get(satellite, "")
    if not area:
        return "", []
    voice_devices = {d for e, d in house.entity_device.items() if e.startswith("assist_satellite.")}
    here = sorted(e for e, a in house.entity_area.items()
                  if a == area and e.startswith("media_player.")
                  and house.states.get(e) not in (None, "unavailable")
                  and house.entity_device.get(e) not in voice_devices)
    native = [e for e in here if house.entity_platform.get(e) != "music_assistant"]
    return area, native or here


def turn_room_down(show: str, satellite: str, speakers: list[str], near_miss: str | None) -> dict:
    inputs: dict[str, Any] = {"satellite": satellite, "speakers": speakers, "part": 10, "quiet": 3}
    if near_miss:
        inputs["near_miss"] = near_miss
    return {
        "alias": f"{show}: turn the room down while it listens",
        "description": f"Blueprint {BLUEPRINT_FOLDER}/{TURN_ROOM_DOWN}.",
        "use_blueprint": {"path": f"{BLUEPRINT_FOLDER}/{TURN_ROOM_DOWN}", "input": inputs},
    }


# ------------------------------------------------------------------------------------------ setting up

Keep = Callable[[str, Any], str]


def _ours(name: str, source: str | None) -> bool:
    found = _OUR_BLUEPRINT.match(source or "")
    return bool(found) and found["name"] == name


@dataclass
class _Run:
    ha: HomeAssistant
    choose: Chooser | None
    keep: Keep | None
    progress: Callable[[str], None]

    differ: list[tuple[str, str, dict, dict]] = field(default_factory=list)
    items: list[tuple[str, str]] = field(default_factory=list)   # every item put, for the labels
    made: list[tuple[str, str]] = field(default_factory=list)    # the ones this run made or replaced

    def put(self, kind: str, item: str, config: dict) -> None:
        """Makes the automation or script item. One that is there and differs waits for settle()."""
        path = f"/api/config/{kind}/config/{item}"
        self.items.append((kind, item))
        try:
            old = self.ha.request("GET", path)
        except NotFound:
            old = None
        if isinstance(old, dict):
            # The name it shows under is the house's: one renamed by hand, or still called what an older
            # installer called it (Jarvis - ...), is the same item (GENBU-110).
            if {k: v for k, v in old.items() if k not in ("id", "alias")} == \
                    {k: v for k, v in config.items() if k != "alias"}:
                self.progress(f"{kind} {item} is already there")
            else:
                self.differ.append((kind, item, config, old))
            return
        self.ha.request("POST", path, config)
        self.made.append((kind, item))
        self.progress(f"{kind} {item} made")

    def settle(self) -> None:
        """Asks about the ones that differ from the installer's: once for all of them, or one by one."""
        if not self.differ:
            return
        each = len(self.differ) == 1
        if not each:
            listed = ", ".join(f"{kind} {item}" for kind, item, _, _ in self.differ)
            question = (f"{len(self.differ)} of the installer's automations and scripts differ from the ones "
                        f"Home Assistant has ({listed}). Replace them? The old ones are saved first.")
            picked = self.choose(question, "none", ["none", "all", "each"],
                                 {"none": "no, keep all of mine", "all": "yes, replace all of them",
                                  "each": "ask about each one"}) if self.choose else None
            if picked == "each":
                each = True
            elif picked != "all":
                for kind, item, _, _ in self.differ:
                    self.progress(f"{kind} {item} differs from the installer's and was kept")
                return
        for kind, item, config, old in self.differ:
            if each:
                question = (f"Home Assistant has {'an' if kind[0] in 'aeiou' else 'a'} {kind} {item} that "
                            "differs from the installer's. Replace it? Its old configuration is saved first.")
                picked = self.choose(question, "no", ["no", "yes"],
                                     {"no": "no, keep mine", "yes": "yes, replace it"}) if self.choose else None
                if picked != "yes":
                    self.progress(f"{kind} {item} differs from the installer's and was kept")
                    continue
            if self.keep is None:
                self.progress(f"{kind} {item} kept: there is nowhere to save the old one")
                continue
            saved = self.keep(f"{kind}-{item}", old)
            self.ha.request("POST", f"/api/config/{kind}/config/{item}", config)
            self.made.append((kind, item))
            self.progress(f"{kind} {item} replaced; the old one is in {saved}")
        self.differ.clear()

    def blueprint(self, name: str) -> bool:
        """Saves the repo's blueprint into Home Assistant, over the one there only when it is ours."""
        path = f"{BLUEPRINT_FOLDER}/{name}"
        listed = self.ha.ws({"type": "blueprint/list", "domain": "automation"}) or {}
        there = listed.get(path)
        source = BLUEPRINT_SOURCE + name
        if there is not None and not _ours(name, (there.get("metadata") or {}).get("source_url")):
            self.progress(f"blueprint {path} is there and is not this installer's; left alone")
            return True
        text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")
        self.ha.ws({"type": "blueprint/save", "domain": "automation", "path": path, "yaml": text,
                    "source_url": source, "allow_override": there is not None})
        self.progress(f"blueprint {path} {'brought up to date' if there is not None else 'saved'}")
        return True


def duck_existing(ha: HomeAssistant, satellite: str) -> tuple[str, dict] | None:
    """An automation from turn-room-down for this satellite that is there already, whatever its id."""
    try:
        related = ha.ws({"type": "search/related", "item_type": "entity", "item_id": satellite}) or {}
    except HomeAssistantError:
        return None
    for automation in related.get("automation") or []:
        state = ha.request("GET", f"/api/states/{automation}") or {}
        item = str((state.get("attributes") or {}).get("id") or "")
        if not _ID_RE.fullmatch(item):
            continue
        try:
            config = ha.request("GET", f"/api/config/automation/config/{item}")
        except NotFound:
            continue
        bp = (config or {}).get("use_blueprint") or {}
        if str(bp.get("path") or "").endswith(TURN_ROOM_DOWN) and (bp.get("input") or {}).get("satellite") == satellite:
            return item, config
    return None


def _per_show(run: _Run, house: House, show: str, entities: list[str]) -> None:
    satellites = sorted(e for e in entities if e.startswith("assist_satellite."))
    if not satellites:
        run.progress("the Show has no voice assistant in Home Assistant yet, so no turn-the-room-down "
                     f"automation. Once it has one, run {_rerun(show)}")
        return
    satellite = satellites[0]
    near_miss = next((e for e in sorted(entities) if e.startswith("event.") and e.endswith("_wake_word_near_miss")),
                     None)
    item = f"{OURS}{service_prefix(show)}"
    found = duck_existing(run.ha, satellite)
    if found and found[0] != item:
        # Someone made their own from the blueprint; it stays theirs.
        mine, config = found
        inputs = config["use_blueprint"].setdefault("input", {})
        if near_miss and not inputs.get("near_miss"):
            inputs["near_miss"] = near_miss
            run.ha.request("POST", f"/api/config/automation/config/{mine}", config)
            run.progress(f"automation {mine}: the Show's near misses now turn the room down too")
        else:
            run.progress(f"automation {mine} turns the room down for this Show already; left as it is")
        return
    # No speakers picked: it turns down whatever room the Show is in when it listens, so a Show
    # moved to another room needs nothing set up again. An older one of ours with fixed speakers
    # is asked about at the end.
    run.put("automation", item, turn_room_down(show, satellite, [], near_miss))
    area, speakers = show_speakers(house, satellite)
    if not area:
        run.progress("the Show is in no room in Home Assistant yet; the room goes quiet once it has one")
    elif not speakers:
        run.progress(f"no speaker besides the Show in {house.areas.get(area, area)} yet; it turns that "
                     "one down once it is there")


def _rerun(show: str) -> str:
    """The command that runs this part again for the Show, once Home Assistant has what was missing."""
    return f"python3 tools/jarvis-show.py home-assistant --name {shlex.quote(show)}"


def set_up(ha: HomeAssistant, show: str, entities: list[str], *, choose: Chooser | None = None,
           keep: Keep | None = None, progress: Callable[[str], None] = print, node: str = "",
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           genres: dict[str, str] | None = None) -> None:
    """Sets up the pieces this house can have, and this Show's turn-the-room-down automation. The ones
    that are there already and differ are asked about at the end, together. Then all of them get the
    installer's label, and the Show's own automation the Show's label too (node, its ESPHome name)."""
    run = _Run(ha, choose, keep, progress)
    _set_up(run, show, entities, genres or {})
    run.settle()
    own = [(k, i) for k, i in run.items if i.startswith(OURS)]
    start = clock()
    labels.mark(ha, progress, node=node or node_slug(show), show_items=own,
                house_items=[x for x in run.items if x not in own], sleep=sleep, clock=clock)
    if ("script", CLIMATE) in run.made:
        # The labels waited for Home Assistant's reload already; this waits out the rest of those 10 s.
        expose(ha, progress, CLIMATE, sleep=sleep, clock=clock, wait=max(0.0, start + 10 - clock()))
    for script in MUSIC_TOOLS:
        if ("script", script) in run.items:
            expose(ha, progress, script, sleep=sleep, clock=clock, wait=max(0.0, start + 10 - clock()))


def expose(ha: HomeAssistant, progress: Callable[[str], None], script: str, *,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           wait: float = 10) -> None:
    """Exposes the script to Assist, so the model has it as a tool. Home Assistant leaves new scripts
    unexposed. Only done when the run made it, so one taken away from Assist by hand stays that way."""
    until = clock() + wait
    while True:
        try:
            found = next((e["entity_id"] for e in ha.ws({"type": "config/entity_registry/list"}) or []
                          if e.get("platform") == "script" and e.get("unique_id") == script), None)
            if found:
                ha.ws({"type": "homeassistant/expose_entity", "assistants": ["conversation"],
                       "entity_ids": [found], "should_expose": True})
                progress(f"script {script} is exposed to Assist")
                return
        except HomeAssistantError as exc:
            progress(f"WARN: Home Assistant did not expose script {script} to Assist ({exc}). Expose it "
                     "under Settings, Voice assistants, Expose, or the model can't use it")
            return
        if clock() >= until:
            progress(f"WARN: Home Assistant had not loaded script {script} yet, so it isn't exposed to "
                     "Assist. Running the installer again does that")
            return
        sleep(1)


def _set_up(run: _Run, show: str, entities: list[str], genres: dict[str, str]) -> None:
    ha, progress = run.ha, run.progress
    house = look(ha)
    house.genres = genres
    run.blueprint(TURN_ROOM_DOWN)
    _per_show(run, house, show, entities)
    if not (house.german or house.english):
        progress("no Assist pipeline speaks German or English, so the local answers and music sentences "
                 "were left out")
        return
    if house.german:
        run.blueprint(VOLUME)
        run.put("automation", "jarvis_local_volume", volume(house))
        run.put("automation", "jarvis_local_where_are_we", where_are_we(house))
        if house.agent:
            run.put("automation", "jarvis_local_greeting", greeting(house))
        run.put("automation", "jarvis_local_room_temperature", room_temperature(house))
        run.put("automation", "jarvis_local_room_humidity", room_humidity(house))
        run.put("automation", "jarvis_local_wecker", wecker())
        run.put("automation", "jarvis_local_genbu_update", genbu_update())
        if house.weather:
            run.put("automation", "jarvis_local_forecast", forecast(house))
    if house.english:
        run.blueprint(VOLUME_EN)
        run.put("automation", "jarvis_local_volume_en", volume_en(house))
        run.put("automation", "jarvis_local_where_are_we_en", where_are_we_en(house))
        if house.agent:
            run.put("automation", "jarvis_local_greeting_en", greeting_en(house))
        run.put("automation", "jarvis_local_room_temperature_en", room_temperature_en(house))
        run.put("automation", "jarvis_local_room_humidity_en", room_humidity_en(house))
        run.put("automation", "jarvis_local_alarm_en", alarm_en())
        run.put("automation", "jarvis_local_genbu_update_en", genbu_update_en())
        if house.weather:
            run.put("automation", "jarvis_local_forecast_en", forecast_en(house))
    # The model's room-climate tool answers in German words; the model says it in the asker's language.
    run.put("script", CLIMATE, room_climate(house))
    if not house.agent:
        progress("the assistant's agent is Home Assistant's own, so greetings stay with it")
    if not house.weather:
        progress("no weather entity, so no local forecast")
    if not house.music_entry:
        progress("no Music Assistant in Home Assistant, so no music sentences")
        return
    if not house.players:
        progress("WARN: none of Music Assistant's players is in a room; music goes to the asking Show's "
                 "room only, and the model's music tool has no usual player")
    run.put("script", "jarvis_music_start", music_start())
    run.put("script", "jarvis_play_radio", play_radio(house))
    run.put("script", "jarvis_play_mood", play_mood(house))
    run.put("script", "play_random_music", play_random(house))
    if house.german:
        run.put("automation", "jarvis_local_random_music", local_music(house))
        run.put("automation", "jarvis_local_music_stop", local_music_stop(house))
    if house.english:
        run.put("automation", "jarvis_local_random_music_en", local_music_en(house))
        run.put("automation", "jarvis_local_music_stop_en", local_music_stop_en(house))
