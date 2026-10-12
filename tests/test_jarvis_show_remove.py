import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import home_assistant as ha  # noqa: E402
from jarvis_crown import music_assistant as ma  # noqa: E402
from jarvis_crown import remove  # noqa: E402
from jarvis_crown.shows import known_shows, record_show, update_show  # noqa: E402

MAC = "0c:ee:00:00:00:05"
BP = "openjade/turn-room-down.yaml"
SAT = "assist_satellite.genbushow5_assist_satellite"


def entity(entity_id, device="MAIN"):
    return {"entity_id": entity_id, "device_id": device, "config_entry_id": "E1", "platform": "esphome"}


class FakeHA:
    """A house with genbuShow5 in it the way the installer left it, and a little of somebody else's."""

    def __init__(self, *, genbu="1.1.0", mains=("genbushow5",)):
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
        self.genbu = genbu
        self.mains = list(mains)
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
            return {"state": "1.4.0", "attributes": {"main_shows": self.mains}}
        if path.startswith("/api/states/"):
            if path[12:] not in self.states:
                raise ha.NotFound(path)
            return self.states[path[12:]]
        if path == "/api/services":
            services = {"forget_show": {}} if self.genbu == "1.1.0" else {}
            return [{"domain": "esphome", "services": {}}, {"domain": "genbu", "services": services}]
        if path.startswith("/api/config/automation/config/"):
            item = path.rsplit("/", 1)[1]
            if item not in self.automations:
                raise ha.NotFound(path)
            if method == "DELETE":
                return {"result": "ok"} if self.automations.pop(item) else None
            return self.automations[item]
        if method == "DELETE" and path.startswith("/api/config/config_entries/entry/"):
            entry_id = path.rsplit("/", 1)[1]
            self.entries = [e for e in self.entries if e["entry_id"] != entry_id]
            self.devices = [d for d in self.devices if d["config_entry_id"] != entry_id]
            self.entities = [e for e in self.entities if e["config_entry_id"] != entry_id]
            return {"require_restart": False}
        if path == "/api/services/genbu/forget_show?return_response":
            was = list(self.mains)
            self.mains = [m for m in self.mains if m != body["node"]] or (["livingroomecho8"] if was else [])
            new = self.mains[0] if body["node"] in was and len(was) == 1 else None
            return {"changed_states": [], "service_response": {"mains": self.mains, "new_main": new}}
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
        self.assertTrue(found.main and found.forget_action)
        # somebody else's automation and script, and the dashboard, are listed and left alone
        self.assertEqual(found.uses, ["automation.mine", "dashboard lovelace", "script.wake"])

    def test_the_other_show_is_not_touched(self):
        found = remove.find(FakeHA(), "genbuShow5")
        self.assertNotIn("U2", [u["id"] for u in found.users])
        self.assertNotIn("techo5_turn_room_down_livingroomecho8", [i for i, _ in found.automations])

    def test_an_owner_named_like_the_show_is_never_taken(self):
        house = FakeHA()
        house.users.append({"id": "U9", "username": "show_old", "is_owner": True})
        found = remove.find(house, "genbuShow5", also=["old"])
        self.assertEqual([u["id"] for u in found.users], ["U1"])
        self.assertEqual(found.skipped_users, ["show_old"])

    def test_a_second_run_finds_what_is_left_by_name_and_kept_mac(self):
        house, music = FakeHA(mains=()), Music()
        house.entries = house.entries[1:]
        found = remove.find(house, "genbuShow5", mac=MAC, music=music.find)
        self.assertIsNone(found.entry)
        self.assertEqual(found.node, "genbushow5")
        self.assertEqual([item for item, _ in found.automations], ["techo5_turn_room_down_genbushow5"])
        self.assertEqual(music.asked, [(["genbuShow5"], MAC)])

    def test_nothing_left_is_nothing(self):
        house = FakeHA(mains=())
        house.entries, house.automations, house.users = [], {}, []
        found = remove.find(house, "genbuShow5", music=Music(players=()).find)
        found.music_users = []
        self.assertFalse(found.anything)

    def test_music_that_could_not_look_is_said_so(self):
        found = remove.find(FakeHA(), "genbuShow5", music=lambda names, mac: None)
        self.assertFalse(found.music_asked)
        stays = remove.plan(found)[2]
        self.assertTrue(any("not looked at" in s for s in stays))


class PlanTests(unittest.TestCase):
    def test_a_connected_player_waits_for_a_later_run(self):
        found = remove.find(FakeHA(), "genbuShow5", music=Music().find)
        goes, later, stays = remove.plan(found)
        self.assertIn("automation techo5_turn_room_down_genbushow5", goes)
        self.assertIn("Home Assistant user 'show_genbushow5'", goes)
        self.assertIn("Music Assistant user 'genbushow5'", goes)
        self.assertTrue(any(g.startswith("ESPHome entry 'genbuShow5' with its 2 devices") for g in goes))
        self.assertTrue(any("place in Genbu (main update Show" in g for g in goes))
        self.assertEqual(len(later), 1)
        self.assertIn("run remove-show again", later[0])
        self.assertTrue(any(s.startswith("automation.mine uses it") for s in stays))

    def test_an_offline_player_goes_now(self):
        found = remove.find(FakeHA(), "genbuShow5", music=Music(players=[dict(PLAYER, available=False)]).find)
        goes, later, _ = remove.plan(found)
        self.assertFalse(later)
        self.assertTrue(any(g.startswith("Music Assistant player 'Jarvis Show 5'") for g in goes))

    def test_an_older_genbu_is_left_with_a_hint(self):
        found = remove.find(FakeHA(genbu="1.0.0"), "genbuShow5")
        goes, _, stays = remove.plan(found)
        self.assertFalse(any("Genbu" in g for g in goes))
        self.assertTrue(any("older than 1.1.0" in s and "select.genbu_main_update_show" in s for s in stays))


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
        self.assertTrue(any("livingroomecho8 is the main update Show now" in line for line in lines))

    def test_a_connected_player_is_not_sent(self):
        house, music = FakeHA(), Music()
        found = remove.find(house, "genbuShow5", music=music.find)
        remove.remove(house, found, keep=kept()[1], drop=music.drop, progress=lambda _: None)
        self.assertEqual(music.dropped, [(["m1"], [])])

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
        self.assertTrue(any(line.startswith("WARN: genbushow5 is still a main update Show") for line in lines))

    def test_run_twice_the_second_finds_only_the_player(self):
        house, music = FakeHA(), Music()
        remove.remove(house, remove.find(house, "genbuShow5", music=music.find), keep=kept()[1],
                      progress=lambda _: None)
        music.users = []
        music.players[0]["available"] = False
        again = remove.find(house, "genbuShow5", mac=MAC, music=music.find)
        goes, later, _ = remove.plan(again)
        self.assertEqual(len(goes), 1)
        self.assertTrue(goes[0].startswith("Music Assistant player"))
        self.assertFalse(later)


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
        self.assertIn('"remove-show"', source)
        self.assertIn("--yes-remove", source)
        self.assertIn("ui.out.typed(found.name)", source)


if __name__ == "__main__":
    unittest.main()
