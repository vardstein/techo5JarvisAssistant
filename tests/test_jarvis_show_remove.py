import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import home_assistant as ha  # noqa: E402
from jarvis_crown import music_assistant as ma  # noqa: E402
from jarvis_crown import remove  # noqa: E402
from jarvis_crown import show_reset  # noqa: E402
from jarvis_crown.shows import known_shows, record_show, update_show  # noqa: E402

MAC = "0c:ee:00:00:00:05"
BP = "openjade/turn-room-down.yaml"
SAT = "assist_satellite.genbushow5_assist_satellite"


def entity(entity_id, device="MAIN"):
    return {"entity_id": entity_id, "device_id": device, "config_entry_id": "E1", "platform": "esphome"}


class FakeHA:
    """A house with genbuShow5 in it the way the installer left it, and a little of somebody else's."""

    def __init__(self, *, genbu="1.1.0", new_main=None):
        self.entries = [{"entry_id": "E1", "domain": "esphome", "title": "genbuShow5"},
                        {"entry_id": "E2", "domain": "esphome", "title": "livingRoomEcho8"}]
        self.devices = [
            {"id": "MAIN", "name": "genbuShow5", "config_entry_id": "E1", "via_device_id": None,
             "connections": [["mac", MAC.upper()]], "config_entries": ["E1"]},
            {"id": "RING", "name": "genbuShow5 ring", "config_entry_id": "E1", "via_device_id": "MAIN",
             "config_entries": ["E1"]},
            {"id": "ECHO", "name": "livingRoomEcho8", "config_entry_id": "E2", "via_device_id": None,
             "config_entries": ["E2"]},
        ]
        self.entities = [entity(SAT), entity("light.genbushow5_ring", device="RING"),
                         entity("select.genbushow5_wake_word"),
                         {"entity_id": "light.echo", "device_id": "ECHO", "config_entry_id": "E2"}]
        self.automations = {
            "techo5_turn_room_down_genbushow5": {"id": "techo5_turn_room_down_genbushow5",
                                                 "use_blueprint": {"path": BP, "input": {"satellite": SAT}}},
            "techo5_turn_room_down_livingroomecho8": {"id": "techo5_turn_room_down_livingroomecho8",
                                                      "use_blueprint": {"path": BP, "input": {}}},
            "mine": {"id": "mine", "triggers": [{"entity_id": "select.genbushow5_wake_word"}]},
        }
        self.states = {"automation.ours": {"entity_id": "automation.ours",
                                           "attributes": {"id": "techo5_turn_room_down_genbushow5"}},
                       "automation.mine": {"entity_id": "automation.mine", "attributes": {"id": "mine"}}}
        self.users = [{"id": "U1", "username": "show_genbushow5", "name": "genbuShow5", "group_ids": ["system-users"]},
                      {"id": "U2", "username": "show_livingroomecho8", "group_ids": ["system-users"]},
                      {"id": "U0", "username": "jade", "is_owner": True}]
        self.lovelace = {None: {"views": [{"cards": [{"type": "tile", "entity": "select.genbushow5_wake_word"}]}]}}
        self.scripts = {}
        self.labels = []
        self.genbu = genbu
        self.new_main = new_main  # what a Genbu before 1.4.0 answers forget_show with
        self.calls = []
        self.fail = set()

    # -- the two ways in

    def request(self, method, path, body=None, **_):
        self.calls.append((method, path, body))
        if (method, path) in self.fail:
            raise ha.HomeAssistantError(f"{method} {path} refused")
        if path == "/api/config/config_entries/entry?domain=esphome":
            return self.entries
        if path == "/api/config/config_entries/entry?domain=genbu":
            return [{"entry_id": "G", "domain": "genbu"}] if self.genbu else []
        if path.startswith("/api/diagnostics/config_entry/"):
            return {"data": {"config": {"data": {"device_name": "genbushow5"}}}}
        if path == "/api/states":
            return list(self.states.values())
        if path == "/api/states/sensor.genbu_house_version":
            return {"state": "1.4.0", "attributes": {}}
        if path.startswith("/api/states/"):
            if path[12:] not in self.states:
                raise ha.NotFound(path)
            return self.states[path[12:]]
        if path == "/api/services":
            services = {"forget_show": {}} if self.genbu == "1.1.0" else {}
            return [{"domain": "esphome", "services": {}}, {"domain": "genbu", "services": services}]
        if path.startswith(("/api/config/automation/config/", "/api/config/script/config/")):
            kind, item = path.split("/")[3], path.rsplit("/", 1)[1]
            configs = self.automations if kind == "automation" else self.scripts
            if item not in configs:
                raise ha.NotFound(path)
            if method == "DELETE":
                del configs[item]
                self.entities = [e for e in self.entities
                                 if (e.get("platform"), e.get("unique_id")) != (kind, item)]
                return {"result": "ok"}
            return configs[item]
        if method == "DELETE" and path.startswith("/api/config/config_entries/entry/"):
            entry_id = path.rsplit("/", 1)[1]
            self.entries = [e for e in self.entries if e["entry_id"] != entry_id]
            self.devices = [d for d in self.devices if d["config_entry_id"] != entry_id]
            self.entities = [e for e in self.entities if e["config_entry_id"] != entry_id]
            return {"require_restart": False}
        if path == "/api/services/genbu/forget_show?return_response":
            return {"changed_states": [], "service_response": {"new_main": self.new_main} if self.new_main else {}}
        raise AssertionError(f"unexpected {method} {path}")

    def ws(self, message):
        self.calls.append(("WS", message["type"], message))
        kind = message["type"]
        if kind == "config/entity_registry/list":
            return self.entities
        if kind == "config/device_registry/list":
            return self.devices
        if kind == "search/related":
            if message["item_id"] == SAT:
                return {"automation": ["automation.ours"]}
            if message["item_id"] == "MAIN":
                return {"automation": ["automation.ours", "automation.mine"], "script": ["script.wake"]}
            return {}
        if kind == "config/auth/list":
            return self.users
        if kind == "config/auth/delete":
            self.users = [u for u in self.users if u["id"] != message["user_id"]]
            return None
        if kind == "lovelace/dashboards/list":
            return []
        if kind == "config/label_registry/list":
            return self.labels
        if kind == "config/label_registry/delete":
            # as Home Assistant does: the label goes off everything that had it
            self.labels = [x for x in self.labels if x["label_id"] != message["label_id"]]
            for item in self.entities + self.devices:
                if message["label_id"] in item.get("labels", []):
                    item["labels"] = [x for x in item["labels"] if x != message["label_id"]]
            return None
        if kind == "lovelace/config":
            return self.lovelace[message.get("url_path")]
        raise AssertionError(f"unexpected ws {kind}")


PLAYER = {"player_id": "upf6aac30e", "provider": "universal_player", "name": "Jarvis Show 5", "available": True}


class Music:
    def __init__(self, players=(PLAYER,)):
        self.users = [{"user_id": "m1", "username": "genbushow5", "role": "user"}]
        self.players = [dict(p) for p in players]
        self.asked, self.dropped = [], []

    def find(self, names, mac):
        self.asked.append((list(names), mac))
        return list(self.users), list(self.players)

    def drop(self, users, players):
        self.dropped.append((users, players))
        return []


def kept():
    saved = {}

    def keep(what, config):
        saved[what] = config
        return f"backups/home-assistant/{what}.json"
    return saved, keep


class FindTests(unittest.TestCase):
    def test_finds_everything_the_installer_left(self):
        house, music = FakeHA(), Music()
        found = remove.find(house, "genbuShow5", music=music.find)
        self.assertEqual(found.entry["entry_id"], "E1")
        self.assertEqual(found.node, "genbushow5")
        self.assertEqual(found.mac, MAC)
        self.assertEqual([d["id"] for d in found.devices], ["MAIN", "RING"])
        self.assertEqual([item for item, _ in found.automations], ["techo5_turn_room_down_genbushow5"])
        self.assertEqual([u["id"] for u in found.users], ["U1"])
        self.assertEqual(music.asked, [(["genbuShow5"], MAC)])
        self.assertTrue(found.forget_action)
        # somebody else's automation and script, and the dashboard, are listed and left alone
        self.assertEqual(found.uses, ["automation.mine", "dashboard lovelace", "script.wake"])

    def test_the_other_show_is_not_touched(self):
        found = remove.find(FakeHA(), "genbuShow5")
        self.assertNotIn("U2", [u["id"] for u in found.users])
        self.assertNotIn("techo5_turn_room_down_livingroomecho8", [i for i, _ in found.automations])

    def test_the_second_user_an_admin_run_made_is_found_too(self):
        house = FakeHA()
        house.users.append({"id": "U3", "username": "show_genbushow5-2", "group_ids": ["system-users"]})
        found = remove.find(house, "genbuShow5")
        self.assertEqual([u["id"] for u in found.users], ["U1", "U3"])

    def test_an_owner_named_like_the_show_is_never_taken(self):
        house = FakeHA()
        house.users.append({"id": "U9", "username": "show_old", "is_owner": True})
        found = remove.find(house, "genbuShow5", also=["old"])
        self.assertEqual([u["id"] for u in found.users], ["U1"])
        self.assertEqual(found.skipped_users, ["show_old"])

    def test_a_second_run_finds_what_is_left_by_name_and_kept_mac(self):
        house, music = FakeHA(), Music()
        house.entries = house.entries[1:]
        found = remove.find(house, "genbuShow5", mac=MAC, music=music.find)
        self.assertIsNone(found.entry)
        self.assertEqual(found.node, "genbushow5")
        self.assertEqual([item for item, _ in found.automations], ["techo5_turn_room_down_genbushow5"])
        self.assertEqual(music.asked, [(["genbuShow5"], MAC)])

    def test_nothing_left_is_nothing(self):
        house = FakeHA()
        house.entries, house.automations, house.users = [], {}, []
        found = remove.find(house, "genbuShow5", music=Music(players=()).find)
        found.music_users = []
        self.assertFalse(found.anything)

    def test_music_that_could_not_look_is_said_so(self):
        found = remove.find(FakeHA(), "genbuShow5", music=lambda names, mac: None)
        self.assertFalse(found.music_asked)
        stays = remove.plan(found)[1]
        self.assertTrue(any("not looked at" in s for s in stays))


class PlanTests(unittest.TestCase):
    def test_a_connected_player_goes_too(self):
        found = remove.find(FakeHA(), "genbuShow5", music=Music().find)
        goes, stays = remove.plan(found)
        self.assertIn("automation techo5_turn_room_down_genbushow5", goes)
        self.assertIn("Home Assistant user 'show_genbushow5'", goes)
        self.assertIn("Music Assistant user 'genbushow5'", goes)
        self.assertTrue(any(g.startswith("ESPHome entry 'genbuShow5' with its 2 devices") for g in goes))
        self.assertIn("genbushow5's place in Genbu", goes)
        self.assertIn("Music Assistant player 'Jarvis Show 5' (and its device in Home Assistant), connected now",
                      goes)
        self.assertTrue(any(s.startswith("automation.mine uses it") for s in stays))

    def test_an_offline_player_goes_now(self):
        found = remove.find(FakeHA(), "genbuShow5", music=Music(players=[dict(PLAYER, available=False)]).find)
        goes, _ = remove.plan(found)
        self.assertIn("Music Assistant player 'Jarvis Show 5' (and its device in Home Assistant)", goes)

    def test_an_older_genbu_is_left_with_a_hint(self):
        found = remove.find(FakeHA(genbu="1.0.0"), "genbuShow5")
        goes, stays = remove.plan(found)
        self.assertFalse(any("Genbu" in g for g in goes))
        self.assertTrue(any("older than 1.1.0" in s for s in stays))


class RemoveTests(unittest.TestCase):
    def test_removes_in_order_after_saving_it(self):
        house, music = FakeHA(), Music(players=[dict(PLAYER, available=False)])
        found = remove.find(house, "genbuShow5", music=music.find)
        saved, keep = kept()
        lines = []
        failed = remove.remove(house, found, keep=keep, drop=music.drop, progress=lines.append)
        self.assertEqual(failed, 0)
        snap = saved["remove-show-genbushow5"]
        self.assertIn("techo5_turn_room_down_genbushow5", snap["automations"])
        self.assertEqual(snap["mac"], MAC)
        self.assertNotIn("token", json.dumps(snap))
        writes = [c for c in house.calls if c[0] in ("DELETE", "POST") or c[1] == "config/auth/delete"]
        self.assertEqual([c[1] for c in writes], [
            "/api/config/automation/config/techo5_turn_room_down_genbushow5",
            "config/auth/delete",
            "/api/config/config_entries/entry/E1",
            "/api/services/genbu/forget_show?return_response"])
        self.assertEqual(writes[-1][2], {"node": "genbushow5"})
        self.assertEqual(music.dropped, [(["m1"], ["upf6aac30e"])])
        self.assertIn("techo5_turn_room_down_livingroomecho8", house.automations)
        self.assertIn("mine", house.automations)
        self.assertEqual([u["id"] for u in house.users], ["U2", "U0"])
        self.assertEqual([e["entry_id"] for e in house.entries], ["E2"])
        self.assertIn("Genbu forgot genbushow5", lines)

    def test_an_older_genbu_says_which_show_is_main_now(self):
        house = FakeHA(new_main="livingroomecho8")
        lines = []
        remove.remove(house, remove.find(house, "genbuShow5"), keep=kept()[1], progress=lines.append)
        self.assertIn("Genbu forgot genbushow5; livingroomecho8 is the main update Show now", lines)

    def test_a_connected_player_is_sent_too(self):
        house, music = FakeHA(), Music()
        found = remove.find(house, "genbuShow5", music=music.find)
        remove.remove(house, found, keep=kept()[1], drop=music.drop, progress=lambda _: None)
        self.assertEqual(music.dropped, [(["m1"], ["upf6aac30e"])])

    def test_a_failed_step_is_reported_and_the_rest_runs(self):
        house = FakeHA()
        house.fail.add(("DELETE", "/api/config/automation/config/techo5_turn_room_down_genbushow5"))
        found = remove.find(house, "genbuShow5")
        lines = []
        failed = remove.remove(house, found, keep=kept()[1], progress=lines.append)
        self.assertEqual(failed, 1)
        self.assertTrue(any(line.startswith("WARN: automation techo5_turn_room_down_genbushow5") for line in lines))
        self.assertEqual([e["entry_id"] for e in house.entries], ["E2"])

    def test_an_older_genbu_is_not_called(self):
        house = FakeHA(genbu="1.0.0")
        found = remove.find(house, "genbuShow5")
        lines = []
        remove.remove(house, found, keep=kept()[1], progress=lines.append)
        self.assertNotIn("forget_show", json.dumps(house.calls))
        self.assertFalse(any("Genbu" in line for line in lines))

    def test_run_twice_the_second_finds_only_the_player(self):
        house, music = FakeHA(), Music()
        remove.remove(house, remove.find(house, "genbuShow5"), keep=kept()[1], progress=lambda _: None)
        again = remove.find(house, "genbuShow5", mac=MAC, music=music.find)
        goes, _ = remove.plan(again)
        self.assertEqual(len(goes), 2)
        self.assertTrue(goes[0].startswith("Music Assistant user"))
        self.assertTrue(goes[1].startswith("Music Assistant player"))


SHOW_LABEL = {"label_id": "genbu_show_genbushow5", "name": "Genbu Show: genbushow5"}
INSTALLED = {"label_id": "genbu_show_installed", "name": "Genbu Show installed"}


def labeled(kind, item, *label_ids):
    return {"entity_id": f"{kind}.{item}", "platform": kind, "unique_id": item, "device_id": None,
            "config_entry_id": None, "labels": list(label_ids)}


def labeled_house():
    """The house after a 1.3.0 install: the Show's pieces carry both labels, the house-wide ones only the
    common one, and somebody added an automation of their own under the Show's label."""
    house = FakeHA()
    house.labels = [dict(SHOW_LABEL), dict(INSTALLED)]
    both = (SHOW_LABEL["label_id"], INSTALLED["label_id"])
    house.devices[0]["labels"] = list(both)
    house.devices[1]["labels"] = list(both)
    house.automations["hall_lights_with_show5"] = {"id": "hall_lights_with_show5", "triggers": []}
    house.automations["jarvis_local_timer"] = {"id": "jarvis_local_timer", "triggers": []}
    house.scripts = {"show5_radio": {"sequence": []}, "jarvis_music_play": {"sequence": []}}
    house.entities += [labeled("automation", "techo5_turn_room_down_genbushow5", *both),
                       labeled("automation", "hall_lights_with_show5", *both),
                       labeled("script", "show5_radio", *both),
                       labeled("automation", "jarvis_local_timer", INSTALLED["label_id"]),
                       labeled("script", "jarvis_music_play", INSTALLED["label_id"])]
    return house


class LabelTests(unittest.TestCase):
    def test_what_carries_the_shows_label_goes_and_the_label_after_it(self):
        house = labeled_house()
        found = remove.find(house, "genbuShow5", music=Music(players=()).find)
        self.assertEqual([i for i, _ in found.automations],
                         ["techo5_turn_room_down_genbushow5", "hall_lights_with_show5"])
        self.assertEqual([i for i, _ in found.scripts], ["show5_radio"])
        self.assertEqual([x["label_id"] for x in found.labels], ["genbu_show_genbushow5"])
        goes, stays = remove.plan(found)
        self.assertIn("script show5_radio", goes)
        self.assertIn("label 'Genbu Show: genbushow5'", goes)
        self.assertIn("1 house-wide automation: automation.jarvis_local_timer", stays)

        saved, keep = kept()
        lines = []
        self.assertEqual(remove.remove(house, found, keep=keep, progress=lines.append), 0)
        self.assertEqual(saved["remove-show-genbushow5"]["scripts"], {"show5_radio": {"sequence": []}})
        self.assertEqual(saved["remove-show-genbushow5"]["labels"], [SHOW_LABEL])
        self.assertEqual(set(house.automations),
                         {"techo5_turn_room_down_livingroomecho8", "mine", "jarvis_local_timer"})
        self.assertEqual(set(house.scripts), {"jarvis_music_play"})
        self.assertEqual(house.labels, [INSTALLED])
        self.assertIn("label 'Genbu Show: genbushow5': removed", lines)
        # the label goes last, once the Show's pieces are gone
        writes = [c[1] for c in house.calls if c[0] in ("DELETE", "POST") or c[1].endswith("/delete")]
        self.assertEqual(writes[-1], "config/label_registry/delete")

    def test_what_stays_is_named_and_counted(self):
        house = labeled_house()
        house.labels.append({"label_id": "genbu_show_hall", "name": "Genbu Show: hallshow5"})
        house.automations["jarvis_local_weather"] = {"id": "jarvis_local_weather", "triggers": []}
        house.entities += [labeled("automation", "jarvis_local_weather", INSTALLED["label_id"]),
                           labeled("automation", "techo5_turn_room_down_hallshow5", "genbu_show_hall",
                                   INSTALLED["label_id"])]
        found = remove.find(house, "genbuShow5")
        self.assertEqual(found.house, ["automation.jarvis_local_timer", "automation.jarvis_local_weather",
                                       "script.jarvis_music_play"])
        self.assertEqual(found.others, {"hallshow5": ["automation.techo5_turn_room_down_hallshow5"]})
        stays = remove.plan(found)[1]
        self.assertIn("2 house-wide automations: automation.jarvis_local_timer, automation.jarvis_local_weather",
                      stays)
        self.assertIn("1 house-wide script: script.jarvis_music_play", stays)
        self.assertIn("hallshow5's own: automation.techo5_turn_room_down_hallshow5", stays)
        self.assertFalse(any("only the 'Genbu Show installed' label" in s for s in stays))
        self.assertTrue(remove.only_this(found).startswith("only genbuShow5 goes;"))

    def test_the_label_stays_while_something_still_carries_it(self):
        house = labeled_house()
        house.fail.add(("DELETE", "/api/config/script/config/show5_radio"))
        found = remove.find(house, "genbuShow5")
        lines = []
        self.assertEqual(remove.remove(house, found, keep=kept()[1], progress=lines.append), 1)
        self.assertIn(SHOW_LABEL, house.labels)
        self.assertTrue(any(line.startswith("label 'Genbu Show: genbushow5' stays: 1 thing(s)") for line in lines))
        self.assertNotIn("config/label_registry/delete", [c[1] for c in house.calls])

    def test_a_label_under_an_older_name_is_found(self):
        house = FakeHA()
        house.labels = [{"label_id": "old", "name": "genbu show: KITCHENSHOW"}]
        house.automations["kitchen_extra"] = {"id": "kitchen_extra"}
        house.entities.append(labeled("automation", "kitchen_extra", "old"))
        found = remove.find(house, "genbuShow5", also=["kitchenShow"])
        self.assertIn("kitchen_extra", [i for i, _ in found.automations])
        self.assertEqual([x["label_id"] for x in found.labels], ["old"])

    def test_the_common_label_alone_takes_nothing(self):
        house = labeled_house()
        house.labels = [dict(INSTALLED)]
        found = remove.find(house, "genbuShow5")
        self.assertEqual([i for i, _ in found.automations], ["techo5_turn_room_down_genbushow5"])
        self.assertFalse(found.scripts or found.labels)

    def test_a_house_without_labels_is_found_by_name(self):
        house = FakeHA()
        found = remove.find(house, "genbuShow5")
        self.assertEqual([i for i, _ in found.automations], ["techo5_turn_room_down_genbushow5"])
        self.assertFalse(found.labels)


def _cli():
    spec = importlib.util.spec_from_file_location("jarvis_show_cli_remove", ROOT / "tools" / "jarvis-show.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class RemoveShowCommandTests(unittest.TestCase):
    """The run as a whole: the reset first when asked and it can be, then the house side."""

    def setUp(self):
        self.cli = _cli()
        self.reach = show_reset.Reach(host="10.0.0.5", serial="G0000000000005", keeps_wifi=True)

    def run_it(self, *, reset_show=True, keep_wifi=False, probe=None, reset=None, answers=(), no_questions=False,
               players=(PLAYER,), nothing_left=False, key=True, record=None):
        cli = self.cli
        found = remove.find(FakeHA(), "genbuShow5", music=Music(players=players).find)
        settings = types.SimpleNamespace(ha_admin=True, ha_sign_in=object(), music_assistant="ma.example.org",
                                         music_assistant_token="t")
        args = types.SimpleNamespace(name="genbuShow5", yes_remove=True, no_questions=no_questions,
                                     reset_show=reset_show, keep_wifi=keep_wifi, host=None, ssh_identity=None)
        asked = iter(answers)
        asker = types.SimpleNamespace(yes=lambda label, **_: next(asked))
        plan = remove.plan(found) if not nothing_left else ([], ["the blueprints"])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "gather", return_value=settings), \
                mock.patch.object(cli.gone, "plan", return_value=plan), \
                mock.patch.object(cli, "identity_for", return_value=pathlib.Path("/k/genbu_genbuShow5") if key else None), \
                mock.patch.object(cli, "_asker", return_value=asker), \
                mock.patch.object(cli.gone, "find", return_value=found), \
                mock.patch.object(cli.gone, "remove", return_value=0) as removed, \
                mock.patch.object(cli.show_reset, "address", return_value="10.0.0.5"), \
                mock.patch.object(cli.show_reset, "probe", side_effect=probe, return_value=self.reach) as probed, \
                mock.patch.object(cli.show_reset, "reset", side_effect=reset) as reset_done, \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            folder = pathlib.Path(tmp) / "G0000000000005"
            if record is not None:
                record_show(folder, name="genbuShow5", board="crown")
                update_show(folder, **record)
            code = cli.remove_show_command(args, pathlib.Path(tmp))
            self.record = known_shows(pathlib.Path(tmp))
        self.out = out.getvalue() + err.getvalue()
        self.assertNotIn("10.0.0.5", self.out)
        return code, removed, probed, reset_done

    def test_reset_then_removed(self):
        code, removed, _, reset_done = self.run_it(keep_wifi=True)
        self.assertEqual(code, 0)
        self.assertTrue(reset_done.call_args.kwargs["keep_wifi"])
        removed.assert_called_once()
        self.assertIn("genbuShow5 is reset and removed", self.out)
        self.assertIn("change it on the others' setup pages", self.out)
        self.assertIn("doesn't undo a copy", self.out)

    def test_a_show_out_of_reach_is_removed_only(self):
        code, removed, _, reset_done = self.run_it(probe=show_reset.ResetError("SSH: Connection timed out"))
        self.assertEqual(code, 0)
        reset_done.assert_not_called()
        removed.assert_called_once()
        self.assertIn("it can't be reset from here", self.out)
        self.assertIn("genbuShow5 is removed only", self.out)

    def test_keep_wifi_on_an_older_image_changes_nothing(self):
        self.reach.keeps_wifi = False
        code, removed, _, reset_done = self.run_it(keep_wifi=True)
        self.assertEqual(code, 1)
        reset_done.assert_not_called()
        removed.assert_not_called()
        self.assertIn("--keep-wifi no", self.out)

    def test_no_reset_is_the_house_side_only(self):
        code, removed, probed, _ = self.run_it(reset_show=False, keep_wifi=None)
        self.assertEqual(code, 0)
        probed.assert_not_called()
        removed.assert_called_once()
        self.assertIn("removed only", self.out)
        self.assertIn("makes it again", self.out)
        self.assertIn("the house word is the same on every Show", self.out)

    def test_a_reset_that_does_not_go_through_still_removes(self):
        code, removed, _, _ = self.run_it(reset=show_reset.ResetError("the mark was not left (Permission denied)"))
        self.assertEqual(code, 0)
        removed.assert_called_once()
        self.assertIn("the reset did not go through", self.out)
        self.assertIn("removed only", self.out)

    def test_left_out_it_asks(self):
        code, _, _, reset_done = self.run_it(reset_show=None, keep_wifi=None, answers=[True, False])
        self.assertEqual(code, 0)
        self.assertFalse(reset_done.call_args.kwargs["keep_wifi"])

    def test_the_reset_is_kept_in_the_record(self):
        self.run_it(record={})
        self.assertTrue(self.record[0].reset)
        self.run_it(record={}, reset_show=False, keep_wifi=None)
        self.assertEqual(self.record[0].reset, "")

    def test_a_rerun_after_a_reset_says_so(self):
        code, removed, _, _ = self.run_it(record={"reset": "2026-10-18T10:00:00+00:00"},
                                          probe=show_reset.ResetError("SSH: Permission denied (publickey)"))
        self.assertEqual(code, 0)
        removed.assert_called_once()
        self.assertIn("An earlier run reset it on 2026-10-18", self.out)

    def test_a_show_that_does_not_restart_erases_later(self):
        code, removed, _, _ = self.run_it(reset=show_reset.NotRestarted("it did not restart"))
        self.assertEqual(code, 0)
        removed.assert_called_once()
        self.assertIn("is removed and erases the next time it starts", self.out)
        self.assertNotIn("removed only", self.out)

    def test_nothing_left_in_the_house_can_still_be_reset(self):
        code, removed, _, reset_done = self.run_it(nothing_left=True)
        self.assertEqual(code, 0)
        reset_done.assert_called_once()
        removed.assert_not_called()
        self.assertIn("nothing of genbuShow5 is left in the house", self.out)
        self.assertIn("genbuShow5 is reset: it comes back in setup mode", self.out)

    def test_nothing_left_and_no_reset_is_done(self):
        code, removed, probed, _ = self.run_it(nothing_left=True, reset_show=False, keep_wifi=None)
        self.assertEqual(code, 0)
        probed.assert_not_called()
        removed.assert_not_called()

    def test_nothing_left_and_out_of_reach_says_reset_it_by_hand(self):
        code, removed, _, _ = self.run_it(nothing_left=True, probe=show_reset.ResetError("SSH did not answer"))
        self.assertEqual(code, 1)
        removed.assert_not_called()
        self.assertIn("Reset it on its screen", self.out)

    def test_nothing_left_without_a_key_or_after_a_reset_asks_nothing(self):
        for kw in ({"key": False}, {"record": {"reset": "2026-10-18T10:00:00+00:00"}}):
            code, _, probed, _ = self.run_it(nothing_left=True, reset_show=None, keep_wifi=None, **kw)
            self.assertEqual(code, 0, kw)
            probed.assert_not_called()

    def test_nobody_to_ask_removes_nothing(self):
        code, removed, probed, _ = self.run_it(reset_show=None, keep_wifi=None, answers=[None])
        self.assertEqual(code, 1)
        probed.assert_not_called()
        removed.assert_not_called()
        self.assertIn("--reset-show yes or no", self.out)


class FlagTests(unittest.TestCase):
    def setUp(self):
        self.cli = _cli()

    def parse(self, *argv):
        with mock.patch.object(sys, "argv", ["jarvis-show.py", *argv]):
            return self.cli.parse_args()

    def test_the_flag_is_its_own_run(self):
        self.assertEqual(self.parse("--remove-show", "--name", "genbuShow5").command, "remove-show")
        self.assertEqual(self.parse().command, "preflight")

    def test_the_flag_with_a_command_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            self.parse("home-assistant", "--remove-show", "--name", "genbuShow5")
        self.assertIn("--remove-show is a run of its own", err.getvalue())

    def test_yes_and_no_in_every_spelling(self):
        for said, meant in (("yes", True), ("Y", True), ("true", True), ("1", True),
                            ("no", False), ("n", False), ("FALSE", False), ("0", False)):
            args = self.parse("--remove-show", "--name", "genbuShow5", "--reset-show", said, "--keep-wifi", said)
            self.assertEqual((args.reset_show, args.keep_wifi), (meant, meant), said)
        args = self.parse("--remove-show", "--name", "genbuShow5")
        self.assertEqual((args.reset_show, args.keep_wifi), (None, None))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            self.parse("--remove-show", "--name", "genbuShow5", "--reset-show", "maybe")
        self.assertIn("yes or no", err.getvalue())

    def test_the_old_command_is_gone(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.parse("remove-show", "--name", "genbuShow5")


class RecordTests(unittest.TestCase):
    def test_the_mac_is_kept_with_the_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / "G0000000000005"
            record_show(folder, name="genbuShow5", board="crown")
            self.assertTrue(update_show(folder, mac=MAC))
            show = known_shows(pathlib.Path(tmp))[0]
            self.assertEqual((show.name, show.mac), ("genbuShow5", MAC))
            self.assertFalse(update_show(pathlib.Path(tmp) / "none", mac=MAC))


class MusicAssistantSideTests(unittest.TestCase):
    server = staticmethod(lambda *answers: _server(*answers))

    def test_finds_the_shows_user_and_player_by_mac(self):
        players = [
            {"player_id": "upf6aac30e", "provider": "universal_player", "name": "Jarvis Show 5",
             "output_protocols": [{"protocol_domain": "sendspin", "output_protocol_id": "0cee00000005"}]},
            {"player_id": "0cee00000005", "provider": "sendspin", "name": "Jarvis Show 5",
             "device_info": {"mac_address": MAC}},
            {"player_id": "wiim", "provider": "hass_players", "name": "Jarvis Show 5 speaker",
             "device_info": {"mac_address": "aa:bb:cc:dd:ee:ff"}},
        ]
        users = [{"user_id": "m1", "username": "genbushow5", "role": "user"},
                 {"user_id": "m0", "username": "baka", "role": "admin"}]
        sock = self.server({"message_id": "1", "result": {}}, {"message_id": "2", "result": users},
                           {"message_id": "3", "result": players})
        got_users, got_players = ma.leftovers_exchange(sock, "ma.example.org", "t", ["genbuShow5"], MAC,
                                                       key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual([u["user_id"] for u in got_users], ["m1"])
        self.assertEqual([p["player_id"] for p in got_players], ["upf6aac30e", "0cee00000005"])
        self.assertEqual([m["command"] for m in sock.messages()], ["auth", "auth/users", "players/all"])

    def test_without_a_mac_only_a_shows_player_by_name(self):
        players = [{"player_id": "u1", "provider": "universal_player", "name": "genbuShow5"},
                   {"player_id": "h1", "provider": "hass_players", "name": "genbuShow5"}]
        sock = self.server({"message_id": "1", "result": {}}, {"message_id": "2", "result": []},
                           {"message_id": "3", "result": players})
        _, got = ma.leftovers_exchange(sock, "ma.example.org", "t", ["genbuShow5"], "",
                                       key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual([p["player_id"] for p in got], ["u1"])

    def test_remove_collects_what_failed(self):
        sock = self.server({"message_id": "1", "result": {}}, {"message_id": "2", "result": None},
                           {"message_id": "3", "error_code": 999, "details": "player is active"})
        failed = ma.remove_exchange(sock, "ma.example.org", "t", ["m1"], ["upf6aac30e"],
                                    key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual(failed, ["config/players/remove: player is active"])
        sent = sock.messages()
        self.assertEqual([(m["command"], m["args"]) for m in sent[1:]],
                         [("auth/user/delete", {"user_id": "m1"}),
                          ("config/players/remove", {"player_id": "upf6aac30e"})])


def _server(*answers):
    """Music Assistant on a socket, as in test_jarvis_show_home_assistant."""
    def frame(obj):
        data = json.dumps(obj).encode()
        n = len(data)
        return bytes([0x81, n]) + data if n < 126 else bytes([0x81, 126]) + n.to_bytes(2, "big") + data

    inbox = (b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"
             + frame({"server_id": "x", "server_version": "2.10.6", "schema_version": 32})
             + b"".join(frame(a) for a in answers))

    class Sock:
        def __init__(self):
            self.inbox, self.sent = inbox, b""

        def recv(self, n):
            out, self.inbox = self.inbox[:n], self.inbox[n:]
            return out

        def sendall(self, data):
            self.sent += data

        def messages(self):
            sent, out = self.sent.split(b"\r\n\r\n", 1)[1], []
            while sent:
                n, at = sent[1] & 0x7F, 2
                if n == 126:
                    n, at = int.from_bytes(sent[2:4], "big"), 4
                mask = sent[at:at + 4]
                out.append(json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(sent[at + 4:at + 4 + n]))))
                sent = sent[at + 4 + n:]
            return out
    return Sock()


class FilesTests(unittest.TestCase):
    def test_the_command_is_there(self):
        source = (ROOT / "tools/jarvis-show.py").read_text(encoding="utf-8")
        self.assertIn('"--remove-show"', source)
        self.assertIn('"--reset-show"', source)
        self.assertIn('"--keep-wifi"', source)
        self.assertIn("--yes-remove", source)
        self.assertIn("ui.out.typed(found.name)", source)


if __name__ == "__main__":
    unittest.main()
