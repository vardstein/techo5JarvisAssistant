import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from jarvis_crown import voice_kit as kit  # noqa: E402
from jarvis_crown.home_assistant import NotFound  # noqa: E402

MA_ENTRY = "01ABCDEFGHJKMNPQRSTVWXYZ00"
SOURCE = kit.BLUEPRINT_SOURCE


def entity(eid, area=None, device=None, platform="test", **more):
    return {"entity_id": eid, "area_id": area, "device_id": device, "platform": platform, **more}


class FakeHA:
    """A small house: a living room with a Show, a speaker and a Music Assistant player, a kitchen with a
    temperature and a humidity sensor beside a thermostat, a room the sentences do not know, and the
    weather."""

    def __init__(self, language="de"):
        self.language = language
        self.agent = "conversation.ollama"
        self.areas = [{"area_id": "living_room", "name": "Living Room"}, {"area_id": "kitchen", "name": "Küche"},
                      {"area_id": "mamas", "name": "Mamas Zuhause"}]
        self.devices = [{"id": "dev-show", "area_id": "living_room"}, {"id": "dev-speaker", "area_id": "living_room"},
                        {"id": "dev-kitchen", "area_id": "kitchen"}, {"id": "dev-heat", "area_id": "kitchen"}]
        self.entities = [
            entity("assist_satellite.jarvis_show_5_assist_satellite", device="dev-show", platform="esphome"),
            entity("event.jarvis_show_5_wake_word_near_miss", device="dev-show", platform="esphome"),
            entity("media_player.jarvis_show_5_speaker", device="dev-show", platform="esphome"),
            entity("media_player.speaker", device="dev-speaker", platform="linkplay"),
            entity("media_player.living_ma", area="living_room", platform="music_assistant"),
            entity("media_player.nowhere_ma", platform="music_assistant"),
            entity("sensor.kitchen_temp", device="dev-kitchen"),
            entity("sensor.kitchen_humidity", device="dev-kitchen"),
            entity("sensor.heater_humidity", device="dev-heat"),
            entity("sensor.kitchen_board_temp", device="dev-kitchen", entity_category="diagnostic"),
            entity("sensor.heater_temp", device="dev-heat"),
            entity("climate.heater", device="dev-heat"),
            entity("sensor.hidden_temp", area="living_room", hidden_by="user"),
        ]
        temp = {"device_class": "temperature", "unit_of_measurement": "°C"}
        self.states = {e["entity_id"]: {"entity_id": e["entity_id"], "state": "1", "attributes": {}}
                       for e in self.entities}
        for e in ("sensor.kitchen_temp", "sensor.kitchen_board_temp", "sensor.heater_temp", "sensor.hidden_temp"):
            self.states[e]["attributes"] = dict(temp)
        for e in ("sensor.kitchen_humidity", "sensor.heater_humidity"):
            self.states[e]["attributes"] = {"device_class": "humidity", "unit_of_measurement": "%"}
        self.states["weather.forecast_home"] = {"entity_id": "weather.forecast_home", "state": "sunny",
                                                "attributes": {}}
        self.entries = [{"domain": "music_assistant", "state": "loaded", "entry_id": MA_ENTRY}]
        self.blueprints = {}
        self.configs = {}           # (kind, id) -> config
        self.related = {}           # entity -> automation entity ids
        self.posts = []
        self.ws_calls = []
        self.labels = []            # the label registry
        self.item_labels = {}       # automation/script entity id -> label ids
        self.loaded = True          # Home Assistant has loaded what was saved, so it is in the registry
        self.exposed = []           # entity ids exposed to Assist

    def ws(self, msg):
        self.ws_calls.append(msg)
        t = msg["type"]
        if t == "assist_pipeline/pipeline/list":
            return {"pipelines": [{"id": "p1", "name": "Genbu Assistant", "language": self.language,
                                   "conversation_engine": self.agent}], "preferred_pipeline": "p1"}
        if t == "config/area_registry/list":
            return self.areas
        if t == "config/device_registry/list":
            return self.devices
        if t == "config/entity_registry/list":
            items = [{"entity_id": f"{kind}.{item}", "platform": kind, "unique_id": item,
                      "labels": list(self.item_labels.get(f"{kind}.{item}", []))}
                     for kind, item in self.configs] if self.loaded else []
            return self.entities + items
        if t == "config/label_registry/list":
            return [dict(x) for x in self.labels]
        if t == "config/label_registry/create":
            label = {"label_id": msg["name"].lower().replace(" ", "_").replace(":", ""), **msg}
            self.labels.append(label)
            return label
        if t == "config/entity_registry/update":
            self.item_labels[msg["entity_id"]] = list(msg["labels"])
            return {}
        if t == "config_entries/get":
            return self.entries
        if t == "blueprint/list":
            return self.blueprints
        if t == "blueprint/save":
            if msg["path"] in self.blueprints and not msg.get("allow_override"):
                raise AssertionError("saved over a blueprint without allow_override")
            self.blueprints[msg["path"]] = {"metadata": {"source_url": msg.get("source_url")}}
            return {"overrides_existing": msg.get("allow_override", False)}
        if t == "homeassistant/expose_entity":
            assert msg["assistants"] == ["conversation"] and msg["should_expose"] is True
            self.exposed += msg["entity_ids"]
            return None
        if t == "search/related":
            return {"automation": self.related.get(msg["item_id"], [])}
        raise AssertionError(f"unexpected ws {t}")

    def request(self, method, path, body=None):
        if method == "GET" and path == "/api/states":
            return list(self.states.values())
        if method == "GET" and path.startswith("/api/states/"):
            return self.states.get(path.rsplit("/", 1)[1])
        for kind in ("automation", "script"):
            prefix = f"/api/config/{kind}/config/"
            if path.startswith(prefix):
                item = path[len(prefix):]
                if method == "GET":
                    if (kind, item) not in self.configs:
                        raise NotFound(f"GET {path}: HTTP 404")
                    return {"id": item, **self.configs[(kind, item)]} if kind == "automation" \
                        else dict(self.configs[(kind, item)])
                self.posts.append((kind, item))
                self.configs[(kind, item)] = json.loads(json.dumps(body))
                return {"result": "ok"}
        raise AssertionError(f"unexpected {method} {path}")


SHOW = ["assist_satellite.jarvis_show_5_assist_satellite", "event.jarvis_show_5_wake_word_near_miss",
        "media_player.jarvis_show_5_speaker", "select.jarvis_show_5_assistant"]


def run(fake, **kw):
    log = []
    kit.set_up(fake, "Jarvis Show 5", SHOW, progress=log.append, **kw)
    return log


class LookTests(unittest.TestCase):
    def test_the_house_is_read_as_it_is(self):
        house = kit.look(FakeHA())
        self.assertTrue(house.german)
        # The room's own sensors, not the thermostat's.
        self.assertEqual(house.temperatures, {"kitchen": "sensor.kitchen_temp"})
        self.assertEqual(house.humidities, {"kitchen": "sensor.kitchen_humidity"})
        self.assertEqual(house.weather, "weather.forecast_home")
        self.assertEqual(house.music_entry, MA_ENTRY)
        self.assertEqual(house.players, ["media_player.living_ma"])

    def test_the_assistants_agent_is_its_pipelines(self):
        self.assertEqual(kit.look(FakeHA()).agent, "conversation.ollama")
        listed = {"pipelines": [{"id": "a", "name": "Other", "conversation_engine": "conversation.other"},
                                {"id": "b", "name": "Mine", "conversation_engine": "conversation.mine"}],
                  "preferred_pipeline": "b"}
        self.assertEqual(kit.assistant_agent(listed), "conversation.mine")
        listed["pipelines"].append({"id": "c", "name": "Genbu Assistant", "conversation_engine": "conversation.genbu"})
        self.assertEqual(kit.assistant_agent(listed), "conversation.genbu")
        for own in ("conversation.home_assistant", "homeassistant", "x'}}{{", None):
            self.assertIsNone(kit.assistant_agent({"pipelines": [{"name": "Genbu Assistant", "conversation_engine": own}]}))

    def test_german_is_the_assistants_language(self):
        self.assertFalse(kit.look(FakeHA(language="en")).german)
        self.assertTrue(kit.look(FakeHA(language="de-CH")).german)

    def test_an_odd_music_assistant_entry_is_not_used(self):
        fake = FakeHA()
        fake.entries = [{"domain": "music_assistant", "state": "loaded", "entry_id": "x'}}{{"}]
        self.assertIsNone(kit.look(fake).music_entry)

    def test_rooms_get_their_german(self):
        phrases, words = kit.room_words({"living_room": "Living Room", "kitchen": "Küche", "x": "Mamas Zuhause",
                                         "office": "Arbeitszimmer"})
        self.assertEqual(phrases, {"living_room": "im Wohnzimmer", "kitchen": "in der Küche", "office": "im Büro"})
        self.assertEqual(words["wohnzimmer"], "living_room")
        self.assertEqual(words["kueche"], "kitchen")
        self.assertEqual(words["arbeitszimmer"], "office")

    def test_the_speakers_to_turn_down_are_the_rooms_own(self):
        house = kit.look(FakeHA())
        area, speakers = kit.show_speakers(house, SHOW[0])
        self.assertEqual(area, "living_room")
        # Not the Show's own speaker, and the speaker's own entity rather than Music Assistant's.
        self.assertEqual(speakers, ["media_player.speaker"])


class SetUpTests(unittest.TestCase):
    def test_a_german_house_gets_everything(self):
        fake = FakeHA()
        log = run(fake)
        self.assertEqual(set(fake.blueprints), {"techo5/turn-room-down.yaml", "techo5/volume-where-the-music-is.yaml"})
        self.assertEqual(sorted(fake.configs), [
            ("automation", "jarvis_local_forecast"), ("automation", "jarvis_local_genbu_update"),
            ("automation", "jarvis_local_greeting"),
            ("automation", "jarvis_local_music_stop"),
            ("automation", "jarvis_local_random_music"), ("automation", "jarvis_local_room_humidity"),
            ("automation", "jarvis_local_room_temperature"), ("automation", "jarvis_local_volume"),
            ("automation", "jarvis_local_wecker"), ("automation", "jarvis_local_where_are_we"), ("automation", "techo5_turn_room_down_jarvis_show_5"),
            ("script", "jarvis_music_start"), ("script", "jarvis_play_mood"), ("script", "jarvis_play_radio"),
            ("script", "jarvis_room_climate"), ("script", "play_random_music")])
        duck = fake.configs[("automation", "techo5_turn_room_down_jarvis_show_5")]["use_blueprint"]["input"]
        # No speakers picked: it turns down whatever room the Show is in when it listens.
        self.assertEqual(duck, {"satellite": SHOW[0], "speakers": [], "part": 10, "quiet": 3,
                                "near_miss": "event.jarvis_show_5_wake_word_near_miss"})
        volume = fake.configs[("automation", "jarvis_local_volume")]["use_blueprint"]["input"]["rooms"]
        self.assertEqual(volume["wohnzimmer"], "living_room")
        radio = json.dumps(fake.configs[("script", "jarvis_play_radio")])
        self.assertIn(MA_ENTRY, radio)
        self.assertIn("media_player.living_ma", radio)
        self.assertNotIn("WARN", "\n".join(log))
        # The model's room-climate tool is exposed to Assist, the others are left to the house.
        self.assertEqual(fake.exposed, ["script.jarvis_room_climate"])
        climate = json.dumps(fake.configs[("script", "jarvis_room_climate")], ensure_ascii=False)
        self.assertIn('\\"kitchen\\": \\"sensor.kitchen_humidity\\"', climate)
        self.assertNotIn("sensor.heater", climate)
        self.assertIn("current_temperature", climate)
        self.assertNotIn("'temperature'", climate)   # the setpoint attribute

    def test_music_starts_before_the_mix_of_similar_songs_is_made(self):
        fake = FakeHA()
        run(fake)
        steps = fake.configs[("script", "jarvis_music_start")]["sequence"]
        plays = [s for s in steps if s.get("action") == "music_assistant.play_media"]
        # The first play has no radio mode, which is what makes Music Assistant wait.
        self.assertEqual(len(plays), 1)
        self.assertNotIn("radio_mode", plays[0]["data"])
        radio = steps[steps.index(plays[0]) + 1]
        self.assertIn("radio", radio["if"][0]["value_template"])
        then = radio["then"][0]
        self.assertEqual(then["data"]["enqueue"], "add")
        self.assertIs(then["data"]["radio_mode"], True)
        self.assertTrue(then["continue_on_error"])
        # The other seeds of the mix go in the same call, so Music Assistant builds it once.
        self.assertEqual(then["data"]["media_id"], "{{ [media_id] + (mix | default([], true) | list) }}")

    def test_random_music_mixes_three_songs_and_a_genre_for_the_time_of_day(self):
        fake = FakeHA()
        run(fake, genres={"library://genre/29": "Jazz", "library://genre/12": "Classical",
                          "library://genre/39": "Pop", "library://genre/7": "Polka"})
        steps = fake.configs[("script", "play_random_music")]["sequence"]
        library = next(s for s in steps if s.get("action") == "music_assistant.get_library")
        self.assertEqual(library["data"]["limit"], kit.RANDOM_SEEDS)
        hours = next(s["variables"]["genre_hours"] for s in steps if "genre_hours" in s.get("variables", {}))
        self.assertEqual(hours["night"], [{"uri": "library://genre/12", "name": "Klassik"},
                                          {"uri": "library://genre/29", "name": "Jazz"}])
        self.assertEqual(hours["day"], [{"uri": "library://genre/39", "name": "Pop"}])
        self.assertNotIn("Polka", json.dumps(hours))
        start = next(s for s in steps if s.get("action") == "script.turn_on")
        mix = start["data"]["variables"]["mix"]
        self.assertIn("random_track['items'][1:]", mix)
        self.assertIn("genre.uri", mix)
        answer = json.dumps(steps, ensure_ascii=False)
        self.assertIn("etwas ' ~ genre.name", answer)

    def test_random_music_without_genres_mixes_the_songs_alone(self):
        fake = FakeHA()
        run(fake)
        steps = fake.configs[("script", "play_random_music")]["sequence"]
        self.assertIn({"variables": {"genre": {}}}, steps)
        self.assertNotIn("genre_hours", json.dumps(steps))

    def test_genres_are_known_by_other_spellings(self):
        hours = kit.genre_hours({"a": "Hip-Hop", "b": "RnB", "c": "Elektro", "d": "Klassik"})
        self.assertIn({"uri": "a", "name": "Hip-Hop"}, hours["day"])
        self.assertIn({"uri": "b", "name": "R und B"}, hours["evening"])
        self.assertIn({"uri": "c", "name": "Elektro"}, hours["day"])
        self.assertIn({"uri": "d", "name": "Klassik"}, hours["night"])
        self.assertEqual(kit.genre_hours({})["night"], [])

    def test_music_waits_for_the_answer_to_be_spoken(self):
        fake = FakeHA()
        run(fake)
        steps = fake.configs[("script", "jarvis_music_start")]["sequence"]
        # Before anything is played: while a voice assistant near the player is busy, wait it out, look
        # again a moment later, and never hang on it.
        first = steps[0]
        self.assertIn("responding", first["if"][0]["value_template"])
        waits = [s for s in first["then"] if "wait_template" in s]
        self.assertEqual(len(waits), 2)
        for wait in waits:
            self.assertIn("processing", wait["wait_template"])
            self.assertTrue(wait["continue_on_timeout"])
        self.assertTrue(any("delay" in s for s in first["then"]))

    def test_greetings_go_to_the_assistants_model(self):
        fake = FakeHA()
        run(fake)
        greeting = fake.configs[("automation", "jarvis_local_greeting")]
        call = greeting["actions"][0]
        self.assertEqual(call["action"], "conversation.process")
        self.assertEqual(call["data"]["agent_id"], "conversation.ollama")
        self.assertIn("trigger.sentence", call["data"]["text"])
        self.assertIn("said.response.speech.plain.speech", greeting["actions"][1]["set_conversation_response"])

    def test_greetings_stay_with_home_assistant_when_it_is_the_agent(self):
        fake = FakeHA()
        fake.agent = "conversation.home_assistant"
        log = run(fake)
        self.assertNotIn(("automation", "jarvis_local_greeting"), fake.configs)
        self.assertIn("the assistant's agent is Home Assistant's own, so greetings stay with it", log)

    def test_a_second_run_changes_nothing(self):
        fake = FakeHA()
        run(fake)
        fake.posts.clear()
        log = run(fake)
        self.assertEqual(fake.posts, [])
        self.assertEqual(sum("is already there" in line for line in log), 16)
        self.assertEqual(fake.exposed, ["script.jarvis_room_climate"])

    def test_one_taken_from_assist_by_hand_stays_that_way(self):
        fake = FakeHA()
        run(fake)
        fake.exposed.clear()
        run(fake)
        self.assertEqual(fake.exposed, [])

    def test_a_tool_home_assistant_has_not_loaded_is_said_and_nothing_hangs(self):
        fake = FakeHA()
        fake.loaded = False
        now = [0.0]
        log = run(fake, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
        self.assertEqual(fake.exposed, [])
        self.assertTrue(any("jarvis_room_climate yet, so it isn't exposed to Assist" in line for line in log), log)

    def test_a_name_of_its_own_is_not_a_difference(self):
        fake = FakeHA()
        run(fake)
        fake.configs[("automation", "jarvis_local_where_are_we")]["alias"] = "Jarvis - Where are we"
        fake.posts.clear()
        log = run(fake)
        self.assertEqual(fake.posts, [])
        self.assertEqual(fake.configs[("automation", "jarvis_local_where_are_we")]["alias"], "Jarvis - Where are we")
        self.assertIn("automation jarvis_local_where_are_we is already there", log)

    def test_nothing_it_makes_carries_an_assistant_name(self):
        fake = FakeHA()
        run(fake)
        said = json.dumps([c.get("alias", "") + c.get("description", "") for c in fake.configs.values()],
                          ensure_ascii=False).replace("Jarvis Show 5", "the Show")  # the test Show's own name
        self.assertNotIn("Jarvis", said)

    def test_one_that_differs_is_kept_unless_the_person_says_so(self):
        fake = FakeHA()
        mine = {"alias": "mine", "triggers": [], "actions": []}
        fake.configs[("automation", "jarvis_local_where_are_we")] = dict(mine)
        log = run(fake)
        self.assertEqual(fake.configs[("automation", "jarvis_local_where_are_we")], mine)
        self.assertIn("automation jarvis_local_where_are_we differs from the installer's and was kept", log)

        kept = []
        asked = []

        def choose(label, current, options, names):
            asked.append(label)
            return "yes"
        log = run(fake, choose=choose, keep=lambda what, config: kept.append((what, config)) or "/b/x.json")
        self.assertEqual(len(asked), 1)
        self.assertIn("jarvis_local_where_are_we", asked[0])
        self.assertEqual(kept, [("automation-jarvis_local_where_are_we", {"id": "jarvis_local_where_are_we", **mine})])
        self.assertEqual(fake.configs[("automation", "jarvis_local_where_are_we")]["alias"], "Genbu - Where are we")
        self.assertIn("automation jarvis_local_where_are_we replaced; the old one is in /b/x.json", log)

    def test_several_that_differ_are_asked_about_once(self):
        mine = {"alias": "mine", "triggers": [], "actions": []}
        for answer, replaced, questions in (("all", 2, 1), ("none", 0, 1), ("each", 1, 3)):
            fake = FakeHA()
            fake.configs[("automation", "jarvis_local_where_are_we")] = dict(mine)
            fake.configs[("script", "jarvis_play_radio")] = {"alias": "mine", "sequence": []}
            asked, kept = [], []

            def choose(label, current, options, names):
                asked.append(label)
                if label.startswith("2 of"):
                    self.assertEqual(options, ["none", "all", "each"])
                    return answer
                return "yes" if "an automation" in label else "no"
            log = run(fake, choose=choose, keep=lambda what, config: kept.append(what) or "/b/x.json")
            self.assertEqual(len(asked), questions, answer)
            self.assertIn("automation jarvis_local_where_are_we, script jarvis_play_radio", asked[0])
            self.assertEqual(len(kept), replaced, answer)
            self.assertEqual(sum("replaced; the old one is in" in line for line in log), replaced)
            if answer == "each":
                self.assertIn("a script jarvis_play_radio", asked[2])
                self.assertEqual(fake.configs[("script", "jarvis_play_radio")]["alias"], "mine")

    def test_nothing_is_replaced_when_it_cannot_be_saved_first(self):
        fake = FakeHA()
        fake.configs[("script", "jarvis_play_radio")] = {"alias": "mine", "sequence": []}
        run(fake, choose=lambda *_: "yes")
        self.assertEqual(fake.configs[("script", "jarvis_play_radio")]["alias"], "mine")

    def test_a_hand_made_turn_down_is_found_by_its_satellite(self):
        fake = FakeHA()
        fake.configs[("automation", "by_hand")] = {"alias": "by hand", "use_blueprint": {
            "path": "techo5/turn-room-down.yaml", "input": {"satellite": SHOW[0], "speakers": ["media_player.x"]}}}
        fake.related[SHOW[0]] = ["automation.by_hand"]
        fake.states["automation.by_hand"] = {"state": "on", "attributes": {"id": "by_hand"}}
        log = run(fake)
        self.assertNotIn(("automation", "techo5_turn_room_down_jarvis_show_5"), fake.configs)
        self.assertEqual(fake.configs[("automation", "by_hand")]["use_blueprint"]["input"],
                         {"satellite": SHOW[0], "speakers": ["media_player.x"],
                          "near_miss": "event.jarvis_show_5_wake_word_near_miss"})
        self.assertIn("automation by_hand: the Show's near misses now turn the room down too", log)

    def test_someone_elses_blueprint_is_left_alone(self):
        fake = FakeHA()
        fake.blueprints["techo5/turn-room-down.yaml"] = {"metadata": {"source_url": "https://example.org/x.yaml"}}
        log = run(fake)
        self.assertEqual(fake.blueprints["techo5/turn-room-down.yaml"]["metadata"]["source_url"],
                         "https://example.org/x.yaml")
        self.assertIn("blueprint techo5/turn-room-down.yaml is there and is not this installer's; left alone", log)

    def test_our_blueprint_is_brought_up_to_date(self):
        fake = FakeHA()
        fake.blueprints["techo5/turn-room-down.yaml"] = {"metadata": {"source_url": SOURCE + "turn-room-down.yaml"}}
        log = run(fake)
        self.assertIn("blueprint techo5/turn-room-down.yaml brought up to date", log)

    def test_our_blueprint_from_an_older_branch_name_is_still_ours(self):
        fake = FakeHA()
        old = SOURCE.replace("/genbuShowCrown/", "/jarvis-crown-v1/") + "turn-room-down.yaml"
        fake.blueprints["techo5/turn-room-down.yaml"] = {"metadata": {"source_url": old}}
        log = run(fake)
        self.assertIn("blueprint techo5/turn-room-down.yaml brought up to date", log)
        self.assertEqual(fake.blueprints["techo5/turn-room-down.yaml"]["metadata"]["source_url"],
                         SOURCE + "turn-room-down.yaml")

    def test_another_of_our_blueprints_at_that_path_is_not_ours_to_overwrite(self):
        fake = FakeHA()
        other = SOURCE + "volume-where-the-music-is.yaml"
        fake.blueprints["techo5/turn-room-down.yaml"] = {"metadata": {"source_url": other}}
        log = run(fake)
        self.assertIn("blueprint techo5/turn-room-down.yaml is there and is not this installer's; left alone", log)

    def test_a_house_without_german_gets_only_the_turn_down(self):
        fake = FakeHA(language="en")
        log = run(fake)
        self.assertEqual(list(fake.blueprints), ["techo5/turn-room-down.yaml"])
        self.assertEqual(sorted(fake.configs), [("automation", "techo5_turn_room_down_jarvis_show_5")])
        self.assertIn("no Assist pipeline speaks German, so the local answers and music sentences were left out", log)

    def test_without_music_assistant_or_weather_those_are_left_out(self):
        fake = FakeHA()
        fake.entries = []
        del fake.states["weather.forecast_home"]
        log = run(fake)
        made = {item for _, item in fake.configs}
        self.assertNotIn("jarvis_local_forecast", made)
        self.assertNotIn("play_random_music", made)
        self.assertNotIn("jarvis_local_music_stop", made)
        for item in ("jarvis_local_room_temperature", "jarvis_local_room_humidity"):
            self.assertNotIn("draußen", json.dumps(fake.configs[("automation", item)], ensure_ascii=False))
        self.assertIn("no weather entity, so no local forecast", log)
        self.assertIn("no Music Assistant in Home Assistant, so no music sentences", log)

    def test_an_older_turn_down_of_ours_with_fixed_speakers_is_asked_about(self):
        fake = FakeHA()
        item = ("automation", "techo5_turn_room_down_jarvis_show_5")
        old = {"alias": "old", "use_blueprint": {"path": "techo5/turn-room-down.yaml",
                                                  "input": {"satellite": SHOW[0], "speakers": ["media_player.x"]}}}
        fake.configs[item] = old
        fake.related[SHOW[0]] = ["automation.techo5_turn_room_down_jarvis_show_5"]
        fake.states["automation.techo5_turn_room_down_jarvis_show_5"] = {
            "state": "on", "attributes": {"id": "techo5_turn_room_down_jarvis_show_5"}}
        asked = []
        run(fake, choose=lambda label, *_: asked.append(label) or "yes",
            keep=lambda what, config: "/b/x.json")
        self.assertTrue(any("techo5_turn_room_down_jarvis_show_5" in q for q in asked))
        self.assertEqual(fake.configs[item]["use_blueprint"]["input"]["speakers"], [])

    def test_a_show_in_no_room_still_gets_the_turn_down(self):
        fake = FakeHA()
        fake.devices[0]["area_id"] = None
        log = run(fake)
        self.assertIn(("automation", "techo5_turn_room_down_jarvis_show_5"), fake.configs)
        self.assertIn("the Show is in no room in Home Assistant yet; the room goes quiet once it has one", log)

    def test_a_room_without_another_speaker_still_gets_the_turn_down(self):
        fake = FakeHA()
        fake.devices[0]["area_id"] = "kitchen"
        log = run(fake)
        self.assertIn(("automation", "techo5_turn_room_down_jarvis_show_5"), fake.configs)
        self.assertIn("no speaker besides the Show in Küche yet; it turns that one down once it is there", log)


class LabelTests(unittest.TestCase):
    """Everything it makes gets "Genbu Show installed"; the Show's own automation also the Show's label."""

    def labels_of(self, fake, entity_id):
        names = {x["label_id"]: x["name"] for x in fake.labels}
        return sorted(names[i] for i in fake.item_labels.get(entity_id, []))

    def test_the_show_s_automation_gets_both_and_the_house_s_only_the_common_one(self):
        fake = FakeHA()
        log = run(fake, node="jarvis-show-5")
        self.assertEqual(sorted(x["name"] for x in fake.labels), ["Genbu Show installed", "Genbu Show: jarvis-show-5"])
        self.assertEqual(self.labels_of(fake, "automation.techo5_turn_room_down_jarvis_show_5"),
                         ["Genbu Show installed", "Genbu Show: jarvis-show-5"])
        for kind, item in fake.configs:
            if not item.startswith("techo5_"):
                self.assertEqual(self.labels_of(fake, f"{kind}.{item}"), ["Genbu Show installed"], item)
        self.assertNotIn("WARN", "\n".join(log))

    def test_a_second_run_makes_no_new_labels_and_changes_none(self):
        fake = FakeHA()
        run(fake, node="jarvis-show-5")
        fake.ws_calls.clear()
        run(fake, node="jarvis-show-5")
        kinds = {m["type"] for m in fake.ws_calls}
        self.assertNotIn("config/label_registry/create", kinds)
        self.assertNotIn("config/entity_registry/update", kinds)

    def test_labels_put_on_by_hand_stay(self):
        fake = FakeHA()
        fake.labels.append({"label_id": "kitchen", "name": "Kitchen"})
        fake.configs[("automation", "jarvis_local_wecker")] = json.loads(json.dumps(kit.wecker()))
        fake.item_labels["automation.jarvis_local_wecker"] = ["kitchen"]
        run(fake, node="jarvis-show-5")
        self.assertEqual(self.labels_of(fake, "automation.jarvis_local_wecker"), ["Genbu Show installed", "Kitchen"])

    def test_one_home_assistant_has_not_loaded_is_said_and_nothing_hangs(self):
        fake = FakeHA()
        fake.loaded = False
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
        log = run(fake, node="jarvis-show-5", sleep=sleep, clock=lambda: now[0])
        self.assertLessEqual(now[0], 11)
        warn = [line for line in log if line.startswith("WARN: not labeled yet")]
        self.assertEqual(len(warn), 1)
        self.assertIn("automation techo5_turn_room_down_jarvis_show_5", warn[0])

    def test_a_house_without_labels_still_gets_everything(self):
        fake = FakeHA()
        real = fake.ws

        def ws(msg):
            if msg["type"].startswith("config/label_registry/"):
                raise kit.HomeAssistantError("unknown command")
            return real(msg)
        fake.ws = ws
        log = run(fake, node="jarvis-show-5")
        self.assertEqual(len(fake.configs), 16)
        self.assertTrue(any(line.startswith("WARN: Home Assistant did not take the labels") for line in log))

    def test_a_hand_made_turn_down_gets_no_label(self):
        fake = FakeHA()
        fake.configs[("automation", "by_hand")] = {"alias": "by hand", "use_blueprint": {
            "path": "techo5/turn-room-down.yaml", "input": {"satellite": SHOW[0]}}}
        fake.related[SHOW[0]] = ["automation.by_hand"]
        fake.states["automation.by_hand"] = {"state": "on", "attributes": {"id": "by_hand"}}
        run(fake, node="jarvis-show-5")
        self.assertEqual(self.labels_of(fake, "automation.by_hand"), [])


class WeckerTests(unittest.TestCase):
    def test_it_sets_the_alarm_on_the_show_that_was_asked(self):
        fake = FakeHA()
        run(fake)
        wecker = fake.configs[("automation", "jarvis_local_wecker")]
        default = wecker["actions"][1]["default"]
        self.assertEqual(default[0]["action"], "esphome.{{ node }}_alarm_set")
        self.assertEqual(default[0]["data"], {"time": "{{ at }}", "days": "once", "label": "Wecker"})
        self.assertEqual(wecker["mode"], "queued")

    def test_the_node_is_made_from_the_name_like_the_daemon_makes_it(self):
        node = kit.WECKER_VARIABLES["node"]
        self.assertIn("regex_replace('[^a-z0-9]+', '_')", node)
        self.assertIn("regex_replace('^_+|_+$', '')", node)
        for name in ("genbuShow5", "Jarvis Show 5", "Küche", "  Kitchen -- Show!"):
            with self.subTest(name=name):
                same = re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", name.lower()))
                self.assertEqual(same, kit.service_prefix(name))

    def test_a_house_without_german_gets_no_wecker(self):
        fake = FakeHA(language="en")
        run(fake)
        self.assertNotIn(("automation", "jarvis_local_wecker"), fake.configs)


class TemplateTests(unittest.TestCase):
    def test_the_values_go_in_as_jinja_literals(self):
        text = kit._fill("{% set de = __PHRASES__ %}{{ __WEATHER__ }}", phrases={"kitchen": "in der Küche"},
                         weather="weather.x")
        self.assertEqual(text, '{% set de = {"kitchen": "in der Küche"} %}{{ "weather.x" }}')

    def test_no_placeholder_is_left(self):
        fake = FakeHA()
        run(fake)
        self.assertNotRegex(json.dumps(list(fake.configs.values())), r"__[A-Z]+__")


try:
    import hassil
except ImportError:
    hassil = None


@unittest.skipUnless(hassil, "hassil is not installed")
class SentenceTests(unittest.TestCase):
    """The sentences matched the way Home Assistant matches sentence triggers: every trigger whose
    sentences fit runs, so no sentence may fit two; and one that fits none goes on to Home Assistant's
    own sentences, then the model."""

    def heard(self, text: str) -> list[tuple[str, str]]:
        from hassil import Intents, recognize_all
        from hassil.expression import Group, ListReference
        from hassil.intents import WildcardSlotList
        house = kit.House(german=True, areas={"living_room": "Wohnzimmer"}, weather="weather.home",
                          music_entry=MA_ENTRY, players=["media_player.living_ma"], agent="conversation.ollama")
        intents = {}
        for name, item in {"music": kit.local_music(house), "stop": kit.local_music_stop(house),
                           "wecker": kit.wecker(), "where": kit.where_are_we(house),
                           "temperature": kit.room_temperature(house), "humidity": kit.room_humidity(house),
                           "forecast": kit.forecast(house), "greeting": kit.greeting(house)}.items():
            for t in item["triggers"]:
                intents[f"{name}:{t.get('id', '')}"] = {"data": [{"sentences": t["command"]}]}
        found = Intents.from_dict({"language": "de", "intents": intents})
        lists = set()

        def walk(e):
            if isinstance(e, ListReference):
                lists.add(e.list_name)
            elif isinstance(e, Group):
                for x in e.items:
                    walk(x)
        for intent in found.intents.values():
            for data in intent.data:
                for sentence in data.sentences:
                    walk(sentence.expression)
        for name in lists:
            found.slot_lists[name] = WildcardSlotList(name)
        got = {}
        for r in recognize_all(text, found):
            if r.intent.name in got:
                break
            got[r.intent.name] = {k: v.text for k, v in r.entities.items()}
        return sorted((name, str(slots)) for name, slots in got.items())

    def test_music_is_started_however_it_is_asked_for(self):
        for text in ["Spiele Musik!", "Ich spiele Musik.", "Spielen Musik", "Spiel Musik ab", "Musik spielen",
                     "Bitte spiel Musik", "Spiel Musik, bitte", "Spiel die Musik", "Spiel Musik Spiel Musik",
                     "Mach Musik", "Musik ab", "Musik an", "Mach die Musik an"]:
            self.assertEqual(self.heard(text), [("music:", "{}")], text)

    def test_just_the_music_has_its_own_trigger(self):
        for text in ["Die Musik.", "Musik", "Musik bitte", "Bitte Musik"]:
            self.assertEqual(self.heard(text), [("music:toggle", "{}")], text)

    def test_stop_in_the_words_home_assistant_misses(self):
        for text in ["Stopp! Stopp!", "Stopp Stopp Stopp", "Halt stopp", "Pause Pause", "Aufhören", "Hör auf",
                     "Hör auf mit der Musik", "Mach die Musik aus", "Mach bitte die Musik aus"]:
            self.assertEqual(self.heard(text), [("stop:", "{}")], text)

    def test_what_home_assistant_gets_right_is_left_to_it(self):
        for text in ["Stopp", "Pause", "Halt", "Musik aus", "Stopp die Musik", "Musik pausieren", "Weiter",
                     "Nächstes Lied"]:
            self.assertEqual(self.heard(text), [], text)

    def test_merged_and_misheard_words_still_hit_the_local_sentences(self):
        # GENBU-130: Whisper writes two words as one, or a near word, and these went to the model.
        for text in ["Spielmusik", "Spielmusik!", "Spielemusik bitte"]:
            self.assertEqual(self.heard(text), [("music:", "{}")], text)
        self.assertEqual(self.heard("Spielradio FFH"), [("music:", "{'station': 'FFH'}")])
        self.assertEqual(self.heard("Hörradio Bob"), [("music:", "{'station': 'Bob'}")])
        self.assertEqual(self.heard("Spielradio"), [("music:", "{}")])
        self.assertEqual(self.heard("Weg mich um Viertel nach Sieben"),
                         [("wecker:", "{'when': 'Viertel nach Sieben'}")])

    def test_the_other_sentences_are_heard_as_before(self):
        self.assertEqual(self.heard("Spiel was Flottes"), [("music:", "{'mood': 'Flottes'}")])
        self.assertEqual(self.heard("Spiel Musik von Queen"), [("music:", "{'mood': 'Queen'}")])
        self.assertEqual(self.heard("Hör Radio Bob"), [("music:", "{'station': 'Bob'}")])
        self.assertEqual(self.heard("Spiel etwas leiser"), [("music:", "{'mood': 'leiser'}")])
        self.assertEqual(self.heard("Wo sind wir"), [("where:", "{}")])

    def test_a_greeting_is_heard_and_nothing_more(self):
        for text in ["Hi.", "Hallo", "Hey Genbu", "Guten Morgen", "Moin!", "Servus", "Hallo Home Assistant"]:
            self.assertEqual(self.heard(text), [("greeting:", "{}")], text)
        for text in ["Hallo, mach das Licht an", "Hi, wie spät ist es"]:
            self.assertEqual(self.heard(text), [], text)

    def test_humidity_is_heard_and_not_taken_for_temperature(self):
        for text, slots in [("Wie feucht ist es im Wohnzimmer", "{'raum': 'Wohnzimmer'}"),
                            ("Wie hoch ist die Luftfeuchtigkeit in der Küche", "{'raum': 'Küche'}"),
                            ("Wie ist die Luftfeuchtigkeit hier", "{}"),
                            ("Wie viel Luftfeuchtigkeit haben wir gerade hier drin", "{}"),
                            ("Wie feucht ist es draußen", "{}"),
                            ("Wie feucht ist es", "{}"), ("Wie feucht ist das", "{}"),
                            ("Wie ist die Luftfeuchtigkeit gerade", "{}")]:
            self.assertEqual(self.heard(text), [("humidity:", slots)], text)
        self.assertEqual(self.heard("Wie warm ist es im Bad"), [("temperature:", "{'raum': 'Bad'}")])
        for text in ("Wie warm ist es", "Wie kalt ist es jetzt", "Wie ist die Temperatur"):
            self.assertEqual(self.heard(text), [("temperature:", "{}")], text)


class GenbuUpdateTests(unittest.TestCase):
    """GENBU-125: "installiere die Updates" at any Show starts every Show's update through Genbu."""

    def test_calls_genbu_and_says_who_started(self):
        auto = kit.genbu_update()
        self.assertEqual(auto["actions"][0]["default"][0]["action"], "genbu.install_updates")
        try:
            import jinja2
        except ImportError:
            self.skipTest("no jinja2 here")
        said = jinja2.Environment().from_string(kit.GENBU_UPDATING)
        for updating, want in (([], "Alle Shows sind auf dem neuesten Stand."),
                               (["Küche"], "Die Updates starten auf Küche."),
                               (["Küche", "Flur"], "Die Updates starten auf Küche und Flur."),
                               (["Küche", "Flur", "Bad"], "Die Updates starten auf Küche, Flur und Bad.")):
            self.assertEqual(said.render(started={"updating": updating, "current": []}), want)


def _ha_env():
    """Jinja with the regex filters and tests Home Assistant adds, enough to render these templates."""
    import re
    import jinja2
    env = jinja2.Environment()
    env.filters["regex_replace"] = lambda v, find="", rep="": re.sub(find, rep, v)
    env.filters["regex_findall"] = lambda v, find="": re.findall(find, v)
    env.filters["regex_search"] = lambda v, find="": bool(re.search(find, v))
    env.tests["search"] = lambda v, find="": bool(re.search(find, v))
    return env


class MisheardTemplateTests(unittest.TestCase):
    """GENBU-130: what the local sentences take in is read the same as the words meant."""

    def setUp(self):
        try:
            self.env = _ha_env()
        except ImportError:
            self.skipTest("no jinja2 here")

    def at(self, when: str) -> str:
        names = ["spoken", "nums", "half", "before", "past", "evening", "h", "m", "hh"]
        ctx = {"trigger": {"slots": {"when": when}}}
        for name in names:
            out = self.env.from_string(kit.WECKER_VARIABLES[name]).render(**ctx).strip()
            ctx[name] = {"True": True, "False": False}.get(out, out)
            if name == "nums":
                ctx[name] = [int(n) for n in re.findall(r"[0-9]+", out)]
            elif name in ("h", "m", "hh"):
                ctx[name] = int(out)
        return "%02d:%02d" % (ctx["hh"], ctx["m"])

    def test_vierte_nacht_is_viertel_nach(self):
        self.assertEqual(self.at("Viertel nach sieben"), "07:15")
        self.assertEqual(self.at("vierte Nacht sieben"), "07:15")
        self.assertEqual(self.at("Viertel vor acht"), "07:45")
        self.assertEqual(self.at("vierte vor acht"), "07:45")
        self.assertEqual(self.at("halb acht"), "07:30")
        self.assertEqual(self.at("sieben Uhr nachts"), "19:00")

    def test_a_merged_radio_is_heard_as_radio(self):
        heard = self.env.from_string(kit.RADIO_HEARD)
        self.assertEqual(heard.render(name="spielradio ffh").strip(), "radio ffh")
        self.assertEqual(heard.render(name="Hörradio Bob").strip(), "radio bob")
        self.assertEqual(heard.render(name="biele hit radio ffh").strip(), "hit radio ffh")
        self.assertEqual(heard.render(name="spiel hitradio ffh").strip(), "hitradio ffh")
        kind = self.env.from_string(kit.LOCAL_MUSIC_KIND)
        self.assertEqual(kind.render(trigger={"id": "", "slots": {}}, said="spielradio"), "radio")
        self.assertEqual(kind.render(trigger={"id": "", "slots": {}}, said="spielmusik"), "random")
        self.assertEqual(kind.render(trigger={"id": "", "slots": {"mood": "radiohead"}},
                                     said="spiel musik von radiohead"), "mood")


if __name__ == "__main__":
    unittest.main()
