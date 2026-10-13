import argparse
import contextlib
import io
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from jarvis_crown import home_assistant as ha  # noqa: E402
from jarvis_crown import music_assistant as ma  # noqa: E402
from jarvis_crown import ui  # noqa: E402
from jarvis_crown import settings as settings_mod  # noqa: E402
from jarvis_crown.settings import Asker, Settings, SettingsError, gather, load_defaults, save_defaults, secret_files  # noqa: E402
from jarvis_crown.sign_ins import SignIns  # noqa: E402
from jarvis_crown.shows import record_show, show_named  # noqa: E402
from techo5lib import Fail  # noqa: E402

SPEC = importlib.util.spec_from_file_location("jarvis_install_show_settings", TOOLS / "install-show.py")
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)

PSK = "c2VjcmV0LWtleS1mb3ItdGVzdHMtb25seS0zMmJ5dGVzIQ=="
TOKEN = "a.b.c-token"
DASH_KEY = "0123456789abcdef0123"

_RUN_DIR = tempfile.TemporaryDirectory()


def setUpModule():
    # No test signs in anywhere or writes next to the real sign-ins of this computer.
    patcher = mock.patch.object(settings_mod, "RUN", SignIns(lambda: pathlib.Path(_RUN_DIR.name) / "sign-ins.json"))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(_RUN_DIR.cleanup)


class AddressTests(unittest.TestCase):
    def test_dashcast_matches_the_daemon(self):
        cases = {
            "10.0.0.30": "10.0.0.30:9555",
            "http://10.0.0.30:9555/": "10.0.0.30:9555",
            "dash.example:80": "dash.example:80",
            "user@dash.example": "dash.example:9555",
            "[fd00::1]:9000": "[fd00::1]:9000",
            "fd00::1": "[fd00::1]:9555",
            "": "",
        }
        for given, want in cases.items():
            self.assertEqual(ha.normalize_dashcast(given), want, given)
        for bad in ("dash example", "-x:1", "host:0", "host:99999", "host:abc"):
            with self.assertRaises(ValueError, msg=bad):
                ha.normalize_dashcast(bad)

    def test_music_assistant_is_one_ip(self):
        self.assertEqual(ha.normalize_music_assistant("10.0.0.2"), "10.0.0.2")
        self.assertEqual(ha.normalize_music_assistant("http://10.0.0.2:8095/"), "10.0.0.2")
        self.assertEqual(ha.normalize_music_assistant("[fd00::2]:8095"), "fd00::2")
        with self.assertRaises(ValueError):
            ha.normalize_music_assistant("ma.local")
        self.assertEqual(ha.normalize_music_assistant("ma.local", lambda h: "10.1.2.3"), "10.1.2.3")
        with self.assertRaises(ValueError):
            ha.normalize_music_assistant("ma.local", lambda h: "not-an-ip")

    def test_ha_url_is_scheme_host_port(self):
        self.assertEqual(ha.normalize_ha_url("https://HA.example/"), "https://HA.example")
        self.assertEqual(ha.normalize_ha_url("http://10.0.0.2:8123"), "http://10.0.0.2:8123")
        self.assertEqual(ha.normalize_ha_url("haos.example.org"), "https://haos.example.org")
        self.assertEqual(ha.normalize_ha_url("10.0.0.2"), "http://10.0.0.2:8123")
        self.assertEqual(ha.normalize_ha_url("10.0.0.2:8124"), "http://10.0.0.2:8124")
        self.assertEqual(ha.normalize_ha_url("homeassistant.local"), "http://homeassistant.local:8123")
        for bad in ("ftp://ha", "http://u:p@ha", "http://ha/lovelace", "haos.example.org/lovelace"):
            with self.assertRaises(ValueError, msg=bad):
                ha.normalize_ha_url(bad)

    def test_names(self):
        self.assertEqual(ha.node_slug("Jarvis Show 5"), "jarvis-show-5")
        self.assertEqual(ha.node_slug("  Küche / Echo!! "), "k-che-echo")
        self.assertEqual(ha.service_prefix("Jarvis Show 5"), "jarvis_show_5")


class FakeHA:
    """Home Assistant's REST answers for one ESPHome device, from nothing to fully set up."""

    def __init__(self, *, flow_errors=(), allowed=False, entry=False, unconfigured=0):
        self.calls = []
        self.node = "jarvis-show-5"  # the ESPHome node name the diagnostics give; None: they say nothing
        self.unconfigured = unconfigured  # times the wake word select still reads unavailable
        self.flow_errors = list(flow_errors)
        self.allowed = allowed
        self.show_answers = True  # the Show's home_assistant action says it stored the token
        self.show_fails = ""      # what the Show's home_assistant action fails with
        self.show_says = {"stored": True}  # its answer
        self.entries = [{"entry_id": "E1", "domain": "esphome", "title": "Jarvis Show 5"}] if entry else []
        self.areas = {"living_room": "Wohnzimmer", "kitchen": "Küche"}
        self.area = ""
        self.ws_calls = []
        self.pipelines = None
        self.boards = []      # lovelace/dashboards/list
        self.labels = None    # config/label_registry/list; None: this fake knows no labels
        self.views = {}       # url_path (None for the default) -> its views
        self.devices = [{"id": "D1", "area_id": None}, {"id": "D2", "area_id": "living_room"}]
        self.entities = [
            {"entity_id": "climate.wohnzimmer", "device_id": "D2", "area_id": None},
            {"entity_id": "sensor.wohnzimmer_temperature", "device_id": "D2", "area_id": None},
            {"entity_id": "sensor.wohnzimmer_battery", "device_id": "D2", "area_id": None,
             "entity_category": "diagnostic"},
            {"entity_id": "light.decke", "device_id": None, "area_id": "living_room"},
            {"entity_id": "light.versteckt", "device_id": None, "area_id": "living_room", "hidden_by": "user"},
            {"entity_id": "binary_sensor.fenster", "device_id": None, "area_id": "living_room"},
            {"entity_id": "media_player.wohnzimmer", "device_id": None, "area_id": "living_room"},
            {"entity_id": "switch.stehlampe", "device_id": None, "area_id": "living_room"},
            {"entity_id": "switch.kaffee", "device_id": None, "area_id": "kitchen"},
            {"entity_id": "light.kueche", "device_id": None, "area_id": "kitchen"},
            {"entity_id": "light.jarvis_show_5_screen", "device_id": "D1", "area_id": None},
        ]
        self.classes = {"sensor.wohnzimmer_temperature": "temperature", "binary_sensor.fenster": "window"}
        self.units = {"sensor.wohnzimmer_temperature": "°C"}
        self.genbu_cards = False  # Genbu 1.3.1 or newer, serving the dashboard cards
        self.resources = []       # lovelace/resources
        self.states = {
            "select.jarvis_show_5_assistant": {"state": "preferred", "attributes": {"options": ["preferred", "Jarvis"]}},
            "select.jarvis_show_5_wake_word": {"state": "Okay Nabu", "attributes": {"options": ["Okay Nabu", "Hey Jarvis"]}},
            "switch.jarvis_show_5_playback_sendspin": {"state": "off"},
        }

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/genbu_panel/"):
            if not self.genbu_cards:
                raise ha.NotFound(f"GET {path}: HTTP 404")
            return "customElements.define(...)"
        if method == "GET" and path == "/api/diagnostics/config_entry/E1":
            if self.node is None:
                raise ha.NotFound("GET /api/diagnostics/config_entry/E1: HTTP 404")
            return {"home_assistant": {}, "data": {"config": {"data": {"device_name": self.node,
                                                                        "noise_psk": "**REDACTED**"}}}}
        if path.startswith("/api/config/config_entries/entry"):
            return list(self.entries)
        if method == "GET" and path == "/api/config/config_entries/flow":
            raise ha.HomeAssistantError("GET /api/config/config_entries/flow: HTTP 405")
        if method == "POST" and path == "/api/config/config_entries/flow":
            return {"type": "form", "flow_id": "F1", "step_id": "user"}
        if method == "POST" and path.startswith("/api/config/config_entries/flow/"):
            flow = path.rsplit("/", 1)[1]
            if self.flow_errors:
                return {"type": "form", "flow_id": flow, "step_id": "user", "errors": {"base": self.flow_errors.pop(0)}}
            if "noise_psk" in body:
                self.entries = [{"entry_id": "E1", "domain": "esphome", "title": "Jarvis Show 5"}]
                return {"type": "create_entry", "result": {"entry_id": "E1"}}
            return {"type": "form", "flow_id": flow, "step_id": "encryption_key"}
        if method == "DELETE":
            return None
        if path == "/api/config/config_entries/options/flow":
            return {"type": "form", "flow_id": "O1", "data_schema": [
                {"name": "allow_service_calls", "default": self.allowed},
                {"name": "subscribe_logs", "default": False}]}
        if path == "/api/config/config_entries/options/flow/O1":
            self.allowed = body["allow_service_calls"]
            return {"type": "create_entry"}
        if path == "/api/template" and "areas()" in body["template"]:
            return "".join(f"{a}\t{n}\n" for a, n in self.areas.items())
        if path == "/api/template" and "device_id(" in body["template"]:
            return f"D1\t{self.area or 'None'}"
        if path == "/api/template" and "area_entities('living_room')" in body["template"]:
            return "\n".join(e["entity_id"] for e in self.entities
                             if e["area_id"] == "living_room" or e["device_id"] == "D2")
        if path == "/api/template":
            return "\n".join([e for e in self.states if ".jarvis_show_5_" in e] + ["select.jarvis_show_5_assistant_2"])
        if path == "/api/services" and method == "GET":
            prefix = ha.service_prefix(self.node or "Jarvis Show 5")
            names = [f"{prefix}_{action}" for action in
                     ("dashboard_server", "home_assistant", "sendspin_server", "music_assistant")]
            return [{"domain": "light", "services": {}}, {"domain": "esphome", "services": {n: {} for n in names}}]
        if path == "/api/states" and method == "GET":
            return [{"entity_id": e["entity_id"], "state": "on",
                     "attributes": {"device_class": self.classes.get(e["entity_id"]),
                                    "unit_of_measurement": self.units.get(e["entity_id"])}} for e in self.entities]
        if path.startswith("/api/states/"):
            entity = path[len("/api/states/"):]
            if self.unconfigured and entity.endswith("_wake_word"):
                self.unconfigured -= 1
                return {"state": "unavailable", "attributes": {"options": ["no_wake_word"]}}
            return self.states.get(entity)
        if path == "/api/services/select/select_option":
            self.states[body["entity_id"]]["state"] = body["option"]
            return []
        if path == "/api/services/switch/turn_on":
            self.states[body["entity_id"]]["state"] = "on"
            return []
        if path.startswith("/api/services/esphome/"):
            if not path.endswith("?return_response"):
                return []
            # Before GENBU-158 no action of the Show's answered; since then home_assistant does.
            if not (self.show_answers and path.endswith("_home_assistant?return_response")):
                raise ha.HomeAssistantError(f"POST {path}: HTTP 400 {{\"message\": \"Service does not support "
                                            "responses. Remove return_response from request.\"}")
            if self.show_fails:
                raise ha.HomeAssistantError(f"POST {path}: HTTP 500 {{\"message\": \"{self.show_fails}\"}}")
            return {"changed_states": [], "service_response": self.show_says}
        raise AssertionError(f"unexpected {method} {path}")

    def ws(self, message):
        if message["type"] == "assist_pipeline/pipeline/list":
            if self.pipelines is None:
                raise ha.HomeAssistantError("unknown command")
            return self.pipelines
        if message["type"] == "lovelace/dashboards/list":
            return list(self.boards)
        if message["type"] == "lovelace/resources":
            return list(self.resources)
        if message["type"] == "lovelace/config":
            if message["url_path"] not in self.views:
                raise ha.HomeAssistantError("lovelace/config: config_not_found")
            return {"views": self.views[message["url_path"]]}
        if message["type"] == "config/device_registry/list":
            return [{**d, "area_id": self.area or None} if d["id"] == "D1" else d for d in self.devices]
        if message["type"] == "config/entity_registry/list":
            return list(self.entities)
        if message["type"] == "config/label_registry/list" and self.labels is not None:
            return list(self.labels)
        self.ws_calls.append(message)
        if message["type"] == "config/label_registry/create" and self.labels is not None:
            made = {"label_id": message["name"].lower().replace(" ", "_").replace(":", ""), "name": message["name"]}
            self.labels.append(made)
            return made
        if message["type"] == "config/device_registry/update":
            if "area_id" in message:
                self.area = message["area_id"]
            if "labels" in message:
                next(d for d in self.devices if d["id"] == message["device_id"])["labels"] = message["labels"]
        return {}

    def posted(self, prefix):
        """What was posted under prefix, by path without its query."""
        return [(p.split("?")[0], b) for m, p, b in self.calls if m == "POST" and p.startswith(prefix)]


def run_deploy(fake, choose=None, login=None, **kw):
    settings = ha.DeviceSettings(dashcast="10.0.0.5:9555", dashcast_key=DASH_KEY, ha_url="http://ha:8123",
                                 ha_token=TOKEN, music_assistant="10.0.0.6", music_assistant_login=login)
    opts = ha.DeployOptions(name="Jarvis Show 5", psk=PSK, settings=settings, **kw)
    log = []
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds
    entry = ha.deploy(fake, opts, progress=log.append, sleep=sleep, clock=lambda: now[0], choose=choose)
    return entry, log


class DeployTests(unittest.TestCase):
    def test_new_show_by_address(self):
        fake = FakeHA()
        entry, log = run_deploy(fake, host="10.0.0.9", assistant="Jarvis", wake_word="Hey Jarvis")
        self.assertEqual(entry, "E1")
        self.assertIn(("/api/config/config_entries/flow/F1", {"host": "10.0.0.9", "port": 6053}),
                      fake.posted("/api/config/config_entries/flow/F1"))
        self.assertIn(("/api/config/config_entries/flow/F1", {"noise_psk": PSK}),
                      fake.posted("/api/config/config_entries/flow/F1"))
        self.assertTrue(fake.allowed)
        self.assertEqual(fake.posted("/api/config/config_entries/options/flow/O1")[0][1],
                         {"allow_service_calls": True, "subscribe_logs": False})
        self.assertEqual(fake.states["select.jarvis_show_5_assistant"]["state"], "Jarvis")
        self.assertEqual(fake.states["select.jarvis_show_5_wake_word"]["state"], "Hey Jarvis")
        self.assertEqual(fake.states["switch.jarvis_show_5_playback_sendspin"]["state"], "on")
        actions = dict(fake.posted("/api/services/esphome/"))
        self.assertEqual(actions["/api/services/esphome/jarvis_show_5_dashboard_server"],
                         {"address": "10.0.0.5:9555", "key": DASH_KEY})
        self.assertEqual(actions["/api/services/esphome/jarvis_show_5_home_assistant"],
                         {"url": "http://ha:8123", "token": TOKEN})
        self.assertEqual(actions["/api/services/esphome/jarvis_show_5_sendspin_server"], {"ip": "10.0.0.6"})
        for line in log:
            self.assertNotIn(PSK, line)
            self.assertNotIn(TOKEN, line)
            self.assertNotIn(DASH_KEY, line)

    def test_the_shows_devices_get_both_labels(self):
        fake = FakeHA()
        fake.labels = []
        fake.devices[0]["config_entries"] = ["E1"]
        fake.devices[1]["config_entries"] = ["E9"]
        fake.node = "genbushow5"
        _, log = run_deploy(fake, host="10.0.0.9")
        self.assertEqual([x["name"] for x in fake.labels], ["Genbu Show installed", "Genbu Show: genbushow5"])
        self.assertEqual(fake.devices[0]["labels"], ["genbu_show_installed", "genbu_show_genbushow5"])
        self.assertNotIn("labels", fake.devices[1])
        self.assertFalse([line for line in log if "label" in line])

    def test_a_house_that_does_not_take_labels_still_gets_the_show(self):
        fake = FakeHA()
        fake.devices[0]["config_entries"] = ["E1"]
        entry, log = run_deploy(fake, host="10.0.0.9")
        self.assertEqual(entry, "E1")
        self.assertTrue(any(line.startswith("WARN: Home Assistant did not take the labels") for line in log))

    def test_a_renamed_show_is_reached_by_the_name_home_assistant_has(self):
        fake, names = FakeHA(), []
        fake.node = "genbushow5"
        _, log = run_deploy(fake, host="10.0.0.9", login=lambda name: names.append(name) or "MA-SHOW-TOKEN")
        actions = dict(fake.posted("/api/services/esphome/"))
        self.assertEqual(actions["/api/services/esphome/genbushow5_sendspin_server"], {"ip": "10.0.0.6"})
        self.assertEqual(actions["/api/services/esphome/genbushow5_music_assistant"], {"token": "MA-SHOW-TOKEN"})
        self.assertEqual(names, ["genbushow5"])
        self.assertTrue(any("knows the Show as 'genbushow5'" in line for line in log))
        self.assertFalse(any(line.startswith("WARN") for line in log))

    def test_a_renamed_show_keeps_its_old_entity_ids_unless_told(self):
        fake = FakeHA()
        fake.node = "genbushow5"
        with mock.patch("jarvis_crown.rename.follow") as follow:
            _, log = run_deploy(fake, host="10.0.0.9")
        follow.assert_not_called()
        self.assertIn("its entities keep the name 'Jarvis Show 5'", log)

    def test_a_renamed_show_can_take_its_entities_to_the_new_name(self):
        fake, renamed = FakeHA(), []
        fake.node = "genbushow5"
        with mock.patch("jarvis_crown.rename.follow", return_value=3) as follow, \
                mock.patch("jarvis_crown.voice_kit.set_up") as voice:
            run_deploy(fake, host="10.0.0.9", rename_entities=True, renamed=renamed.append, voice_extras=True)
        follow.assert_called_once()
        self.assertEqual(follow.call_args.args[1:], ("E1", "Jarvis Show 5", "genbushow5"))
        self.assertEqual(renamed, ["genbushow5"])
        self.assertEqual(voice.call_args.args[1], "genbushow5")  # the rest of the run goes by the new name

    def test_without_diagnostics_the_installed_name_is_used(self):
        fake = FakeHA()
        fake.node = None
        run_deploy(fake, host="10.0.0.9")
        self.assertIn("/api/services/esphome/jarvis_show_5_sendspin_server", dict(fake.posted("/api/services/esphome/")))

    def test_the_wait_says_what_it_waits_for(self):
        fake = FakeHA()
        fake.node = "somewhere-else"
        services = fake.request

        def request(method, path, body=None):
            if path == "/api/services" and method == "GET":
                return [{"domain": "esphome", "services": {}}]
            return services(method, path, body)
        fake.request = request
        _, log = run_deploy(fake, host="10.0.0.9", wait_seconds=30)
        waits = [line for line in log if line.startswith("waiting for the Show")]
        self.assertEqual(len(waits), 1)
        self.assertIn("its actions (dashboard_server, home_assistant, sendspin_server)", waits[0])
        self.assertIn("up to 30 s more", waits[0])
        self.assertTrue(any("run this again once the Show is online" in line for line in log))

    def test_music_assistant_token_made_for_the_show_by_name(self):
        fake, names = FakeHA(), []
        run_deploy(fake, host="10.0.0.9", login=lambda name: names.append(name) or "MA-SHOW-TOKEN")
        self.assertEqual(names, ["jarvis-show-5"])
        self.assertEqual(dict(fake.posted("/api/services/esphome/"))
                         ["/api/services/esphome/jarvis_show_5_music_assistant"], {"token": "MA-SHOW-TOKEN"})
        fake = FakeHA()
        _, log = run_deploy(fake, host="10.0.0.9", login=lambda name: None)
        self.assertNotIn("/api/services/esphome/jarvis_show_5_music_assistant",
                         dict(fake.posted("/api/services/esphome/")))
        self.assertTrue(any("lyrics won't work" in line for line in log))
        fake = FakeHA()
        run_deploy(fake, host="10.0.0.9")
        self.assertNotIn("/api/services/esphome/jarvis_show_5_music_assistant",
                         dict(fake.posted("/api/services/esphome/")))

    def test_show_without_an_address_is_found_by_its_local_name(self):
        fake = FakeHA()
        entry, _ = run_deploy(fake)
        self.assertEqual(entry, "E1")
        self.assertIn(("/api/config/config_entries/flow/F1", {"host": "jarvis-show-5.local", "port": 6053}),
                      fake.posted("/api/config/config_entries/flow/F1"))

    def test_nothing_chosen_leaves_the_selects_alone(self):
        fake = FakeHA()
        _, log = run_deploy(fake, host="10.0.0.9")
        self.assertFalse(fake.posted("/api/services/select/"))
        self.assertEqual(fake.states["select.jarvis_show_5_wake_word"]["state"], "Okay Nabu")

    def test_chooser_picks_from_what_is_really_there(self):
        fake = FakeHA()
        offered = {}

        def choose(label, current, options, names=None):
            offered[label] = (current, options)
            return options[-1] if label == "assistant" else None
        _, log = run_deploy(fake, choose=choose, host="10.0.0.9")
        self.assertEqual(offered, {"assistant": ("preferred", ["preferred", "Jarvis"]),
                                   "wake word": ("Okay Nabu", ["Okay Nabu", "Hey Jarvis"]),
                                   "room": ("", ["Küche", "Wohnzimmer"]),
                                   "voice extras": ("", ["yes", "no"])})
        self.assertEqual(fake.states["select.jarvis_show_5_assistant"]["state"], "Jarvis")
        self.assertEqual(fake.states["select.jarvis_show_5_wake_word"]["state"], "Okay Nabu")
        self.assertTrue(any("left at Okay Nabu" in line for line in log))

    def test_one_assistant_that_is_the_default_is_not_asked_about(self):
        fake = FakeHA()
        fake.pipelines = {"pipelines": [{"id": "p1", "name": "Jarvis"}], "preferred_pipeline": "p1"}
        asked = []
        _, log = run_deploy(fake, choose=lambda label, *_: asked.append(label), host="10.0.0.9")
        self.assertNotIn("assistant", asked)
        self.assertTrue(any("Jarvis is the only assistant" in line for line in log))
        self.assertEqual(fake.states["select.jarvis_show_5_assistant"]["state"], "preferred")

    def test_the_default_assistant_is_marked(self):
        fake = FakeHA()
        fake.states["select.jarvis_show_5_assistant"]["attributes"]["options"] = ["preferred", "Jarvis", "Basic"]
        fake.pipelines = {"pipelines": [{"id": "p1", "name": "Jarvis"}, {"id": "p2", "name": "Basic"}],
                          "preferred_pipeline": "p1"}
        shown = {}

        def choose(label, current, options, names=None):
            shown[label] = names
            return None
        run_deploy(fake, choose=choose, host="10.0.0.9")
        self.assertEqual(shown["assistant"]["Jarvis"], "Jarvis (default)")
        self.assertIn("now Jarvis", shown["assistant"]["preferred"])
        self.assertNotIn("Basic", shown["assistant"])

    def two_assistants(self, show_on="Jarvis"):
        """A house with two pipelines, a Show and a voice satellite on Jarvis, and a third device on the other."""
        fake = FakeHA()
        jarvis = {"id": "p1", "name": "Jarvis", "language": "de", "conversation_engine": "conversation.jarvis",
                  "conversation_language": "de", "stt_engine": "stt.faster_whisper", "stt_language": "de",
                  "tts_engine": "tts.piper", "tts_language": "de", "tts_voice": "thorsten", "wake_word_entity": None,
                  "wake_word_id": None, "prefer_local_intents": True}
        fake.pipelines = {"pipelines": [jarvis, {"id": "p2", "name": "Basic"}], "preferred_pipeline": "p1"}
        options = {"options": ["preferred", "Basic", "Jarvis"]}
        fake.states["select.jarvis_show_5_assistant"] = {"state": show_on, "attributes": options}
        fake.states["select.kitchen_pe_assistant"] = {"state": "Jarvis", "attributes": options}
        fake.states["select.office_pe_assistant"] = {"state": "Basic", "attributes": options}
        fake.entities += [
            {"entity_id": "select.jarvis_show_5_assistant", "unique_id": "aa:bb-pipeline", "device_id": "D1"},
            {"entity_id": "select.kitchen_pe_assistant", "unique_id": "cc:dd-pipeline", "device_id": None},
            {"entity_id": "select.office_pe_assistant", "unique_id": "ee:ff-pipeline_2", "device_id": None},
            {"entity_id": "select.kitchen_pe_wake_word", "unique_id": "cc:dd-wake_word", "device_id": None},
        ]
        fake.states["select.kitchen_pe_wake_word"] = {"state": "Jarvis", "attributes": {"options": ["Jarvis"]}}
        return fake, jarvis

    def test_the_assistant_is_renamed_and_every_device_on_it_follows(self):
        fake, jarvis = self.two_assistants()
        kept = {}

        def keep(what, config):
            kept[what] = config
            return "/backups/" + what
        _, log = run_deploy(fake, host="10.0.0.9", assistant_name=True, keep_replaced=keep)
        update = [m for m in fake.ws_calls if m["type"] == "assist_pipeline/pipeline/update"]
        self.assertEqual(update, [{"type": "assist_pipeline/pipeline/update", "pipeline_id": "p1",
                                   **{k: v for k, v in jarvis.items() if k != "id"}, "name": "Genbu Assistant"}])
        # Saved before it changed, with the devices that were on it.
        self.assertEqual(kept["assist-pipeline-Jarvis"]["pipeline"]["name"], "Jarvis")
        self.assertEqual(kept["assist-pipeline-Jarvis"]["selects_on_it"],
                         ["select.jarvis_show_5_assistant", "select.kitchen_pe_assistant"])
        self.assertEqual(fake.states["select.jarvis_show_5_assistant"]["state"], "Genbu Assistant")
        self.assertEqual(fake.states["select.kitchen_pe_assistant"]["state"], "Genbu Assistant")
        # Not a pipeline select, and one on another assistant: both left alone.
        self.assertEqual(fake.states["select.kitchen_pe_wake_word"]["state"], "Jarvis")
        self.assertEqual(fake.states["select.office_pe_assistant"]["state"], "Basic")
        self.assertTrue(any("Jarvis is now called Genbu Assistant" in line and "/backups/" in line for line in log))

    def test_a_show_on_preferred_renames_the_preferred_one(self):
        fake, _ = self.two_assistants(show_on="preferred")
        _, log = run_deploy(fake, host="10.0.0.9", assistant_name=True, keep_replaced=lambda *_: "x")
        update = [m for m in fake.ws_calls if m["type"] == "assist_pipeline/pipeline/update"]
        self.assertEqual([(m["pipeline_id"], m["name"]) for m in update], [("p1", "Genbu Assistant")])
        self.assertEqual(fake.states["select.jarvis_show_5_assistant"]["state"], "preferred")
        self.assertEqual(fake.states["select.kitchen_pe_assistant"]["state"], "Genbu Assistant")

    def test_the_assistant_keeps_its_name_unless_asked(self):
        for kw, said in (({"keep_replaced": lambda *_: "x"}, "keeps the name Jarvis"),
                         ({"assistant_name": True}, "nowhere to save it first"),
                         ({"assistant_name": False, "keep_replaced": lambda *_: "x"}, None)):
            fake, _ = self.two_assistants()
            asked = []

            def choose(label, current, options, names=None):
                asked.append(label)
                return None  # Enter at every question
            _, log = run_deploy(fake, host="10.0.0.9", choose=choose if "assistant_name" not in kw else None, **kw)
            self.assertFalse([m for m in fake.ws_calls if m["type"] == "assist_pipeline/pipeline/update"], kw)
            self.assertEqual(fake.states["select.kitchen_pe_assistant"]["state"], "Jarvis")
            if said:
                self.assertTrue(any(said in line for line in log), (kw, log))
            else:  # --no-genbu-assistant: not even looked at
                self.assertFalse(any("Genbu Assistant" in line or "keeps the name" in line for line in log), log)

    def test_the_name_is_not_taken_twice(self):
        fake, _ = self.two_assistants()
        fake.pipelines["pipelines"][1]["name"] = "Genbu Assistant"
        _, log = run_deploy(fake, host="10.0.0.9", assistant_name=True, keep_replaced=lambda *_: "x")
        self.assertFalse([m for m in fake.ws_calls if m["type"] == "assist_pipeline/pipeline/update"])
        self.assertTrue(any("another pipeline is already called Genbu Assistant" in line for line in log))
        fake, _ = self.two_assistants(show_on="Basic")
        fake.pipelines["pipelines"][1]["name"] = "Genbu Assistant"
        fake.states["select.jarvis_show_5_assistant"]["state"] = "Genbu Assistant"
        asked = []
        run_deploy(fake, host="10.0.0.9", choose=lambda label, *_: asked.append(label), keep_replaced=lambda *_: "x")
        self.assertNotIn("assistant name", asked)

    def test_enter_keeps_the_assistant_name(self):
        args = argparse.Namespace(assistant=None, wake_word=None, room=None, room_dashboard=None, voice_extras=None,
                                  calendars=None, no_questions=False, genbu_assistant=None)
        asker = Asker(interactive=True, ask=lambda _: "", say=lambda _: None)
        with mock.patch.object(show_cli, "_asker", return_value=asker):
            self.assertEqual(show_cli._chooser(args)("assistant name", "", ["yes", "no"], {}), "no")
            args.genbu_assistant = True
            self.assertIsNone(show_cli._chooser(args)("assistant name", "", ["yes", "no"], {}))
        with mock.patch.object(sys, "argv", ["jarvis-show.py", "home-assistant", "--name", "x", "--genbu-assistant"]):
            self.assertIs(show_cli.parse_args().genbu_assistant, True)

    def test_the_installer_shows_what_home_assistant_names(self):
        args = argparse.Namespace(assistant=None, wake_word=None, room=None, room_dashboard=None, voice_extras=None,
                                  calendars=None, no_questions=False)
        said, prompts = [], []
        asker = Asker(interactive=True, ask=lambda q: prompts.append(q) or "", say=said.append)
        with mock.patch.object(show_cli, "_asker", return_value=asker):
            picked = show_cli._chooser(args)("assistant", "preferred", ["preferred", "Jarvis", "Basic"],
                                                {"Jarvis": "Jarvis (default)"})
        # Enter leaves it as it is: no assistant is picked by its name, whatever this house calls its own.
        self.assertIsNone(picked)
        self.assertIn("  │   2  Jarvis (default)", said)
        self.assertTrue(any("Enter leaves it as it is" in q for q in prompts))

    def test_the_rename_switch_answers_the_new_name_question(self):
        with mock.patch.object(sys, "argv", ["jarvis-show.py", "home-assistant", "--name", "Jarvis Show 5",
                                             "--rename-entities"]):
            args = show_cli.parse_args()
        self.assertIs(args.rename_entities, True)
        with mock.patch.object(sys, "argv", ["jarvis-show.py", "home-assistant", "--name", "x"]):
            self.assertIsNone(show_cli.parse_args().rename_entities)
        asker = Asker(interactive=True, ask=lambda _: self.fail("asked"), say=lambda _: None)
        with mock.patch.object(show_cli, "_asker", return_value=asker):
            self.assertIsNone(show_cli._chooser(args)("new name", "", ["yes", "no"], {}))

    def test_known_show_is_only_brought_up_to_date(self):
        fake = FakeHA(entry=True, allowed=True)
        fake.states["select.jarvis_show_5_wake_word"]["state"] = "Hey Jarvis"
        _, log = run_deploy(fake, wake_word="Hey Jarvis")
        self.assertFalse(fake.posted("/api/config/config_entries/flow"))
        self.assertFalse(fake.posted("/api/config/config_entries/options/flow/O1"))
        self.assertIn(("DELETE", "/api/config/config_entries/options/flow/O1", None), fake.calls)
        self.assertFalse([p for p, b in fake.posted("/api/services/select/") if b["entity_id"].endswith("_wake_word")])
        self.assertTrue(any("already" in line for line in log))

    def test_waits_while_the_show_is_not_reachable_yet(self):
        fake = FakeHA(flow_errors=["cannot_connect", "cannot_connect"])
        entry, log = run_deploy(fake, host="10.0.0.9")
        self.assertEqual(entry, "E1")
        self.assertEqual(sum(1 for line in log if line.startswith("waiting")), 1)

    def test_gives_up_at_the_deadline(self):
        fake = FakeHA(flow_errors=["cannot_connect"] * 100)
        with self.assertRaisesRegex(ha.HomeAssistantError, "gave up"):
            run_deploy(fake, host="10.0.0.9", wait_seconds=30)

    def test_wrong_key_stops_at_once(self):
        fake = FakeHA(flow_errors=["invalid_psk"])
        with self.assertRaisesRegex(ha.HomeAssistantError, "not this unit's key"):
            run_deploy(fake, host="10.0.0.9")

    def test_unknown_step_is_left_to_a_person(self):
        fake = FakeHA()
        real = fake.request

        def request(method, path, body=None):
            if method == "POST" and path == "/api/config/config_entries/flow":
                fake.calls.append((method, path, body))
                return {"type": "form", "flow_id": "F1", "step_id": "name_conflict"}
            return real(method, path, body)
        fake.request = request
        with self.assertRaisesRegex(ha.HomeAssistantError, "name_conflict"):
            run_deploy(fake, host="10.0.0.9")
        self.assertIn(("DELETE", "/api/config/config_entries/flow/F1", None), fake.calls)

    def test_client_never_shows_the_token(self):
        import urllib.error

        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        client = ha.HomeAssistant("http://ha:8123", TOKEN, opener=opener)
        with self.assertRaises(ha.HomeAssistantError) as cm:
            client.request("GET", "/api/")
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertIn("401", str(cm.exception))


class RoomTests(unittest.TestCase):
    def test_the_room_picked_is_where_the_device_goes(self):
        fake = FakeHA()
        offered = {}

        def choose(label, current, options, names=None):
            offered[label] = (current, options)
            return "Wohnzimmer" if label == "room" else None
        _, log = run_deploy(fake, choose=choose, host="10.0.0.9")
        self.assertEqual(offered["room"], ("", ["Küche", "Wohnzimmer"]))
        self.assertEqual(fake.ws_calls, [{"type": "config/device_registry/update", "device_id": "D1",
                                          "area_id": "living_room"}])
        self.assertIn("the Show put in Wohnzimmer", log)

    def test_room_by_switch_and_twice_is_once(self):
        fake = FakeHA()
        run_deploy(fake, host="10.0.0.9", room="wohnzimmer")
        run_deploy(fake, host="10.0.0.9", room="living_room")
        self.assertEqual(len(fake.ws_calls), 1)
        self.assertEqual(fake.area, "living_room")

    def test_enter_and_unknown_rooms_leave_it_alone(self):
        fake = FakeHA()
        _, log = run_deploy(fake, choose=lambda *_: None, host="10.0.0.9")
        self.assertIn("the Show left in no room", log)
        _, log = run_deploy(fake, host="10.0.0.9", room="Dachboden")
        self.assertTrue(any("no room 'Dachboden'" in line for line in log))
        self.assertEqual(fake.ws_calls, [])

    def test_a_room_dashboard_is_made_when_asked_for(self):
        fake = FakeHA()
        offered = {}

        def choose(label, current, options, names=None):
            offered[label] = (options, names)
            return {"room": "Wohnzimmer", "room dashboard": "yes"}.get(label)
        _, log = run_deploy(fake, choose=choose, host="10.0.0.9")
        self.assertEqual(offered["room dashboard"][0], ["yes", "no"])
        self.assertIn("Wohnzimmer", offered["room dashboard"][1]["yes"])
        create, save = fake.ws_calls[1:]
        self.assertEqual(create["type"], "lovelace/dashboards/create")
        self.assertEqual((create["url_path"], create["title"], create["mode"]),
                         ("dashboard-wohnzimmer", "Wohnzimmer", "storage"))
        self.assertEqual(save["type"], "lovelace/config/save")
        cards = save["config"]["views"][0]["sections"][0]["cards"]
        self.assertEqual([c["entity"] for c in cards],
                         ["climate.wohnzimmer", "sensor.wohnzimmer_temperature", "light.decke",
                          "media_player.wohnzimmer", "switch.stehlampe"])
        self.assertEqual(cards[0]["features"], [{"type": "target-temperature"}])
        self.assertEqual(cards[3], {"type": "tile", "entity": "media_player.wohnzimmer"})
        self.assertIn("room dashboard dashboard-wohnzimmer made for Wohnzimmer, 5 cards", log)

    def test_enter_makes_no_room_dashboard(self):
        fake = FakeHA()
        _, log = run_deploy(fake, choose=lambda label, *_: "Wohnzimmer" if label == "room" else None,
                            host="10.0.0.9")
        self.assertEqual([m["type"] for m in fake.ws_calls], ["config/device_registry/update"])
        self.assertIn("no room dashboard for Wohnzimmer", log)

    def test_a_switch_makes_it_or_never_asks(self):
        fake = FakeHA()
        _, log = run_deploy(fake, host="10.0.0.9", room="Wohnzimmer", room_dashboard=True)
        self.assertIn("lovelace/dashboards/create", [m["type"] for m in fake.ws_calls])
        fake = FakeHA()
        asked = []
        run_deploy(fake, choose=lambda label, *_: asked.append(label) or ("Wohnzimmer" if label == "room" else None),
                   host="10.0.0.9", room_dashboard=False)
        self.assertNotIn("room dashboard", asked)

    def test_a_room_with_a_dashboard_keeps_it(self):
        for boards, views, where in (
                ([{"url_path": "living-room", "title": "Unten", "mode": "storage"}], {}, "living-room"),
                ([{"url_path": "dashboard-x", "title": "wohnzimmer", "mode": "storage"}], {}, "dashboard-x"),
                ([], {None: [{"title": "Wohnzimmer", "path": "wz"}]}, "lovelace/wz"),
                ([{"url_path": "dashboard-haus", "title": "Haus", "mode": "storage"}],
                 {"dashboard-haus": [{"title": "Wohnzimmer", "path": "unten"}]}, "dashboard-haus/unten")):
            fake = FakeHA()
            fake.boards, fake.views = boards, views
            asked = []
            _, log = run_deploy(fake, choose=lambda label, *_: asked.append(label) or (
                "Wohnzimmer" if label == "room" else "yes"), host="10.0.0.9", voice_extras=False)
            self.assertNotIn("room dashboard", asked)
            self.assertIn(f"Wohnzimmer has a dashboard already ({where}); the Show goes on to it", log)
            self.assertEqual(len(fake.ws_calls), 1)

    def jarvis_house(self, *, clock=True):
        fake = FakeHA()
        fake.genbu_cards = True
        fake.resources = [{"url": "/hacsfiles/wall-clock-card/wall-clock-card.js", "type": "module"}] if clock else []
        fake.states["assist_satellite.jarvis_show_5_assist_satellite"] = {"state": "idle"}
        fake.entities.append({"entity_id": "sensor.wohnzimmer_humidity", "device_id": "D2", "area_id": None})
        fake.classes["sensor.wohnzimmer_humidity"] = "humidity"
        fake.units["sensor.wohnzimmer_humidity"] = "%"
        fake.entities.append({"entity_id": "weather.forecast_home", "device_id": None, "area_id": None})
        return fake

    def test_the_jarvis_dashboard_is_made_from_the_house(self):
        fake = self.jarvis_house()
        offered = {}

        def choose(label, current, options, names=None):
            offered[label] = options
            return {"room": "Wohnzimmer", "jarvis dashboard": "yes"}.get(label)
        _, log = run_deploy(fake, choose=choose, host="10.0.0.9", voice_extras=False, calendars="")
        self.assertEqual(offered["jarvis dashboard"], ["yes", "no"])
        create, save = [m for m in fake.ws_calls if m.get("url_path") == "jarvis-display"]
        self.assertEqual((create["type"], create["title"]), ("lovelace/dashboards/create", "Jarvis"))
        cards = ha._calendar_cards(save["config"])
        self.assertEqual(cards, [{"type": "custom:jarvis-calendar-card", "entity": "calendar.personal",
                                  "title": "Kalender", "daysAhead": 14}])
        zones = save["config"]["views"][0]["cards"][0]["layout"]["zones"]
        top = [w["card"] for w in zones["top-center"]["widgets"]]
        self.assertEqual(top[0], {"type": "custom:jarvis-room-environment-card", "title": "Wohnzimmer",
                                  "temperature_entity": "sensor.wohnzimmer_temperature",
                                  "humidity_entity": "sensor.wohnzimmer_humidity"})
        self.assertEqual(top[1]["entity"], "weather.forecast_home")
        timer = zones["bottom-center"]["widgets"][0]["card"]
        self.assertEqual(timer, {"type": "custom:jarvis-live-timer-card", "device_id": "D1",
                                 "satellite_id": "assist_satellite.jarvis_show_5_assist_satellite"})
        self.assertIn("the Jarvis dashboard jarvis-display made; on the Show, pick it under Dashboard to show", log)
        # Again: it is there, nothing is asked or made.
        fake.boards = [{"url_path": "jarvis-display", "mode": "storage"}]
        offered.clear()
        _, log = run_deploy(fake, choose=choose, host="10.0.0.9", voice_extras=False, calendars="")
        self.assertNotIn("jarvis dashboard", offered)
        self.assertIn("the Jarvis dashboard is there already (jarvis-display)", log)

    def test_no_jarvis_dashboard_without_its_clock_but_the_recipe_page(self):
        fake = self.jarvis_house(clock=False)
        fake.entities.append({"entity_id": "sensor.jarvis_recipe", "device_id": None, "area_id": None})
        _, log = run_deploy(fake, host="10.0.0.9", jarvis_dashboard=True, voice_extras=False, calendars="")
        made = [m["url_path"] for m in fake.ws_calls if m["type"] == "lovelace/dashboards/create"]
        self.assertEqual(made, ["jarvis-rezept"])
        recipe = [m for m in fake.ws_calls if m["type"] == "lovelace/config/save"][0]["config"]
        self.assertEqual(recipe["views"][0]["cards"], [{"type": "custom:jarvis-recipe-card",
                                                        "entity": "sensor.jarvis_recipe"}])
        self.assertTrue(any("WARN: no Jarvis dashboard, as its clock is wall-clock-card" in line for line in log))

    def test_no_jarvis_dashboard_is_asked_for_without_genbus_cards(self):
        fake = FakeHA()
        asked = []
        _, log = run_deploy(fake, choose=lambda label, *_: asked.append(label) or (
            "Wohnzimmer" if label == "room" else "yes"), host="10.0.0.9", voice_extras=False, calendars="")
        self.assertNotIn("jarvis dashboard", asked)
        self.assertFalse(any("Jarvis dashboard" in line for line in log))
        _, log = run_deploy(FakeHA(), choose=None, host="10.0.0.9", jarvis_dashboard=False)
        self.assertFalse(any("Jarvis dashboard" in line for line in log))

    def test_a_room_with_nothing_to_show_gets_none(self):
        fake = FakeHA()
        fake.entities = [e for e in fake.entities
                         if e["entity_id"].startswith(("binary_sensor.", "sensor.wohnzimmer_battery"))]
        _, log = run_deploy(fake, host="10.0.0.9", room="Wohnzimmer", room_dashboard=True)
        self.assertEqual(len(fake.ws_calls), 1)
        self.assertTrue(any("nothing in Wohnzimmer worth a room dashboard" in line for line in log))

    def test_room_keys_match_the_shows(self):
        self.assertEqual(ha.slug_key("Küche"), "kuche")
        self.assertEqual(ha.slug_key("Küche", True), "kueche")
        self.assertEqual(ha.slug_key("Groß Raum_2"), "grossraum2")

    def test_websocket_exchange(self):
        key = "dGhlIHNhbXBsZSBub25jZQ=="

        def frame(obj):
            data = json.dumps(obj).encode()
            return bytes([0x81, len(data)]) + data

        accept = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        server = (f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
        server += frame({"type": "auth_required"}) + frame({"type": "auth_ok"})
        server += bytes([0x89, 0]) + frame({"id": 1, "type": "result", "success": True, "result": {"ok": 1}})

        class Sock:
            def __init__(self):
                self.inbox, self.sent = server, b""

            def recv(self, n):
                out, self.inbox = self.inbox[:7], self.inbox[7:]  # in small pieces, as a network gives them
                return out

            def sendall(self, data):
                self.sent += data
        sock = Sock()
        result = ha._ws_exchange(sock, "ha:8123", TOKEN, {"type": "x"}, key=key)
        self.assertEqual(result, {"ok": 1})
        sent = sock.sent.split(b"\r\n\r\n", 1)[1]
        messages, pongs = [], 0
        while sent:
            op, n, mask = sent[0] & 0x0F, sent[1] & 0x7F, sent[2:6]
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(sent[6:6 + n]))
            sent = sent[6 + n:]
            if op == 10:
                pongs += 1
            else:
                messages.append(json.loads(payload))
        self.assertEqual(messages, [{"type": "auth", "access_token": TOKEN}, {"id": 1, "type": "x"}])
        self.assertEqual(pongs, 1)

    def test_websocket_refusal_says_why_without_the_token(self):
        def frame(obj):
            data = json.dumps(obj).encode()
            return bytes([0x81, len(data)]) + data

        class Sock:
            inbox = (b"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"
                     + frame({"type": "auth_required"}) + frame({"type": "auth_invalid"}))

            def recv(self, n):
                out, self.inbox = self.inbox[:n], self.inbox[n:]
                return out

            def sendall(self, data):
                pass
        with self.assertRaises(ValueError) as cm:
            ha._ws_exchange(Sock(), "ha:8123", TOKEN, {"type": "x"}, key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertNotIn(TOKEN, str(cm.exception))


class FakeCalendarHA(FakeHA):
    """FakeHA with Home Assistant's CalDAV form and the Show's calendar_sources action. calendars come
    from a CalDAV account it has (entry OLD), yaml_calendars from configuration.yaml (no registry entry);
    caldav_brings are the new account's (entry CAL1). Every calendar has one private event; the ones
    in silent don't answer."""

    def __init__(self, *, calendars=(), yaml_calendars=(), caldav_error=None, caldav_abort=None,
                 caldav_brings=("calendar.personal",), silent=(), **kw):
        super().__init__(**kw)
        self.silent = set(silent)
        self.calendars = {c: c.split(".", 1)[1].title() for c in (*calendars, *yaml_calendars)}
        self.owner = {c: "OLD" for c in calendars}
        self.caldav_entries = [{"entry_id": "OLD", "domain": "caldav", "title": "old-user"}] if calendars else []
        self.caldav_error = caldav_error
        self.caldav_abort = caldav_abort
        self.caldav_brings = caldav_brings
        self.saved = []
        self.card_file = None  # the calendar card's source as Home Assistant serves it; None: no resource
        self.genbu_card = None  # the one Genbu serves; None: no Genbu, or one from before it brought cards

    def request(self, method, path, body=None):
        if method == "GET" and path == "/local/openjade/jarvis-calendar-card.js?v=8":
            return self.card_file
        if method == "GET" and path == "/genbu_panel/jarvis-calendar-card.js":
            if self.genbu_card is None:
                raise ha.NotFound(f"GET {path}: HTTP 404")
            return self.genbu_card
        if method == "GET" and path.startswith("/api/calendars/"):
            self.calls.append((method, path, body))
            calendar = urllib.parse.unquote(path[len("/api/calendars/"):].split("?", 1)[0])
            if calendar in self.silent or calendar not in self.calendars:
                raise ha.HomeAssistantError(f"GET /api/calendars/{calendar}: HTTP 500")
            return [{"summary": "Fahrpruefung PRIVATE", "description": "PRIVATE notes",
                     "start": {"dateTime": "2026-10-29T09:00:00+01:00"}, "end": {"dateTime": "2026-10-29T10:00:00+01:00"}}]
        if method == "GET" and path == "/api/config/config_entries/entry?domain=caldav":
            return list(self.caldav_entries)
        if method == "DELETE" and path.startswith("/api/config/config_entries/entry/"):
            self.calls.append((method, path, body))
            gone = path.rsplit("/", 1)[1]
            self.caldav_entries = [e for e in self.caldav_entries if e["entry_id"] != gone]
            for c in [c for c, owner in self.owner.items() if owner == gone]:
                del self.calendars[c], self.owner[c]
            return {"require_restart": False}
        if method == "POST" and path == "/api/config/config_entries/flow" and body["handler"] == "caldav":
            self.calls.append((method, path, body))
            return {"type": "form", "flow_id": "C1", "step_id": "user", "errors": {}}
        if method == "POST" and path == "/api/config/config_entries/flow/C1":
            self.calls.append((method, path, body))
            if self.caldav_error:
                return {"type": "form", "flow_id": "C1", "step_id": "user", "errors": {"base": self.caldav_error}}
            if self.caldav_abort:
                return {"type": "abort", "reason": self.caldav_abort}
            for c in self.caldav_brings:  # a second calendar of one name is calendar.<name>_2, same name
                self.calendars[c] = c.split(".", 1)[1].removesuffix("_2").title()
                self.owner[c] = "CAL1"
            self.caldav_entries.append({"entry_id": "CAL1", "domain": "caldav", "title": ACCOUNT.username})
            return {"type": "create_entry", "result": {"entry_id": "CAL1"}}
        if path == "/api/states" and method == "GET":
            return super().request(method, path, body) + [
                {"entity_id": c, "state": "off", "attributes": {"friendly_name": n}} for c, n in self.calendars.items()]
        if path == "/api/services" and method == "GET":
            listed = super().request(method, path, body)
            listed[1]["services"]["jarvis_show_5_calendar_sources"] = {}
            return listed
        return super().request(method, path, body)

    def ws(self, message):
        if message["type"] == "lovelace/config/save":
            self.saved.append(message)
            return {}
        if message["type"] == "lovelace/resources" and self.card_file is not None:
            return [{"url": "/local/openjade/jarvis-weather-card.js?v=10", "type": "module"},
                    {"url": "/local/openjade/jarvis-calendar-card.js?v=8", "type": "module"}]
        if message["type"] == "config/entity_registry/list":
            return super().ws(message) + [{"entity_id": c, "config_entry_id": owner} for c, owner in self.owner.items()]
        if message["type"] == "config/entity_registry/update" and message["entity_id"] in self.owner:
            was, now = message["entity_id"], message["new_entity_id"]
            if now in self.calendars:
                raise ha.HomeAssistantError(f"Entity with this ID is already registered: {now}")
            self.calendars[now], self.owner[now] = self.calendars.pop(was), self.owner.pop(was)
            self.ws_calls.append(message)
            return {}
        return super().ws(message)

    def sources(self):
        return [b for p, b in self.posted("/api/services/esphome/jarvis_show_5_calendar_sources")]


ACCOUNT = ha.CalDAVAccount(url="https://cloud.example.org/remote.php/dav", username="jo", password="app-pass-123")


class CalendarTests(unittest.TestCase):
    def test_an_account_from_the_switches_is_added_and_its_calendar_shown(self):
        fake = FakeCalendarHA()
        _, log = run_deploy(fake, host="10.0.0.9", caldav=ACCOUNT, calendars="calendar.personal")
        self.assertEqual(fake.posted("/api/config/config_entries/flow/C1")[0][1],
                         {"url": ACCOUNT.url, "username": "jo", "password": "app-pass-123", "verify_ssl": True})
        self.assertEqual(fake.sources(), [{"calendars": "calendar.personal"}])
        self.assertIn("CalDAV account jo added", log)
        self.assertIn("its calendars in Home Assistant: calendar.personal", log)
        for line in log:
            self.assertNotIn("app-pass-123", line)
        self.assertNotIn("app-pass-123", repr(ACCOUNT))

    def test_a_refused_password_is_said_and_nothing_else_breaks(self):
        fake = FakeCalendarHA(caldav_error="invalid_auth")
        _, log = run_deploy(fake, host="10.0.0.9", caldav=ACCOUNT)
        self.assertTrue(any("refused the user name or password" in line for line in log))
        self.assertIn(("DELETE", "/api/config/config_entries/flow/C1", None), fake.calls)
        self.assertEqual(fake.sources(), [])
        self.assertTrue(any("has no calendar" in line for line in log))

    def test_asked_for_an_account_only_when_the_question_is_said_yes_to(self):
        for answer, added in (("yes", True), ("no", False), (None, False)):
            fake = FakeCalendarHA()
            asked = []

            def choose(label, current, options, names=None):
                asked.append(label)
                return {"calendar account": answer, "calendars": "all"}.get(label)
            _, log = run_deploy(fake, choose=choose, host="10.0.0.9", voice_extras=False,
                                ask_caldav=lambda: asked.append("account") or ACCOUNT)
            self.assertEqual("account" in asked, added, answer)
            self.assertEqual(bool(fake.posted("/api/config/config_entries/flow/C1")), added)
            self.assertEqual(fake.sources(), [{"calendars": "calendar.personal"}] if added else [])

    def test_an_account_it_has_is_replaced_or_the_step_skipped(self):
        offered = {}

        def chooser(answer):
            def choose(label, current, options, names=None):
                offered[label] = (options, names)
                return {"calendar account": answer, "calendars": "all"}.get(label)
            return choose
        fake = FakeCalendarHA(calendars=("calendar.personal", "calendar.contact_birthdays"),
                              caldav_brings=("calendar.personal_2", "calendar.contact_birthdays_2"))
        _, log = run_deploy(fake, choose=chooser("skip"), host="10.0.0.9", voice_extras=False,
                            ask_caldav=lambda: self.fail("asked for an account"))
        options, names = offered["calendar account"]
        self.assertEqual(options, ["replace", "skip"])
        self.assertEqual(names["replace"], "replace the CalDAV account old-user with another one")
        self.assertEqual(names["skip"], "skip, keep old-user and its calendars: Contact_Birthdays, Personal")
        self.assertFalse(fake.posted("/api/config/config_entries/flow/C1"))
        self.assertEqual(fake.sources(), [{"calendars": "calendar.contact_birthdays, calendar.personal"}])

        fake = FakeCalendarHA(calendars=("calendar.personal", "calendar.contact_birthdays"),
                              caldav_brings=("calendar.personal_2", "calendar.contact_birthdays_2"))
        _, log = run_deploy(fake, choose=chooser("replace"), host="10.0.0.9", voice_extras=False,
                            ask_caldav=lambda: ACCOUNT)
        self.assertIn(("DELETE", "/api/config/config_entries/entry/OLD", None), fake.calls)
        self.assertEqual(sorted(fake.calendars), ["calendar.contact_birthdays", "calendar.personal"])
        self.assertEqual(set(fake.owner.values()), {"CAL1"})
        self.assertIn("calendar.personal_2 is calendar.personal again, the id the old account's calendar had", log)
        self.assertEqual(fake.sources(), [{"calendars": "calendar.contact_birthdays, calendar.personal"}])
        self.assertLess(log.index("CalDAV account jo added"), log.index("CalDAV account old-user removed from Home Assistant"))

    def test_the_old_account_stays_when_the_new_one_is_refused_or_the_same(self):
        for kw in ({"caldav_error": "invalid_auth"}, {"caldav_abort": "already_configured"}):
            fake = FakeCalendarHA(calendars=("calendar.personal",), **kw)
            _, log = run_deploy(fake, host="10.0.0.9", caldav=ACCOUNT, calendars="calendar.personal")
            self.assertNotIn(("DELETE", "/api/config/config_entries/entry/OLD", None), fake.calls, kw)
            self.assertEqual(fake.caldav_entries[0]["entry_id"], "OLD")
            self.assertEqual(fake.sources(), [{"calendars": "calendar.personal"}])
        self.assertIn("CalDAV account jo was in Home Assistant already, so it stays as it is", log)

    def test_calendars_from_configuration_yaml_are_never_doubled(self):
        fake = FakeCalendarHA(yaml_calendars=("calendar.personal",), caldav_brings=("calendar.personal_2",))
        asked = []
        _, log = run_deploy(fake, choose=lambda label, *_: asked.append(label) or None, host="10.0.0.9",
                            voice_extras=False, ask_caldav=lambda: ACCOUNT)
        self.assertNotIn("calendar account", asked)
        self.assertFalse(fake.posted("/api/config/config_entries/flow/C1"))
        self.assertTrue(any(line.startswith("Home Assistant has calendar.personal from configuration.yaml")
                            for line in log))

    def test_the_calendars_offered_are_the_ones_there(self):
        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        offered = {}

        def choose(label, current, options, names=None):
            offered[label] = (options, names)
            return {"calendars": "calendar.familie"}.get(label)
        run_deploy(fake, choose=choose, host="10.0.0.9", voice_extras=False)
        self.assertNotIn("calendar account", offered)  # nobody to ask for one
        self.assertEqual(offered["calendars"][0], ["all", "calendar.arbeit", "calendar.familie"])
        self.assertEqual(offered["calendars"][1]["calendar.familie"], "Familie (calendar.familie)")
        self.assertEqual(fake.sources(), [{"calendars": "calendar.familie"}])

        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        run_deploy(fake, choose=lambda label, *_: "all" if label == "calendars" else None, host="10.0.0.9",
                   voice_extras=False)
        self.assertEqual(fake.sources(), [{"calendars": "calendar.arbeit, calendar.familie"}])

    def test_a_switch_says_which_and_unknown_ones_are_left_out(self):
        fake = FakeCalendarHA(calendars=("calendar.familie",))
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie, calendar.weg")
        self.assertEqual(fake.sources(), [{"calendars": "calendar.familie"}])
        self.assertTrue(any("no calendar.weg" in line for line in log))

    def test_an_empty_switch_or_nobody_to_ask_leaves_the_calendars_alone(self):
        for kw in ({"calendars": ""}, {}):
            fake = FakeCalendarHA(calendars=("calendar.familie",))
            run_deploy(fake, host="10.0.0.9", **kw)
            self.assertEqual(fake.sources(), [])
            self.assertFalse(fake.posted("/api/config/config_entries/flow/C1"))

    def test_the_card_on_a_missing_calendar_is_pointed_at_the_chosen_one(self):
        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        fake.boards = [{"url_path": "jarvis-display", "mode": "storage"}, {"url_path": "yaml-one", "mode": "yaml"}]
        fake.views = {"jarvis-display": [{"cards": [{"type": "vertical-stack", "cards": [
            {"type": "custom:jarvis-calendar-card", "entity": "calendar.personal", "title": "Kalender"}]}]}],
            None: [{"cards": [{"type": "custom:jarvis-calendar-card", "entity": "calendar.arbeit"}]}]}
        kept = []
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie",
                            keep_replaced=lambda what, config: kept.append((what, config)) or "kept")
        self.assertEqual(len(fake.saved), 1)
        self.assertEqual(fake.saved[0]["url_path"], "jarvis-display")
        card = fake.saved[0]["config"]["views"][0]["cards"][0]["cards"][0]
        self.assertEqual(card, {"type": "custom:jarvis-calendar-card", "entity": "calendar.familie", "title": "Kalender"})
        self.assertEqual(kept[0][0], "dashboard-jarvis-display")
        self.assertEqual(kept[0][1]["views"][0]["cards"][0]["cards"][0]["entity"], "calendar.personal")
        self.assertIn("the calendar card on jarvis-display now shows calendar.familie", log)

    def test_the_card_gets_every_chosen_calendar(self):
        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        fake.boards = [{"url_path": "jarvis-display", "mode": "storage"}]
        fake.views = {"jarvis-display": [{"cards": [
            {"type": "custom:jarvis-calendar-card", "entities": ["calendar.arbeit", "calendar.personal"]},
            {"type": "custom:jarvis-calendar-card", "entities": ["calendar.arbeit", "calendar.familie"]}]}]}
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie, calendar.arbeit")
        cards = fake.saved[0]["config"]["views"][0]["cards"]
        self.assertEqual(cards[0], {"type": "custom:jarvis-calendar-card",
                                    "entities": ["calendar.familie", "calendar.arbeit"]})
        self.assertEqual(cards[1]["entities"], ["calendar.arbeit", "calendar.familie"])  # all there: untouched
        self.assertIn("the calendar card on jarvis-display now shows calendar.familie, calendar.arbeit", log)

    def test_a_card_file_from_before_lists_gets_one_calendar(self):
        # It throws on entities, and Home Assistant draws a red card with nothing in it.
        old = "setConfig(config) { if (!config.entity) { throw new Error('calendar entity required'); } }"
        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        fake.card_file = old
        fake.boards = [{"url_path": "jarvis-display", "mode": "storage"}]
        fake.views = {"jarvis-display": [{"cards": [
            {"type": "custom:jarvis-calendar-card", "entity": "calendar.personal"},
            {"type": "custom:jarvis-calendar-card", "entities": ["calendar.arbeit", "calendar.familie"]}]}]}
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie, calendar.arbeit")
        cards = fake.saved[0]["config"]["views"][0]["cards"]
        self.assertEqual(cards, [{"type": "custom:jarvis-calendar-card", "entity": "calendar.familie"}] * 2)
        self.assertTrue(any(line.startswith("WARN: the calendar card file on Home Assistant is older") for line in log))
        self.assertIn("the calendar card on jarvis-display now shows calendar.familie", log)
        self.assertEqual(fake.sources(), [{"calendars": "calendar.familie, calendar.arbeit"}])  # the Show: both

        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        fake.card_file = old.replace("config.entity", "config.entities || config.entity")
        fake.boards = [{"url_path": "jarvis-display", "mode": "storage"}]
        fake.views = {"jarvis-display": [{"cards": [{"type": "custom:jarvis-calendar-card", "entity": "calendar.personal"}]}]}
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie, calendar.arbeit")
        self.assertEqual(fake.saved[0]["config"]["views"][0]["cards"][0]["entities"],
                         ["calendar.familie", "calendar.arbeit"])
        self.assertFalse(any("older than this installer" in line for line in log))

        # Genbu's card is the one that counts: it loads before any resource, and the old copy then does nothing.
        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"))
        fake.card_file = old
        fake.genbu_card = (pathlib.Path(__file__).parents[1] / "ha/custom_components/genbu/frontend"
                           / "jarvis-calendar-card.js").read_text()
        fake.boards = [{"url_path": "jarvis-display", "mode": "storage"}]
        fake.views = {"jarvis-display": [{"cards": [{"type": "custom:jarvis-calendar-card", "entity": "calendar.personal"}]}]}
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie, calendar.arbeit")
        self.assertEqual(fake.saved[0]["config"]["views"][0]["cards"][0]["entities"],
                         ["calendar.familie", "calendar.arbeit"])
        self.assertFalse(any("older than this installer" in line for line in log))

    def test_each_chosen_calendar_is_tried_and_what_it_says_is_never_read_out(self):
        fake = FakeCalendarHA(calendars=("calendar.familie", "calendar.arbeit"), silent=("calendar.arbeit",))
        _, log = run_deploy(fake, host="10.0.0.9", calendars="calendar.familie,calendar.arbeit")
        asked = [p for m, p, _ in fake.calls if p.startswith("/api/calendars/")]
        self.assertEqual([p.split("?")[0] for p in asked], ["/api/calendars/calendar.familie",
                                                            "/api/calendars/calendar.arbeit"])
        self.assertTrue(all("start=" in p and "end=" in p for p in asked))
        self.assertIn("calendar.familie answers", log)
        self.assertTrue(any(line.startswith("WARN: calendar.arbeit did not answer") for line in log))
        self.assertEqual(fake.sources(), [{"calendars": "calendar.familie, calendar.arbeit"}])
        for line in log:
            self.assertNotIn("PRIVATE", line)
            self.assertNotIn("Fahrpruefung", line)

    def test_caldav_address_is_checked(self):
        self.assertEqual(ha.check_caldav_url(" https://caldav.icloud.com "), "https://caldav.icloud.com")
        for bad in ("caldav.icloud.com", "ftp://x.example.org", "https://"):
            with self.assertRaises(ValueError):
                ha.check_caldav_url(bad)


class CalendarSwitchTests(unittest.TestCase):
    def ns(self, **kw):
        base = dict(caldav_url=None, caldav_user=None, caldav_password_file=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_the_password_comes_from_its_file_and_only_into_the_account(self):
        with tempfile.TemporaryDirectory() as td:
            pw = pathlib.Path(td) / "pw"
            pw.write_text("app-pass-123\n")
            got = show_cli._caldav_account(self.ns(caldav_url="https://cloud.example.org/remote.php/dav",
                                                   caldav_user="jo", caldav_password_file=pw))
        self.assertEqual((got.url, got.username, got.password),
                         ("https://cloud.example.org/remote.php/dav", "jo", "app-pass-123"))
        self.assertIsNone(show_cli._caldav_account(self.ns()))

    def test_half_an_account_is_refused(self):
        for kw in ({"caldav_user": "jo"}, {"caldav_url": "https://cloud.example.org/dav"}):
            with self.assertRaises(SettingsError):
                show_cli._caldav_account(self.ns(**kw))

    def test_the_account_is_asked_for_at_the_terminal(self):
        answers = iter(["https://cloud.example.org/remote.php/dav", "jo"])
        asker = Asker(interactive=True, ask=lambda _: next(answers), ask_secret=lambda _: "app-pass-123",
                      say=lambda _: None)
        with mock.patch.object(show_cli, "_asker", return_value=asker):
            got = show_cli._caldav_asker(argparse.Namespace())()
        self.assertEqual((got.username, got.password), ("jo", "app-pass-123"))


class FakeSignedIn:
    """What settings needs of a home_assistant.SignedIn."""

    def __init__(self, sign_in_id="S-now"):
        self.id, self.out, self.ended = sign_in_id, False, []

    def sign_in_id(self):
        return self.id

    def end_other(self, sign_in_id):
        self.ended.append(sign_in_id)
        return True

    def sign_out(self):
        self.out = True


def args(**kw):
    base = dict(wifi=None, wifi_passphrase_file=None, ha_url=None, ha_token_file=None, ha_admin_token_file=None,
                dashcast=None, dashcast_key_file=None, music_assistant=None, root_password_file=None)
    base.update(kw)
    return argparse.Namespace(**base)


    def test_waits_for_the_show_to_send_its_wake_words(self):
        fake = FakeHA(unconfigured=3)
        offered = {}

        def choose(label, current, options, names=None):
            offered[label] = (current, options)
            return None
        _, log = run_deploy(fake, choose=choose, host="10.0.0.9")
        self.assertEqual(offered["wake word"], ("Okay Nabu", ["Okay Nabu", "Hey Jarvis"]))
        self.assertTrue(any("waiting for the Show to tell" in line for line in log))

    def test_a_select_that_never_comes_is_not_asked_about(self):
        fake = FakeHA(unconfigured=10**6)
        asked = []
        _, log = run_deploy(fake, choose=lambda label, *_: asked.append(label), host="10.0.0.9")
        self.assertEqual(asked, ["assistant", "room"])
        self.assertTrue(any("not available yet" in line for line in log))


class GatherTests(unittest.TestCase):
    def asker(self, answers, secrets):
        answers, secrets, said = list(answers), list(secrets), []
        return Asker(True, ask=lambda _: answers.pop(0), ask_secret=lambda _: secrets.pop(0), say=said.append,
                     out=ui.Out(io.StringIO(), color=False, fancy=True, width=78)), said

    def test_pick_by_number_name_or_enter(self):
        asker, said = self.asker(["9", "2"], [])
        self.assertEqual(asker.pick("assistant", "preferred", ["preferred", "Jarvis"]), "Jarvis")
        self.assertTrue(any("type 1 to 2" in line for line in said))
        asker, _ = self.asker(["hey jarvis"], [])
        self.assertEqual(asker.pick("wake word", "Okay Nabu", ["Okay Nabu", "Hey Jarvis"]), "Hey Jarvis")
        asker, _ = self.asker([""], [])
        self.assertIsNone(asker.pick("wake word", "Okay Nabu", ["Okay Nabu", "Hey Jarvis"]))
        self.assertIsNone(Asker(False).pick("wake word", "Okay Nabu", ["Okay Nabu"]))
        asker, _ = self.asker(["jarvis"], [])
        self.assertEqual(asker.pick("wake word", "Okay Nabu", ["Okay Nabu", "Hey Jarvis"]), "Hey Jarvis")
        asker, said = self.asker(["hey", ""], [])
        self.assertEqual(asker.pick("wake word", "", ["Hey Jarvis", "Hey Mycroft"], default="Hey Jarvis"),
                         "Hey Jarvis")
        self.assertTrue(any("Enter: Hey Jarvis" in line for line in said))
        asker, said = self.asker(["1"], [])
        self.assertEqual(asker.pick("q", "preferred", ["preferred", "Jarvis"], names={"preferred": "the usual"}),
                         "preferred")
        self.assertIn("  │   1  the usual  ← now", said)

    def test_everything_asked(self):
        asker, _ = self.asker(["Home", "http://ha:8123/", "jade", "10.0.0.5", "10.0.0.6", "jade"],
                              ["wifi-pass", "wifi-pass", "ha-pass", DASH_KEY, "ma-pass"])
        signed = FakeSignedIn()
        logins = []

        def ha_login(url, user, password, code=None):
            logins.append(("ha", url, user, password))
            return signed

        def ma_login(host, user, password):
            logins.append(("ma", host, user, password))
            return "ma-session", "ma-id"
        with tempfile.TemporaryDirectory() as td:
            run = SignIns(lambda: pathlib.Path(td) / "sign-ins.json")
            s = gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, nearby=lambda: [],
                       ha_login=ha_login, ma_login=ma_login, ma_logout=lambda *a: None, run=run)
            self.assertTrue(run.active)
        self.assertEqual(s, Settings(wifi="Home", wifi_passphrase="wifi-pass", ha_url="http://ha:8123",
                                     dashcast="10.0.0.5:9555", dashcast_key=DASH_KEY, music_assistant="10.0.0.6",
                                     music_assistant_token="ma-session", own_ha_user=True, ha_user="jade",
                                     music_assistant_user="jade"))
        self.assertIs(s.ha_sign_in, signed)
        self.assertEqual(logins, [("ha", "http://ha:8123", "jade", "ha-pass"), ("ma", "10.0.0.6", "jade", "ma-pass")])
        self.assertIn("added to Home Assistant automatically, signed in as jade until the run ends", s.summary())
        for line in s.summary():
            for secret in ("wifi-pass", "ha-pass", DASH_KEY, "ma-pass", "ma-session"):
                self.assertNotIn(secret, line)

    def test_enter_takes_the_remembered_addresses_and_the_keyring(self):
        keyring = {"ha-token": TOKEN, "ha-admin-token": "admin-token", "dashcast-key": DASH_KEY,
                   "wifi": "wifi-pass", "music-assistant-token": "ma-admin"}
        asker, _ = self.asker(["", "", "", ""], [])
        defaults = {"wifi": "Home", "ha_url": "http://ha:8123", "dashcast": "10.0.0.5:9555", "music_assistant": "10.0.0.6"}
        s = gather(args(), asker, defaults=defaults, lookup=lambda secret, **attrs: keyring.get(secret),
                   nearby=lambda: [])
        self.assertEqual((s.wifi, s.ha_url, s.dashcast, s.music_assistant),
                         ("Home", "http://ha:8123", "10.0.0.5:9555", "10.0.0.6"))
        self.assertEqual((s.wifi_passphrase, s.ha_token, s.dashcast_key, s.music_assistant_token),
                         ("wifi-pass", None, DASH_KEY, "ma-admin"))
        self.assertEqual((s.ha_admin_token, s.own_ha_user), ("admin-token", True),
                         "with an admin the Show gets a user of its own, not the hand-made token")

    def test_wifi_picked_from_what_is_nearby(self):
        asker, said = self.asker(["4", "2", "-", "-", "-"], ["short", "  spaced pass ", "typo here", "  spaced pass ", "  spaced pass ", ""])
        s = gather(args(music_assistant="10.0.0.6"), asker, defaults={}, lookup=lambda *a, **k: None,
                   nearby=lambda: ["Upstairs", "Garden IoT", "Neighbour"], want_wifi=True)
        self.assertEqual(s.wifi, "Garden IoT")
        self.assertEqual(s.wifi_passphrase, "  spaced pass ", "spaces at the ends belong to the passphrase")
        self.assertTrue(any("2  Garden IoT" in line for line in said))
        self.assertTrue(any("pick 1 to 3" in line for line in said))
        self.assertTrue(any("8 to 63" in line for line in said))
        self.assertTrue(any("did not match" in line for line in said))
        self.assertTrue(any("secret-tool store" in line and "'Garden IoT'" in line for line in said))
        self.assertFalse(any("spaced pass" in line for line in said))

    def test_wifi_name_is_checked_and_typed_names_still_work(self):
        asker, said = self.asker(["x" * 33, "Attic", "-", "-", "-"], ["long enough", "long enough"])
        s = gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, nearby=lambda: [])
        self.assertEqual((s.wifi, s.wifi_passphrase), ("Attic", "long enough"))
        self.assertTrue(any("1 to 32 bytes" in line for line in said))

    def test_enter_on_wifi_leaves_it_to_the_screen(self):
        asker, said = self.asker(["", "-", "-", "-"], [])
        s = gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, nearby=lambda: ["Upstairs"])
        self.assertIsNone(s.wifi)
        self.assertEqual(s.summary()[0], "Wi-Fi: picked on the Show's screen")

    def test_bad_answer_is_asked_again(self):
        asker, said = self.asker(["-", "ftp://ha.example", "http://ha.example", "-", "-", "nope.example", "10.0.0.6", "-"],
                                 ["", ""])
        s = gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, nearby=lambda: [],
                   resolve=lambda h: (_ for _ in ()).throw(OSError("no such host")))
        self.assertEqual((s.wifi, s.ha_url, s.ha_token, s.dashcast, s.music_assistant),
                         (None, "http://ha.example", None, None, "10.0.0.6"))
        self.assertTrue(any("not a Home Assistant address" in line for line in said))
        self.assertTrue(any("cannot look up" in line for line in said))

    def test_dashcast_without_key_is_dropped(self):
        asker, said = self.asker(["-", "-", "10.0.0.5", "-"], ["", ""])
        s = gather(args(music_assistant="10.0.0.6"), asker, defaults={}, lookup=lambda *a, **k: None)
        self.assertIsNone(s.dashcast)
        self.assertEqual(s.music_assistant, "10.0.0.6")

    def test_switches_without_a_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            token = pathlib.Path(td) / "t"
            token.write_text(TOKEN + "\n")
            s = gather(args(ha_url="http://ha:8123", ha_token_file=token, music_assistant="10.0.0.6"),
                       Asker(False), defaults={"dashcast": "10.0.0.5:9555"}, lookup=lambda *a, **k: None)
        self.assertEqual((s.ha_url, s.ha_token, s.dashcast, s.music_assistant, s.ha_admin_token),
                         ("http://ha:8123", TOKEN, None, "10.0.0.6", None))
        with self.assertRaises(SettingsError):
            gather(args(dashcast="bad host"), Asker(False), defaults={}, lookup=lambda *a, **k: None)

    def test_music_assistant_token_whenever_there_is_a_server(self):
        keyring = {"music-assistant-token": "ma-token"}
        s = gather(args(music_assistant="10.0.0.6"), Asker(False), defaults={},
                   lookup=lambda secret, **attrs: keyring.get(secret))
        self.assertEqual(s.music_assistant_token, "ma-token")
        self.assertIn("Music Assistant: the Show gets a user of its own there, for lyrics", s.summary())
        self.assertNotIn("Music Assistant: online metadata lookups switched off", s.summary())
        s = gather(args(music_assistant="10.0.0.6", music_assistant_local_metadata=True), Asker(False),
                   defaults={}, lookup=lambda secret, **attrs: keyring.get(secret))
        self.assertEqual(s.music_assistant_token, "ma-token")
        self.assertIn("Music Assistant: online metadata lookups switched off", s.summary())
        self.assertFalse(any("ma-token" in line for line in s.summary()))
        s = gather(args(music_assistant_local_metadata=True), Asker(False), defaults={},
                   lookup=lambda secret, **attrs: keyring.get(secret))
        self.assertIsNone(s.music_assistant_token)

    def test_a_typed_root_password_has_to_be_saved(self):
        # Nobody answers yes: the install stops rather than go on with a password nobody kept.
        asker, said = self.asker(["no"], ["long enough", "long enough"])
        answers = ["no"]

        def ask(_):
            if not answers:
                raise EOFError
            return answers.pop(0)
        asker.ask = ask
        with self.assertRaises(SettingsError):
            gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, want_wifi=False, want_root_password=True)
        # One from the keyring is kept there already and is not asked about.
        asker, said = self.asker(["-", "-", "-"], [])
        s = gather(args(), asker, defaults={}, lookup=lambda secret, **a: {"root-password": "correct horse"}.get(secret),
                   want_wifi=False, want_root_password=True)
        self.assertEqual(s.root_password, "correct horse")
        self.assertFalse(any("saved this password" in line for line in said))

    def test_root_password_only_for_an_install(self):
        keyring = {"root-password": "  not stripped"}
        s = gather(args(), Asker(False), defaults={}, lookup=lambda secret, **attrs: keyring.get(secret))
        self.assertIsNone(s.root_password)
        keyring = {"root-password": "correct horse"}
        s = gather(args(), Asker(False), defaults={}, lookup=lambda secret, **attrs: keyring.get(secret),
                   want_root_password=True)
        self.assertEqual(s.root_password, "correct horse")
        self.assertIn("root password: set", s.summary())
        self.assertFalse(any("correct horse" in line for line in s.summary()))
        with self.assertRaises(SettingsError):
            gather(args(), Asker(False), defaults={}, lookup=lambda secret, **attrs: {"root-password": "short"}.get(secret),
                   want_root_password=True)

    def test_root_password_asked_twice_then_left_out(self):
        asker, said = self.asker(["", "no", "yes", "-", "-", "-"], ["short", "long enough", "long enough"])
        s = gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, want_wifi=False, want_root_password=True)
        self.assertEqual(s.root_password, "long enough")
        self.assertTrue(any("saved this password yourself" in line for line in said))
        self.assertTrue(any("type yes or no" in line for line in said))
        self.assertTrue(any("Save it now" in line for line in said))
        self.assertTrue(any("8 to 128" in line for line in said))
        self.assertTrue(any("secret-tool store" in line and "root-password" in line for line in said))
        asker, said = self.asker(["-", "-", "-"], ["", ""])
        s = gather(args(), asker, defaults={}, lookup=lambda *a, **k: None, want_wifi=False, want_root_password=True)
        self.assertIsNone(s.root_password)
        self.assertIn("gets a root shell", " ".join(line.strip() for line in said))
        self.assertIn("root password: none (the USB console needs none)", s.summary())

    def test_defaults_keep_no_secret(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "jarvis-show" / "defaults.json"
            save_defaults(Settings(wifi="Home", wifi_passphrase="p", ha_url="http://ha:8123", ha_token=TOKEN,
                                   dashcast="10.0.0.5:9555", dashcast_key=DASH_KEY), path)
            text = path.read_text()
            for secret in ("\"p\"", TOKEN, DASH_KEY):
                self.assertNotIn(secret, text)
            self.assertEqual(load_defaults(path), {"wifi": "Home", "ha_url": "http://ha:8123", "dashcast": "10.0.0.5:9555"})

    def test_run_that_asked_nothing_keeps_the_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "jarvis-show" / "defaults.json"
            save_defaults(Settings(ha_url="http://ha:8123", dashcast="10.0.0.5:9555"), path)
            save_defaults(Settings(), path, asked=False)
            self.assertEqual(load_defaults(path), {"ha_url": "http://ha:8123", "dashcast": "10.0.0.5:9555"})

    def test_secret_files_are_private_and_go_away(self):
        with secret_files(Settings(ha_token=TOKEN, dashcast_key=DASH_KEY)) as files:
            self.assertEqual(set(files), {"ha_token", "dashcast_key"})
            for path in files.values():
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(files["ha_token"].read_text(), TOKEN + "\n")
            folder = files["ha_token"].parent
        self.assertFalse(folder.exists())
        with secret_files(Settings(root_password="correct horse")) as files:
            self.assertEqual(files["root_password"].read_text(), "correct horse\n")


class MusicAssistantTests(unittest.TestCase):
    def server(self, *answers):
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
                out, self.inbox = self.inbox[:min(n, 9)], self.inbox[min(n, 9):]
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

    def test_online_lookups_off_and_only_online_providers_disabled(self):
        providers = [
            {"domain": "lrclib", "instance_id": "lrclib", "enabled": True},
            {"domain": "fanarttv", "instance_id": "fanarttv", "enabled": False},
            {"domain": "musicbrainz", "instance_id": "musicbrainz", "enabled": True},
            {"domain": "opensubsonic", "instance_id": "opensubsonic--x", "enabled": True},
        ]
        sock = self.server({"message_id": "1", "result": {"authenticated": True}},
                           {"message_id": "2", "result": {}},
                           {"message_id": "3", "result": providers},
                           {"message_id": "4", "result": {}})
        done = ma.exchange(sock, "10.0.0.6:8095", "ma-token", key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual(done, ["online metadata lookups off", "lrclib disabled"])
        self.assertEqual([(m["command"], m["args"]) for m in sock.messages()], [
            ("auth", {"token": "ma-token"}),
            ("config/core/save", {"domain": "metadata", "values": {"enable_online_metadata": False}}),
            ("config/providers", {}),
            ("config/providers/save", {"provider_domain": "lrclib", "instance_id": "lrclib",
                                       "values": {"enabled": False}}),
        ])
        self.assertTrue(sock.sent.startswith(b"GET /ws HTTP/1.1\r\n"))

    def test_a_refused_token_changes_nothing_and_is_not_repeated(self):
        sock = self.server({"message_id": "1", "error_code": 20, "details": "Invalid or expired token"})
        with self.assertRaises(ma.MusicAssistantError) as cm:
            ma.exchange(sock, "10.0.0.6:8095", "ma-token", key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertIn("did not accept the token", str(cm.exception))
        self.assertNotIn("ma-token", str(cm.exception))
        self.assertEqual([m["command"] for m in sock.messages()], ["auth"])

    def test_unreachable_says_where(self):
        def refuse(*a, **k):
            raise OSError("connection refused")
        with self.assertRaises(ma.MusicAssistantError) as cm:
            ma.local_metadata_only("10.0.0.6", "ma-token", connect=refuse)
        self.assertIn("10.0.0.6:8095", str(cm.exception))

    def test_show_user_made_once_and_its_old_token_replaced(self):
        sock = self.server({"message_id": "1", "result": {"authenticated": True}},
                           {"message_id": "2", "result": [{"user_id": "a1", "username": "admin", "role": "admin"}]},
                           {"message_id": "3", "result": {"user_id": "u5", "username": "jarvis-show-5",
                                                          "role": "user", "enabled": True}},
                           {"message_id": "4", "result": [{"token_id": "t-old", "name": ma.TOKEN_NAME},
                                                          {"token_id": "t-other", "name": "phone"}]},
                           {"message_id": "5", "result": {}},
                           {"message_id": "6", "result": "show-token"})
        token, made = ma.show_exchange(sock, "10.0.0.6:8095", "ma-token", "Jarvis Show 5",
                                       key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual((token, made), ("show-token", True))
        sent = sock.messages()
        self.assertEqual([m["command"] for m in sent], ["auth", "auth/users", "auth/user/create", "auth/tokens",
                                                        "auth/token/revoke", "auth/token/create"])
        create = sent[2]["args"]
        self.assertEqual((create["username"], create["role"], create["display_name"]),
                         ("jarvis-show-5", "user", "Jarvis Show 5"))
        self.assertGreaterEqual(len(create["password"]), 32)
        self.assertEqual(sent[4]["args"], {"token_id": "t-old"})
        self.assertEqual(sent[5]["args"], {"name": ma.TOKEN_NAME, "user_id": "u5"})

    def test_show_user_reused_and_an_admin_of_that_name_refused(self):
        sock = self.server({"message_id": "1", "result": {"authenticated": True}},
                           {"message_id": "2", "result": [{"user_id": "u5", "username": "jarvis-show-5",
                                                           "role": "user", "enabled": True}]},
                           {"message_id": "3", "result": []},
                           {"message_id": "4", "result": "show-token"})
        self.assertEqual(ma.show_exchange(sock, "10.0.0.6:8095", "ma-token", "Jarvis Show 5",
                                          key="dGhlIHNhbXBsZSBub25jZQ=="), ("show-token", False))
        self.assertNotIn("auth/user/create", [m["command"] for m in sock.messages()])
        sock = self.server({"message_id": "1", "result": {"authenticated": True}},
                           {"message_id": "2", "result": [{"user_id": "u5", "username": "jarvis-show-5",
                                                           "role": "admin", "enabled": True}]})
        with self.assertRaises(ma.MusicAssistantError):
            ma.show_exchange(sock, "10.0.0.6:8095", "ma-token", "Jarvis Show 5", key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual([m["command"] for m in sock.messages()], ["auth", "auth/users"])

    def test_genres_with_enough_songs_by_their_numeric_ids(self):
        few = [{"item_id": str(i)} for i in range(3)]
        enough = [{"item_id": str(i)} for i in range(ma.GENRE_LEAST_TRACKS)]
        sock = self.server({"message_id": "1", "result": {"authenticated": True}},
                           {"message_id": "2", "result": [
                               {"item_id": "29", "name": "Jazz", "uri": "library://genre/29"},
                               {"item_id": "36", "name": "New Age", "uri": "library://genre/36"},
                               {"item_id": "x", "name": "Odd", "uri": "spotify://genre/x"}]},
                           {"message_id": "3", "result": enough},
                           {"message_id": "4", "result": few})
        self.assertEqual(ma.genres_exchange(sock, "10.0.0.6:8095", "ma-token", key="dGhlIHNhbXBsZSBub25jZQ=="),
                         {"library://genre/29": "Jazz"})
        sent = sock.messages()
        self.assertEqual([m["command"] for m in sent], ["auth", "music/genres/library_items",
                                                        "music/tracks/library_items", "music/tracks/library_items"])
        # Only reads, and the genre filter as numbers: Music Assistant ignores it given as text.
        self.assertEqual(sent[2]["args"], {"genre": [29], "limit": ma.GENRE_LEAST_TRACKS})

    def test_show_username(self):
        self.assertEqual(ma.show_username("Jarvis Show 5"), "jarvis-show-5")
        self.assertEqual(ma.show_username("  Küche/Show "), "k-che-show")
        with self.assertRaises(ma.MusicAssistantError):
            ma.show_username("!")


class InstallShowSettingsTests(unittest.TestCase):
    def test_device_settings_from_switches(self):
        with tempfile.TemporaryDirectory() as td:
            token = pathlib.Path(td) / "t"
            key = pathlib.Path(td) / "k"
            token.write_text(TOKEN + "\n")
            key.write_text(DASH_KEY + "\n")
            a = argparse.Namespace(ha_url="http://ha:8123/", ha_token_file=str(token), dashcast="10.0.0.5",
                                   dashcast_key_file=str(key), music_assistant="10.0.0.6")
            got = install.device_settings(a)
        self.assertEqual(got, {"ha_url": "http://ha:8123", "ha_token": TOKEN, "dashcast": "10.0.0.5:9555",
                               "dashcast_key": DASH_KEY, "music_assistant": "10.0.0.6"})
        with self.assertRaises(Fail):
            install.device_settings(argparse.Namespace(ha_url="http://ha:8123", ha_token_file=None, dashcast=None,
                                                       dashcast_key_file=None, music_assistant=None))

    def test_first_state_only_has_what_was_asked(self):
        self.assertEqual(install.initial_state(False, {}), {})
        self.assertEqual(install.initial_state(True, {"dashcast": "h:1", "dashcast_key": DASH_KEY, "music_assistant": "10.0.0.6"}),
                         {"security": {"ssh": True}, "dashboard": {"server": "h:1", "key": DASH_KEY},
                          "sendspin": {"enabled": True, "server_ip": "10.0.0.6"}})

    def test_write_once_keeps_a_file_that_is_there(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            text = json.dumps({"dashboard": {"server": "h:1", "key": "it's a key"}})
            cmd = install.write_once_cmd(path, text)
            import subprocess
            first = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True).stdout.strip()
            again = subprocess.run(["sh", "-c", install.write_once_cmd(path, "{}")], capture_output=True, text=True).stdout.strip()
            self.assertEqual((first, again), ("WRITE-OK", "WRITE-KEPT"))
            self.assertEqual(json.loads(pathlib.Path(path).read_text()), json.loads(text))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()


SHOW_SPEC = importlib.util.spec_from_file_location("jarvis_show_cli", TOOLS / "jarvis-show.py")
show_cli = importlib.util.module_from_spec(SHOW_SPEC)
SHOW_SPEC.loader.exec_module(show_cli)


class KeyFileTests(unittest.TestCase):
    """home-assistant finds the Show by the name the installer recorded, never by guessing."""

    def _args(self, name):
        return argparse.Namespace(key_file=None, serial=None, name=name)

    def test_finds_the_show_by_its_installed_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            backups = pathlib.Path(tmp)
            record_show(backups / "AAAA05BJ", name="Jarvis Show 5", board="checkers")
            record_show(backups / "BBBB08TU", name="Kitchen", board="crown")
            (backups / "CCCC0003").mkdir()
            (backups / "CCCC0003" / "home-assistant.key").write_text("k\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(show_cli._key_file(self._args("jarvis show 5"), backups),
                                 backups / "AAAA05BJ" / "home-assistant.key")

    def test_unknown_name_stops_and_lists_the_known_shows(self):
        with tempfile.TemporaryDirectory() as tmp:
            backups = pathlib.Path(tmp)
            record_show(backups / "BBBB08TU", name="Kitchen", board="crown")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
                self.assertIsNone(show_cli._key_file(self._args("Jarvis Show 5"), backups))
            self.assertIn("'Kitchen' (crown, serial ending 08TU)", err.getvalue())
            self.assertNotIn("BBBB08TU", err.getvalue())

    def test_two_shows_with_one_name_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            backups = pathlib.Path(tmp)
            record_show(backups / "A1", name="Show", board="checkers")
            record_show(backups / "A2", name="Show", board="crown")
            self.assertIsNone(show_named(backups, "Show"))


class OwnUserHA(FakeHA):
    """FakeHA plus Home Assistant's users, its login flow, and the tokens of the Show's user."""

    def __init__(self, users=(), **kw):
        super().__init__(**kw)
        self.users = [dict(u) for u in users]
        self.passwords = {}
        self.llats = [{"id": "R-old", "type": "long_lived_access_token", "client_name": "by hand"},
                      {"id": "R-login", "type": "normal", "client_name": None}]
        self.revoked = []
        self.states["switch.jarvis_show_5_dashboard_as_this_device_s_own_user"] = {"state": "off"}
        self.sent_auth = []
        self.url = "http://ha:8123"
        self.owner = True
        self.current = True  # auth/refresh_tokens marks the token it was asked with
        self.events = []     # the handover and what was deleted, in order

    def request(self, method, path, body=None, *, auth=True, form=False):
        if not path.startswith("/auth/"):
            if path.startswith("/api/services/esphome/"):
                self.events.append(("hand over", path))
            return super().request(method, path, body)
        self.sent_auth.append((path, auth, form))
        if path == "/auth/login_flow":
            return {"type": "form", "flow_id": "L1"}
        if path == "/auth/login_flow/L1":
            ok = self.passwords.get(body["username"]) == body["password"]
            return {"type": "create_entry", "result": "CODE"} if ok else \
                {"type": "form", "errors": {"base": "invalid_auth"}}
        if path == "/auth/token":
            assert body["code"] == "CODE" and form
            return {"access_token": "USER-ACCESS", "refresh_token": "USER-REFRESH"}
        if path == "/auth/revoke":
            self.revoked.append(body["token"])
            return None
        raise AssertionError(f"unexpected {path}")

    def ws(self, message):
        kind = message["type"]
        if kind == "config/auth/list":
            return [dict(u) for u in self.users]
        self.ws_calls.append(message)
        if kind == "config/auth/delete":
            self.events.append(("delete user", message["user_id"]))
            self.users = [u for u in self.users if u["id"] != message["user_id"]]
            return None
        if kind == "config/auth/create":
            self.users.append({"id": "U-new", "name": message["name"], "username": None,
                               "group_ids": message["group_ids"], "local_only": message["local_only"]})
            return {"user": {"id": "U-new"}}
        if kind == "config/auth_provider/homeassistant/create":
            user = next(u for u in self.users if u["id"] == message["user_id"])
            user["username"] = message["username"]
            self.passwords[message["username"]] = message["password"]
            return None
        if kind == "config/auth_provider/homeassistant/admin_change_password":
            if not self.owner:
                raise ha.HomeAssistantError("config/auth_provider/homeassistant/admin_change_password: "
                                            "unauthorized: Unauthorized")
            user = next(u for u in self.users if u["id"] == message["user_id"])
            self.passwords[user["username"]] = message["password"]
            return None
        return super().ws(message)

    def as_user(self, token):
        # The installer's sign-in as the Show's user, or the new token the Show is given.
        current = {"USER-ACCESS": "R-login", "SHOW-USER-TOKEN": "R-new"}[token]
        fake = self

        class Own:
            def ws(self, message):
                fake.ws_calls.append(("as user", message))
                if message["type"] == "auth/long_lived_access_token":
                    assert token == "USER-ACCESS"
                    fake.llats.append({"id": "R-new", "type": "long_lived_access_token",
                                       "client_name": message["client_name"]})
                    return "SHOW-USER-TOKEN"
                if message["type"] == "auth/refresh_tokens":
                    return [{**t, "is_current": fake.current and t["id"] == current} for t in fake.llats]
                if message["type"] == "auth/delete_refresh_token":
                    fake.events.append(("delete token", message["refresh_token_id"]))
                    fake.llats = [t for t in fake.llats if t["id"] != message["refresh_token_id"]]
                    return {}
                raise AssertionError(message)
        return Own()


class OwnUserTests(unittest.TestCase):
    def deploy(self, fake):
        settings = ha.DeviceSettings(ha_url="http://ha:8123", own_user=True)
        log = []
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
        ha.deploy(fake, ha.DeployOptions(name="Jarvis Show 5", psk=PSK, host="10.0.0.9", settings=settings,
                                         wake_word="", assistant="", room="", wait_seconds=60),
                  progress=log.append, sleep=sleep, clock=lambda: now[0])
        return log

    def test_new_show_user_and_its_token(self):
        fake = OwnUserHA()
        log = self.deploy(fake)
        made = [m for m in fake.ws_calls if isinstance(m, dict) and m["type"] == "config/auth/create"]
        self.assertEqual(made, [{"type": "config/auth/create", "name": "Jarvis Show 5",
                                 "group_ids": ["system-users"], "local_only": True}])
        self.assertEqual(fake.users[0]["username"], "show_jarvis_show_5")
        actions = dict(fake.posted("/api/services/esphome/"))
        self.assertEqual(actions["/api/services/esphome/jarvis_show_5_home_assistant"],
                         {"url": "http://ha:8123", "token": "SHOW-USER-TOKEN"})
        self.assertEqual(fake.states["switch.jarvis_show_5_dashboard_as_this_device_s_own_user"]["state"], "on")
        self.assertEqual([t["id"] for t in fake.llats], ["R-login", "R-new"])
        self.assertEqual(fake.revoked, ["USER-REFRESH"])
        self.assertTrue(all(not auth for _, auth, _ in fake.sent_auth))
        password = fake.passwords["show_jarvis_show_5"]
        for line in log:
            for secret in (password, "SHOW-USER-TOKEN", "USER-ACCESS", "USER-REFRESH"):
                self.assertNotIn(secret, line)

    def test_the_same_user_again_gets_a_new_password_and_token(self):
        fake = OwnUserHA(users=[{"id": "U1", "name": "Jarvis Show 5", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-users"]}])
        fake.passwords["show_jarvis_show_5"] = "old"
        self.deploy(fake)
        self.assertFalse(any(isinstance(m, dict) and m["type"] == "config/auth/create" for m in fake.ws_calls))
        self.assertNotEqual(fake.passwords["show_jarvis_show_5"], "old")
        self.assertEqual(dict(fake.posted("/api/services/esphome/"))
                         ["/api/services/esphome/jarvis_show_5_home_assistant"]["token"], "SHOW-USER-TOKEN")

    def test_an_admin_who_is_not_the_owner_makes_a_second_user(self):
        # Only the owner may set a password. Deleting the user first would end the token the Show
        # still uses (GENBU-158), so a second one takes over and the first goes once the Show has it.
        fake = OwnUserHA(users=[{"id": "U1", "name": "Jarvis Show 5", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-users"]}])
        fake.owner = False
        fake.llats = [t for t in fake.llats if t["id"] != "R-old"]  # the old token is U1's, gone with it
        log = self.deploy(fake)
        self.assertEqual([(u["id"], u["username"]) for u in fake.users], [("U-new", "show_jarvis_show_5-2")])
        self.assertEqual(fake.events, [("hand over", "/api/services/esphome/jarvis_show_5_home_assistant"
                                                     "?return_response"), ("delete user", "U1")])
        self.assertTrue(any("made next to 'show_jarvis_show_5'" in line for line in log))
        self.assertEqual(dict(fake.posted("/api/services/esphome/"))
                         ["/api/services/esphome/jarvis_show_5_home_assistant"]["token"], "SHOW-USER-TOKEN")

        # The next run by that admin goes back to the first name the same way.
        fake.users[0]["id"] = "U2"
        fake.events = []
        self.deploy(fake)
        self.assertEqual([u["username"] for u in fake.users], ["show_jarvis_show_5"])
        self.assertEqual(fake.events[-1], ("delete user", "U2"))

    def test_the_owner_keeps_using_the_second_name(self):
        fake = OwnUserHA(users=[{"id": "U2", "name": "Jarvis Show 5", "username": "show_jarvis_show_5-2",
                                 "group_ids": ["system-users"]}])
        fake.passwords["show_jarvis_show_5-2"] = "old"
        self.deploy(fake)
        self.assertEqual([u["id"] for u in fake.users], ["U2"])
        self.assertNotEqual(fake.passwords["show_jarvis_show_5-2"], "old")
        self.assertNotIn(("delete user", "U2"), fake.events)

    def test_the_owner_tidies_up_both_names(self):
        fake = OwnUserHA(users=[{"id": "U1", "name": "Jarvis Show 5", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-users"]},
                                {"id": "U2", "name": "Jarvis Show 5", "username": "show_jarvis_show_5-2",
                                 "group_ids": ["system-users"]}])
        self.deploy(fake)
        self.assertEqual([u["id"] for u in fake.users], ["U1"])
        self.assertEqual(fake.events[0][0], "hand over")
        self.assertIn(("delete user", "U2"), fake.events)

    def test_both_names_taken_and_no_owner_changes_nothing(self):
        fake = OwnUserHA(users=[{"id": "U1", "name": "Jarvis Show 5", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-users"]},
                                {"id": "U2", "name": "Jarvis Show 5", "username": "show_jarvis_show_5-2",
                                 "group_ids": ["system-users"]}])
        fake.owner = False
        with self.assertRaisesRegex(ha.HomeAssistantError, "both 'show_jarvis_show_5' and 'show_jarvis_show_5-2'"):
            self.deploy(fake)
        self.assertEqual([u["id"] for u in fake.users], ["U1", "U2"])
        self.assertEqual(fake.events, [])

    def test_old_tokens_go_only_after_the_show_stored_the_new_one(self):
        fake = OwnUserHA()
        self.deploy(fake)
        self.assertEqual(fake.events, [("hand over", "/api/services/esphome/jarvis_show_5_home_assistant"
                                                     "?return_response"), ("delete token", "R-old")])

    def test_a_show_that_cannot_say_keeps_the_old_tokens(self):
        # A Show from before GENBU-158: Home Assistant refuses return_response, the token goes over the
        # old way, and as nothing says it arrived, the one the Show had stays.
        fake = OwnUserHA(users=[{"id": "U1", "name": "Jarvis Show 5", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-users"]}])
        fake.owner = False
        fake.show_answers = False
        log = self.deploy(fake)
        self.assertEqual([e for e in fake.events if e[0] != "hand over"], [])
        self.assertEqual([p for _, p in fake.events], [
            "/api/services/esphome/jarvis_show_5_home_assistant?return_response",
            "/api/services/esphome/jarvis_show_5_home_assistant"])
        self.assertEqual([u["id"] for u in fake.users], ["U1", "U-new"])
        self.assertIn("R-old", [t["id"] for t in fake.llats])
        self.assertTrue(any(line.startswith("WARN: the Show did not say it stored its new token")
                            and "'show_jarvis_show_5'" in line for line in log))

    def test_only_stored_true_counts(self):
        for says in ({"stored": False}, {"stored": "yes"}, {}, None):
            fake = OwnUserHA()
            fake.show_says = says
            self.deploy(fake)
            self.assertEqual([e[0] for e in fake.events], ["hand over"], says)
            self.assertIn("R-old", [t["id"] for t in fake.llats])

    def test_a_failed_handover_keeps_everything(self):
        fake = OwnUserHA(users=[{"id": "U1", "name": "Jarvis Show 5", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-users"]}])
        fake.show_fails = "Failed to execute action home_assistant on jarvis-show-5: disk full"
        with self.assertRaisesRegex(ha.HomeAssistantError, "disk full"):
            self.deploy(fake)
        self.assertEqual([e[0] for e in fake.events], ["hand over"])
        self.assertIn("R-old", [t["id"] for t in fake.llats])

    def test_no_current_token_means_nothing_is_deleted(self):
        fake = OwnUserHA()
        fake.current = False
        log = self.deploy(fake)
        self.assertEqual([e[0] for e in fake.events], ["hand over"])
        self.assertTrue(any("did not say which token" in line for line in log))

    def test_an_admin_with_that_name_is_not_taken_over(self):
        fake = OwnUserHA(users=[{"id": "U1", "name": "x", "username": "show_jarvis_show_5",
                                 "group_ids": ["system-admin"]}])
        with self.assertRaisesRegex(ha.HomeAssistantError, "admin"):
            self.deploy(fake)
        self.assertEqual(fake.posted("/api/services/esphome/"), [])

    def test_no_action_on_the_show_means_no_new_user(self):
        fake = OwnUserHA()
        plain = FakeHA.request

        def request(method, path, body=None, **kw):
            if path == "/api/services":
                return [{"domain": "esphome", "services": {}}]
            return OwnUserHA.request(fake, method, path, body, **kw) if kw else plain(fake, method, path, body)
        fake.request = request
        log = self.deploy(fake)
        self.assertEqual(fake.users, [])
        self.assertTrue(any("as its own user was not given" in line for line in log))

    def test_failed_sign_in_says_why_and_keeps_the_password_out(self):
        fake = OwnUserHA()
        fake.passwords = mock.MagicMock()
        fake.passwords.get.return_value = "never"
        with self.assertRaisesRegex(ha.HomeAssistantError, "wrong username or password") as cm:
            ha.make_show_user(fake, "Jarvis Show 5", progress=lambda _l: None)
        self.assertNotIn(fake.passwords.__setitem__.call_args[0][1], str(cm.exception))

    def test_client_signs_in_without_a_bearer_and_as_a_form(self):
        seen = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen.append(req)
            return Resp(b'{"ok": true}')
        client = ha.HomeAssistant("http://ha:8123", TOKEN, opener=opener)
        self.assertEqual(client.request("POST", "/auth/token", {"a": "b c"}, auth=False, form=True), {"ok": True})
        self.assertIsNone(seen[0].get_header("Authorization"))
        self.assertEqual(seen[0].data, b"a=b+c")
        self.assertEqual(seen[0].get_header("Content-type"), "application/x-www-form-urlencoded")
        other = client.as_user("OTHER")
        other.request("GET", "/api/")
        self.assertEqual(seen[1].get_header("Authorization"), "Bearer OTHER")

    def test_gather_skips_the_show_token_and_needs_the_admin_one(self):
        keyring = {"ha-token": TOKEN, "ha-admin-token": "admin-token"}
        s = gather(args(ha_url="http://ha:8123", own_ha_user=True), Asker(False), defaults={},
                   lookup=lambda secret, **attrs: keyring.get(secret))
        self.assertEqual((s.ha_token, s.ha_admin_token, s.own_ha_user), (None, "admin-token", True))
        self.assertIn("Home Assistant: http://ha:8123, as a user of its own", s.summary())
        with self.assertRaisesRegex(SettingsError, "needs an admin"):
            gather(args(ha_url="http://ha:8123", own_ha_user=True), Asker(False), defaults={},
                   lookup=lambda secret, **attrs: None)

    def test_a_hand_made_token_file_wins_over_the_own_user(self):
        with tempfile.TemporaryDirectory() as td:
            token = pathlib.Path(td) / "t"
            token.write_text(TOKEN + "\n")
            keyring = {"ha-admin-token": "admin-token"}
            s = gather(args(ha_url="http://ha:8123", ha_token_file=token), Asker(False), defaults={},
                       lookup=lambda secret, **attrs: keyring.get(secret))
            self.assertEqual((s.ha_token, s.ha_admin_token, s.own_ha_user), (TOKEN, "admin-token", False))
            with self.assertRaisesRegex(SettingsError, "two different"):
                gather(args(ha_url="http://ha:8123", ha_token_file=token, own_ha_user=True), Asker(False),
                       defaults={}, lookup=lambda secret, **attrs: keyring.get(secret))
