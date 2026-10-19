import importlib.util
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import genbu  # noqa: E402
from jarvis_crown import home_assistant as ha  # noqa: E402

NODE = "kitchen-show"


class FakeHouse:
    """Home Assistant's REST answers around the Genbu integration, for the Show NODE in ESPHome entry E1."""

    def __init__(self, *, loaded=True, entry=False, version="v1.0.3", statuses=()):
        self.loaded = loaded            # the integration's files are there and Home Assistant was restarted
        self.entry = entry
        self.version = version          # the house version; '' while no Show has settled
        self.statuses = list(statuses)  # what the Show's update_status says, one per look
        self.calls = []

    def posted(self, path):
        return [b for m, p, b in self.calls if m == "POST" and p == path]

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/config/config_entries/entry?domain=genbu":
            return [{"entry_id": "G1", "domain": "genbu"}] if self.entry else []
        if method == "POST" and path == "/api/config/config_entries/flow":
            if not self.loaded:
                raise ha.NotFound("POST /api/config/config_entries/flow: HTTP 404 Invalid handler specified")
            return {"type": "form", "flow_id": "F1", "step_id": "user", "data_schema": []}
        if method == "POST" and path == "/api/config/config_entries/flow/F1":
            self.entry = True
            return {"type": "create_entry"}
        if path == "/api/states/sensor.genbu_house_version":
            if not self.entry:
                raise ha.NotFound("404")
            return {"state": self.version or "unknown", "attributes": {}}
        if path == "/api/template":
            return "sensor.kitchen_show_update_status\nupdate.kitchen_show_firmware\n"
        if path == "/api/states/sensor.kitchen_show_update_status":
            line = self.statuses.pop(0) if len(self.statuses) > 1 else (self.statuses or ["unknown"])[0]
            return {"state": line}
        raise AssertionError(f"unexpected {method} {path}")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def run(fake, *, answers=None, wait=900.0, enter=None):
    lines, asked, clock = [], [], Clock()

    def choose(label, current, options, names):
        asked.append(label)
        return (answers or {}).get(label)

    result = genbu.set_up(fake, "E1", NODE, choose=choose if answers is not None else None,
                          progress=lines.append, sleep=clock.sleep, clock=clock, wait=wait,
                          enter=enter and enter(clock))
    return result, lines, asked


def enter_after(looks):
    """A user who presses Enter during the given look at the status, or never with None."""
    def make(clock):
        seen = []

        def enter(seconds):
            seen.append(seconds)
            clock.sleep(seconds)
            return looks is not None and len(seen) >= looks
        return enter
    return make


class NoIntegrationTest(unittest.TestCase):
    def test_without_it_the_show_stays_on_its_own_and_says_how_to_add_it(self):
        fake = FakeHouse(loaded=False)
        result, lines, _ = run(fake)
        self.assertEqual(result, genbu.SKIPPED)
        self.assertTrue(any("/config/custom_components/genbu/" in line for line in lines), lines)
        self.assertFalse(fake.entry)

    def test_again_after_copying_sets_it_up(self):
        fake = FakeHouse(loaded=False, version="")
        tries = iter([False, True])

        def request(method, path, body=None):
            if path == "/api/config/config_entries/flow" and method == "POST":
                fake.loaded = next(tries)
            return FakeHouse.request(fake, method, path, body)
        fake.request = request
        result, lines, asked = run(fake, answers={"genbu missing": "again"})
        self.assertEqual((result, asked), (genbu.FOLLOW, ["genbu missing"]))
        self.assertIn("Genbu set up in Home Assistant", lines)


class FirstShowTest(unittest.TestCase):
    def test_genbu_is_set_up_without_a_question(self):
        fake = FakeHouse(version="")
        result, lines, asked = run(fake, answers={})
        self.assertEqual((result, asked), (genbu.FOLLOW, []))
        self.assertEqual(fake.posted("/api/config/config_entries/flow/F1"), [{}])
        self.assertIn("the house starts at the newest Show's version", lines[-1])
        self.assertIn(genbu.UPDATE, lines[-1])

    def test_with_no_questions_too(self):
        fake = FakeHouse(version="")
        result, _, _ = run(fake)
        self.assertEqual(result, genbu.FOLLOW)
        self.assertTrue(fake.entry)


class HouseTest(unittest.TestCase):
    def test_the_show_follows_the_house_and_is_waited_for(self):
        fake = FakeHouse(entry=True, statuses=[
            "Waiting for this version to settle before moving to v1.0.3", "unavailable",
            "Following the house · version v1.0.3"])
        result, lines, asked = run(fake, answers={})
        self.assertEqual((result, asked), (genbu.FOLLOW, []))
        self.assertTrue(lines[0].startswith(f"the house is on version v1.0.3; {NODE} follows it"), lines)
        self.assertIn(genbu.NO_STATUS, lines)
        self.assertNotIn("restarting", lines)  # an unavailable status is not proof of a restart
        self.assertEqual(lines[-1], "Following the house · version v1.0.3")
        self.assertEqual(fake.posted("/api/config/config_entries/flow"), [])

    def test_a_version_that_will_not_install_is_said_and_not_waited_out(self):
        fake = FakeHouse(entry=True, statuses=["Version v1.0.3 did not install here; staying on v1.0.2"])
        _, lines, _ = run(fake)
        self.assertEqual(lines[-1], "WARN: Version v1.0.3 did not install here; staying on v1.0.2")

    def test_a_failed_install_is_said_with_its_reason(self):
        line = "Update to v1.0.3 failed: not enough space on the Show (update: 3 bytes free on /data, need 9)"
        fake = FakeHouse(entry=True, statuses=["Downloading v1.0.3", line])
        _, lines, _ = run(fake)
        self.assertEqual(lines[-1], "WARN: " + line)

    def test_a_show_that_said_no_is_not_waited_for(self):
        fake = FakeHouse(entry=True, statuses=["Your other Shows are on v1.0.3; install it from the update card"])
        _, lines, _ = run(fake)
        self.assertEqual(lines[-1], "Your other Shows are on v1.0.3; install it from the update card")

    def test_a_show_ahead_of_the_house_is_not_waited_for(self):
        line = ("Your other Shows are on the older v1.0.3; this one steps back when you say yes to its question, "
                "or they all move up with an update on the Genbu Show card")
        _, lines, _ = run(FakeHouse(entry=True, statuses=[line]))
        self.assertEqual(lines[-1], line)

    def test_waiting_ends_and_leaves_it_to_the_show(self):
        fake = FakeHouse(entry=True, statuses=["Moving to v1.0.3 once nobody is using this Show"])
        _, lines, _ = run(fake, wait=60)
        self.assertIn("carries on by itself", lines[-1])

    def test_enter_ends_the_wait_and_leaves_it_to_the_show(self):
        fake = FakeHouse(entry=True, statuses=["Waiting for this version to settle before moving to v1.0.3"])
        _, lines, _ = run(fake, enter=enter_after(2))
        self.assertIn("Press Enter once the update is done, or to go on without waiting", lines[1])
        self.assertEqual(lines[-1], "going on; it carries on by itself (Waiting for this version to settle before "
                         "moving to v1.0.3); its update card in Home Assistant shows how far it is")
        self.assertEqual(sum(1 for c in fake.calls if c[1].endswith("_update_status")), 2)

    def test_following_goes_on_without_enter(self):
        fake = FakeHouse(entry=True, statuses=["Downloading v1.0.3", "Following the house · version v1.0.3"])
        _, lines, _ = run(fake, enter=enter_after(None))
        self.assertEqual(lines[-1], "Following the house · version v1.0.3")

    def test_never_pressed_it_still_ends_at_the_time(self):
        fake = FakeHouse(entry=True, statuses=["Moving to v1.0.3 once nobody is using this Show"])
        _, lines, _ = run(fake, wait=60, enter=enter_after(None))
        self.assertTrue(lines[-1].startswith("it carries on by itself"), lines[-1])

    def test_nobody_at_the_terminal_is_not_told_to_press_enter(self):
        _, lines, _ = run(FakeHouse(entry=True, statuses=["Following the house · version v1.0.3"]))
        self.assertFalse(any("Enter" in line for line in lines), lines)

    def test_the_house_reads_only_its_version(self):
        self.assertEqual(genbu.house(FakeHouse(entry=True)), genbu.House("v1.0.3"))
        self.assertEqual(genbu.house(FakeHouse(entry=True, version="")), genbu.House(""))


class Features(FakeHouse):
    """Genbu's switches for the Show on device D1: Updates by voice always, Recipes with a recipe search."""

    def __init__(self, *, recipes=True, switches=True):
        super().__init__(entry=True)
        self.keys = (["recipes"] if recipes else []) + ["voice_updates"]
        self.switches = switches

    def ws(self, msg):
        if msg["type"] == "config/device_registry/list":
            return [{"id": "D1", "config_entries": ["E1"]}, {"id": "D9", "config_entries": ["E9"]}]
        if msg["type"] == "config/entity_registry/list":
            mine = [{"platform": "genbu", "unique_id": f"G1_{k}_D1", "entity_id": f"switch.kitchen_{k}"}
                    for k in self.keys] if self.switches else []
            return mine + [{"platform": "genbu", "unique_id": "G1_recipes_D9", "entity_id": "switch.attic_recipes"}]
        raise AssertionError(f"unexpected ws {msg}")

    def request(self, method, path, body=None):
        if method == "POST" and path.startswith("/api/services/switch/"):
            self.calls.append((method, path, body))
            return []
        return super().request(method, path, body)

    def switched(self):
        return [(p.rsplit("/", 1)[1], b["entity_id"]) for m, p, b in self.calls if p.startswith("/api/services/")]


def features(fake, given=None, answers=None):
    lines, asked, clock = [], [], Clock()

    def choose(label, current, options, names):
        asked.append(label)
        return (answers or {}).get(label)

    genbu.features(fake, "E1", given or {}, choose=choose if answers is not None else None,
                   progress=lines.append, sleep=clock.sleep, clock=clock)
    return lines, asked


class FeaturesTest(unittest.TestCase):
    def test_both_are_asked_and_set_on_this_show_only(self):
        fake = Features()
        lines, asked = features(fake, answers={"recipes": "no", "voice updates": "yes"})
        self.assertEqual(asked, ["recipes", "voice updates"])
        self.assertEqual(fake.switched(), [("turn_off", "switch.kitchen_recipes"),
                                           ("turn_on", "switch.kitchen_voice_updates")])
        self.assertIn("Recipes off on this Show", lines)

    def test_recipes_is_not_asked_without_a_recipe_search(self):
        fake = Features(recipes=False)
        _, asked = features(fake, answers={"voice updates": "no"})
        self.assertEqual(asked, ["voice updates"])
        self.assertEqual(fake.switched(), [("turn_off", "switch.kitchen_voice_updates")])

    def test_switches_answer_and_nobody_at_the_terminal_leaves_the_rest(self):
        fake = Features()
        _, asked = features(fake, given={"recipes": True})
        self.assertEqual(asked, [])
        self.assertEqual(fake.switched(), [("turn_on", "switch.kitchen_recipes")])

    def test_a_show_genbu_has_not_seen_yet_points_to_the_page(self):
        fake = Features(switches=False)
        lines, asked = features(fake, answers={})
        self.assertEqual((asked, fake.switched()), ([], []))
        self.assertTrue(any("Genbu Control Center" in line for line in lines), lines)


class LlmHouse(Features):
    """Genbu's house switch for whether the assistant is a language model, as it is now."""

    def __init__(self, *, state="off", switch=True):
        super().__init__()
        self.state, self.switch = state, switch

    def ws(self, msg):
        found = super().ws(msg)
        if msg["type"] == "config/entity_registry/list" and self.switch:
            found.append({"platform": "genbu", "unique_id": "G1_llm_assistant",
                          "entity_id": "switch.genbu_llm_assistant"})
        return found

    def request(self, method, path, body=None):
        if path == "/api/states/switch.genbu_llm_assistant":
            self.calls.append((method, path, body))
            return {"state": self.state}
        return super().request(method, path, body)


def llm(fake, on=None, answer="unasked"):
    lines, asked = [], []

    def choose(label, current, options, names):
        asked.append((label, current))
        return answer
    genbu.llm_assistant(fake, on, choose=choose if answer != "unasked" else None, progress=lines.append)
    return lines, asked


class LlmAssistantTest(unittest.TestCase):
    """GENBU-240: one question for the whole house, shown as it is now, and Enter keeps it."""

    def test_asked_with_the_switch_as_it_is_and_set(self):
        fake = LlmHouse(state="off")
        lines, asked = llm(fake, answer="yes")
        self.assertEqual(asked, [("llm assistant", "no")])
        self.assertEqual(fake.switched(), [("turn_on", "switch.genbu_llm_assistant")])
        self.assertIn("Assistant uses an LLM: yes, for the whole house", lines)

    def test_enter_keeps_it(self):
        fake = LlmHouse(state="on")
        lines, asked = llm(fake, answer=None)
        self.assertEqual(asked, [("llm assistant", "yes")])
        self.assertEqual((fake.switched(), lines), ([], []))

    def test_the_switch_answers_without_asking(self):
        fake = LlmHouse(state="on")
        _, asked = llm(fake, on=False, answer="yes")
        self.assertEqual(asked, [])
        self.assertEqual(fake.switched(), [("turn_off", "switch.genbu_llm_assistant")])

    def test_nobody_at_the_terminal_and_no_switch_leave_it(self):
        fake = LlmHouse()
        self.assertEqual(llm(fake), ([], []))
        self.assertEqual(fake.switched(), [])

    def test_an_older_genbu_without_the_switch_is_left_alone(self):
        fake = LlmHouse(switch=False)
        self.assertEqual(llm(fake, on=True, answer="yes"), ([], []))
        self.assertEqual(fake.switched(), [])


class EnterAtTheTerminalTest(unittest.TestCase):
    """The installer's side of GENBU-222: what ends the wait at a real terminal."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("jarvis_show_cli_enter", ROOT / "tools" / "jarvis-show.py")
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def enter(self, interactive):
        asker = mock.Mock(interactive=interactive)
        with mock.patch.object(self.cli, "_asker", return_value=asker):
            return self.cli._enter(mock.Mock())

    def test_nobody_at_the_terminal_gets_none(self):
        self.assertIsNone(self.enter(False))

    def test_a_line_ends_the_wait_and_is_read(self):
        r, w = os.pipe()
        with os.fdopen(r) as stdin, os.fdopen(w, "w") as out, mock.patch.object(self.cli.sys, "stdin", stdin):
            pressed = self.enter(True)
            self.assertFalse(pressed(0.01))
            out.write("\n")
            out.flush()
            self.assertTrue(pressed(5))
            self.assertFalse(pressed(0.01))  # the line was used up

if __name__ == "__main__":
    unittest.main()
