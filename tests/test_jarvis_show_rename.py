import copy
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import home_assistant as ha  # noqa: E402
from jarvis_crown import rename  # noqa: E402
from jarvis_crown.shows import record_show, rename_show, show_named  # noqa: E402


def entity(entity_id, device="MAIN", entry="E1", platform="esphome"):
    return {"entity_id": entity_id, "device_id": device, "config_entry_id": entry, "platform": platform}


class FakeHA:
    """A Home Assistant that has a Show installed as "Jarvis Show 5" and since renamed genbuShow5, the
    way the house's Show 5 looked: old ids, a few with the room in front, Music Assistant's player."""

    def __init__(self):
        self.devices = [
            {"id": "MAIN", "name": "genbuShow5", "name_by_user": "genbuShow5", "config_entry_id": "E1",
             "via_device_id": None, "area_id": "living_room"},
            {"id": "RING", "name": "genbuShow5 ring", "name_by_user": None, "config_entry_id": "E1",
             "via_device_id": "MAIN"},
            {"id": "MA", "name": "Jarvis Show 5", "name_by_user": None, "config_entry_id": "M1",
             "via_device_id": None},
            {"id": "BT", "name": "Jarvis Show 5 (0c:ee:00:00:00:01)", "config_entry_id": "B1",
             "via_device_id": "MAIN"},
            {"id": "OTHER", "name": "Kitchen light", "config_entry_id": "Z1", "via_device_id": None},
        ]
        self.entities = [
            entity("select.jarvis_show_5_wake_word"),
            entity("select.jarvis_show_5_wake_word_2"),
            entity("assist_satellite.jarvis_show_5_assist_satellite"),
            entity("light.jarvis_show_5_ring_led_ring", device="RING"),
            entity("event.living_room_genbushow5_wake_word_near_miss"),
            entity("switch.living_room_genbushow5_ssh"),
            entity("select.genbushow5_assistant_1_follow_up_after"),
            entity("switch.jarvis_show_5_screen_web_access"),
            entity("media_player.jarvis_show_5", device="MA", entry="M1", platform="music_assistant"),
            entity("button.jarvis_show_5_favorite_current_song", device="MA", entry="M1", platform="music_assistant"),
            entity("light.kitchen", device="OTHER", entry="Z1", platform="zha"),
            entity("input_boolean.jarvis_show_5_away", device=None, entry=None, platform="input_boolean"),
        ]
        self.yaml_only = ["switch.genbushow5_screen_web_access"]   # taken outside the registry
        self.areas = {"living_room": "Living Room"}
        self.services = ["genbushow5_calendar_sources", "genbushow5_home_assistant"]
        self.automations = {
            "techo5_turn_room_down_genbushow5": {
                "id": "techo5_turn_room_down_genbushow5", "alias": "genbuShow5: turn the room down",
                "use_blueprint": {"input": {"satellite": "assist_satellite.jarvis_show_5_assist_satellite",
                                            "near_miss": "event.living_room_genbushow5_wake_word_near_miss"}}},
            "mine": {"id": "mine", "alias": "mine",
                     "triggers": [{"trigger": "state", "entity_id": "select.jarvis_show_5_wake_word_2"}],
                     "actions": [{"action": "esphome.jarvis_show_5_calendar_sources",
                                  "data": {"calendars": "calendar.personal"}},
                                 {"action": "light.turn_on",
                                  "data": {"brightness": "{{ states('sensor.jarvis_show_5_lux') }}"}}]},
            "untouched": {"id": "untouched", "alias": "x", "actions": [{"action": "light.turn_on",
                                                                        "target": {"entity_id": "light.kitchen"}}]},
        }
        self.scripts = {"show_off": {"sequence": [{"action": "media_player.media_play",
                                                   "target": {"entity_id": "media_player.jarvis_show_5"}}]}}
        self.boards = [{"url_path": "dashboard-show", "mode": "storage"}, {"url_path": "yaml-one", "mode": "yaml"}]
        self.lovelace = {
            None: {"views": [{"cards": [{"type": "tile", "entity": "select.jarvis_show_5_wake_word"},
                                        {"type": "tile", "entity": "light.kitchen"}]}]},
            "dashboard-show": {"views": [{"cards": [{"type": "entities", "entities": [
                "switch.living_room_genbushow5_ssh", "select.jarvis_show_5_wake_word_2"]}]}]},
        }
        self.entries = [{"entry_id": "E1", "domain": "esphome", "title": "Jarvis Show 5"}]
        self.users = [{"id": "U1", "username": "show_jarvis_show_5", "name": "Jarvis Show 5"},
                      {"id": "U0", "username": "jade", "is_owner": True}]
        self.owner = True
        self.fail_rename = set()
        self.saved = []

    def request(self, method, path, body=None):
        if method == "GET" and path == "/api/states":
            ids = [e["entity_id"] for e in self.entities] + self.yaml_only
            ids += [f"automation.{k}" for k in self.automations] + [f"script.{k}" for k in self.scripts]
            ids.append("automation.from_a_package")
            return [{"entity_id": i, "state": "on",
                     "attributes": {"id": i.split(".", 1)[1]} if i.startswith("automation.") else {}}
                    for i in ids]
        if path == "/api/template" and "areas()" in body["template"]:
            return "".join(f"{a}\t{n}\n" for a, n in self.areas.items())
        if method == "GET" and path == "/api/services":
            return [{"domain": "esphome", "services": {s: {} for s in self.services}}]
        for kind, store in (("automation", self.automations), ("script", self.scripts)):
            prefix = f"/api/config/{kind}/config/"
            if path.startswith(prefix):
                item = path[len(prefix):]
                if method == "GET":
                    if item not in store:
                        raise ha.NotFound(f"GET {path}: HTTP 404")
                    return copy.deepcopy(store[item])
                store[item] = body
                self.saved.append(f"{kind} {item}")
                return {"result": "ok"}
        if path == "/api/config/core/check_config":
            return {"result": "valid", "errors": None}
        if path.startswith("/api/config/config_entries/entry"):
            return copy.deepcopy(self.entries)
        raise AssertionError(f"unexpected {method} {path}")

    def ws(self, message):
        kind = message["type"]
        if kind == "config/entity_registry/list":
            return copy.deepcopy(self.entities)
        if kind == "config/device_registry/list":
            return copy.deepcopy(self.devices)
        if kind == "config/entity_registry/update":
            if message["entity_id"] in self.fail_rename:
                raise ha.HomeAssistantError("config/entity_registry/update: invalid_format")
            for e in self.entities:
                if e["entity_id"] == message["entity_id"]:
                    e["entity_id"] = message["new_entity_id"]
            return {}
        if kind == "lovelace/dashboards/list":
            return copy.deepcopy(self.boards)
        if kind == "lovelace/config":
            if message["url_path"] not in self.lovelace:
                raise ha.HomeAssistantError("config_not_found")
            return copy.deepcopy(self.lovelace[message["url_path"]])
        if kind == "lovelace/config/save":
            self.lovelace[message["url_path"]] = message["config"]
            self.saved.append(f"dashboard {message['url_path'] or 'lovelace'}")
            return None
        if kind == "config_entries/update":
            for e in self.entries:
                if e["entry_id"] == message["entry_id"]:
                    e["title"] = message["title"]
            return {}
        if kind == "config/auth/list":
            return copy.deepcopy(self.users)
        if kind == "config/auth_provider/homeassistant/admin_change_username":
            if not self.owner:
                raise ha.HomeAssistantError("config/auth_provider/homeassistant/admin_change_username: Unauthorized")
            next(u for u in self.users if u["id"] == message["user_id"])["username"] = message["username"]
            return None
        if kind == "config/auth/update":
            next(u for u in self.users if u["id"] == message["user_id"])["name"] = message["name"]
            return {}
        raise AssertionError(f"unexpected ws {kind}")

    def ids(self):
        return {e["entity_id"] for e in self.entities}


class Keeper:
    def __init__(self):
        self.kept = {}

    def __call__(self, what, config):
        self.kept[what] = config
        return f"backups/home-assistant/{what}.json"


def follow(fake, keep=None):
    log = []
    keep = keep if keep is not None else Keeper()
    n = rename.follow(fake, "E1", "Jarvis Show 5", "genbuShow5", keep=keep, progress=log.append)
    return n, log, keep


class SlugTests(unittest.TestCase):
    def test_slugs_as_home_assistant_makes_them(self):
        self.assertEqual(rename.slugify("Jarvis Show 5"), "jarvis_show_5")
        self.assertEqual(rename.slugify("genbuShow5"), "genbushow5")
        self.assertEqual(rename.slugify("Küche / Straße 2"), "kuche_strasse_2")


class PlanTests(unittest.TestCase):
    def test_the_show_and_its_music_assistant_player_move_to_the_new_name(self):
        fake = FakeHA()
        got = rename.plan(fake.entities, fake.devices, "E1", "Jarvis Show 5", "genbuShow5", fake.areas,
                          set(fake.yaml_only))
        self.assertEqual(got.entities, {
            "select.jarvis_show_5_wake_word": "select.genbushow5_wake_word",
            "select.jarvis_show_5_wake_word_2": "select.genbushow5_wake_word_2",
            "assist_satellite.jarvis_show_5_assist_satellite": "assist_satellite.genbushow5_assist_satellite",
            "light.jarvis_show_5_ring_led_ring": "light.genbushow5_ring_led_ring",
            "event.living_room_genbushow5_wake_word_near_miss": "event.genbushow5_wake_word_near_miss",
            "switch.living_room_genbushow5_ssh": "switch.genbushow5_ssh",
            "media_player.jarvis_show_5": "media_player.genbushow5",
            "button.jarvis_show_5_favorite_current_song": "button.genbushow5_favorite_current_song",
        })
        self.assertEqual(got.taken, [("switch.jarvis_show_5_screen_web_access",
                                      "switch.genbushow5_screen_web_access")])

    def test_nothing_moves_when_the_names_agree(self):
        fake = FakeHA()
        self.assertEqual(rename.plan(fake.entities, fake.devices, "E1", "genbushow5", "genbuShow5",
                                     fake.areas).entities, {})

    def test_two_old_ids_wanting_one_new_one_leave_the_second_alone(self):
        fake = FakeHA()
        fake.entities.append(entity("switch.jarvis_show_5_ssh"))
        got = rename.plan(fake.entities, fake.devices, "E1", "Jarvis Show 5", "genbuShow5", fake.areas)
        self.assertIn(("switch.jarvis_show_5_ssh", "switch.genbushow5_ssh"), got.taken)


class SwapTests(unittest.TestCase):
    def test_only_whole_names_are_swapped(self):
        names = {"select.a_wake_word": "select.b_wake_word"}
        got, n = rename.swap({"x": ["select.a_wake_word", "select.a_wake_word_2", "xselect.a_wake_word",
                                    "{{ states.select.a_wake_word.state }}"]}, names)
        self.assertEqual(got["x"], ["select.b_wake_word", "select.a_wake_word_2", "xselect.a_wake_word",
                                    "{{ states.select.b_wake_word.state }}"])
        self.assertEqual(n, 2)


class FollowTests(unittest.TestCase):
    def test_everything_that_used_the_old_names_uses_the_new_ones(self):
        fake = FakeHA()
        n, log, keep = follow(fake)
        self.assertEqual(n, 8)
        self.assertIn("select.genbushow5_wake_word", fake.ids())
        self.assertIn("switch.jarvis_show_5_screen_web_access", fake.ids())
        self.assertIn("input_boolean.jarvis_show_5_away", fake.ids())
        room_down = fake.automations["techo5_turn_room_down_genbushow5"]["use_blueprint"]["input"]
        self.assertEqual(room_down, {"satellite": "assist_satellite.genbushow5_assist_satellite",
                                     "near_miss": "event.genbushow5_wake_word_near_miss"})
        mine = fake.automations["mine"]
        self.assertEqual(mine["triggers"][0]["entity_id"], "select.genbushow5_wake_word_2")
        self.assertEqual(mine["actions"][0]["action"], "esphome.genbushow5_calendar_sources")
        self.assertIn("sensor.jarvis_show_5_lux", mine["actions"][1]["data"]["brightness"])  # not the Show's
        self.assertEqual(fake.scripts["show_off"]["sequence"][0]["target"]["entity_id"], "media_player.genbushow5")
        self.assertEqual(fake.lovelace[None]["views"][0]["cards"][0]["entity"], "select.genbushow5_wake_word")
        self.assertEqual(fake.lovelace["dashboard-show"]["views"][0]["cards"][0]["entities"],
                         ["switch.genbushow5_ssh", "select.genbushow5_wake_word_2"])
        self.assertEqual(sorted(fake.saved), ["automation mine", "automation techo5_turn_room_down_genbushow5",
                                              "dashboard dashboard-show", "dashboard lovelace", "script show_off"])
        self.assertEqual(sorted(keep.kept), ["automation-mine", "automation-techo5_turn_room_down_genbushow5",
                                             "dashboard-dashboard-show", "dashboard-lovelace", "script-show_off"])
        self.assertEqual(keep.kept["automation-mine"]["triggers"][0]["entity_id"], "select.jarvis_show_5_wake_word_2")
        self.assertEqual(fake.entries[0]["title"], "genbuShow5")
        self.assertEqual(fake.users[0]["username"], "show_genbushow5")
        self.assertEqual(fake.users[0]["name"], "genbuShow5")
        self.assertIn("WARN: switch.jarvis_show_5_screen_web_access keeps its id: "
                      "switch.genbushow5_screen_web_access is taken already", log)
        self.assertIn("Home Assistant checked its configuration: fine", log)

    def test_running_it_again_changes_nothing(self):
        fake = FakeHA()
        follow(fake)
        fake.saved.clear()
        n, log, keep = follow(fake)
        self.assertEqual((n, fake.saved, keep.kept), (0, [], {}))

    def test_nothing_is_changed_that_could_not_be_saved_first(self):
        fake = FakeHA()
        before = copy.deepcopy(fake.automations)
        log = []
        rename.follow(fake, "E1", "Jarvis Show 5", "genbuShow5", keep=None, progress=log.append)
        self.assertEqual(fake.automations, before)
        self.assertTrue(any(line.startswith("WARN: automation mine still uses the old names") for line in log))

    def test_an_id_home_assistant_refuses_keeps_its_name_everywhere(self):
        fake = FakeHA()
        fake.fail_rename = {"select.jarvis_show_5_wake_word"}
        _, log, _ = follow(fake)
        self.assertIn("select.jarvis_show_5_wake_word", fake.ids())
        self.assertEqual(fake.lovelace[None]["views"][0]["cards"][0]["entity"], "select.jarvis_show_5_wake_word")
        self.assertTrue(any("select.jarvis_show_5_wake_word keeps its id" in line for line in log))

    def test_without_the_owner_the_shows_user_keeps_its_name(self):
        fake = FakeHA()
        fake.owner = False
        _, log, _ = follow(fake)
        self.assertEqual(fake.users[0]["username"], "show_jarvis_show_5")
        self.assertIn("WARN: the Show's user stays 'show_jarvis_show_5': only Home Assistant's owner may rename "
                      "users", log)


class FollowRenameTests(unittest.TestCase):
    """The step in deploy() that asks and then calls follow(); deploy around it is in
    test_jarvis_show_home_assistant."""

    def run_step(self, fake, choose=None, **kw):
        renamed, log = [], []
        opts = ha.DeployOptions(name="Jarvis Show 5", psk="x", renamed=renamed.append, keep_replaced=Keeper(), **kw)
        got = ha._follow_rename(fake, "E1", opts, "genbushow5", log.append, choose)
        return got, renamed, log

    def test_the_question_says_what_becomes_what(self):
        fake, asked = FakeHA(), {}

        def choose(label, current, options, names):
            asked[label] = names
            return "yes"
        got, renamed, _ = self.run_step(fake, choose)
        self.assertEqual(asked["new name"], {"yes": "yes, jarvis_show_5_... becomes genbushow5_...",
                                             "no": "no, keep jarvis_show_5_..."})
        self.assertEqual((got, renamed), ("genbuShow5", ["genbuShow5"]))
        self.assertIn("select.genbushow5_wake_word", fake.ids())

    def test_no_leaves_everything(self):
        fake = FakeHA()
        got, renamed, log = self.run_step(fake, lambda *_: "no")
        self.assertEqual((got, renamed, fake.saved), (None, [], []))
        self.assertIn("select.jarvis_show_5_wake_word", fake.ids())
        self.assertIn("its entities keep the name 'Jarvis Show 5'", log)

    def test_without_a_question_or_a_yes_nothing_moves(self):
        fake = FakeHA()
        self.assertIsNone(self.run_step(fake)[0])
        self.assertIsNone(self.run_step(fake, lambda *_: "yes", rename_entities=False)[0])
        self.assertIn("select.jarvis_show_5_wake_word", fake.ids())

    def test_the_switch_says_yes_without_asking(self):
        fake = FakeHA()
        got, _, _ = self.run_step(fake, lambda *_: self.fail("asked"), rename_entities=True)
        self.assertEqual(got, "genbuShow5")

    def test_a_device_named_otherwise_goes_by_the_node_name(self):
        fake = FakeHA()
        fake.devices[0]["name_by_user"] = "Wohnzimmer Show"
        got, _, _ = self.run_step(fake, rename_entities=True)
        self.assertEqual(got, "genbushow5")


class RecordTests(unittest.TestCase):
    def test_the_record_takes_the_new_name_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            folder = pathlib.Path(td) / "SERIAL05BJ"
            record_show(folder, name="Jarvis Show 5", board="checkers")
            before = json.loads((folder / "show.json").read_text())
            self.assertTrue(rename_show(folder, "genbuShow5"))
            after = json.loads((folder / "show.json").read_text())
            self.assertEqual(after, {**before, "name": "genbuShow5"})
            self.assertEqual(show_named(pathlib.Path(td), "genbushow5").board, "checkers")
            self.assertFalse(rename_show(pathlib.Path(td) / "none", "x"))


if __name__ == "__main__":
    unittest.main()
