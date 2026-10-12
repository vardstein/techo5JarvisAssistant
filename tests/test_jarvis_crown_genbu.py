import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import genbu  # noqa: E402
from jarvis_crown import home_assistant as ha  # noqa: E402

NODE = "kitchen-show"


class FakeHouse:
    """Home Assistant's REST answers around the Genbu integration, for the Show NODE in ESPHome entry E1."""

    def __init__(self, *, loaded=True, entry=False, mains=(), version="v1.0.3", statuses=(), offered=True):
        self.loaded = loaded            # the integration's files are there and Home Assistant was restarted
        self.entry = entry
        self.mains = list(mains)
        self.extra = list(mains[1:])
        self.version = version
        self.statuses = list(statuses)  # what the Show's update_status says, one per look
        self.offered = offered          # the integration has seen the Show
        self.notify = "mobile_app_phone"
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
            options = [[NODE, f"Kitchen Show · {self.version}"]] if self.offered else []
            return {"type": "form", "flow_id": "F1", "step_id": "user",
                    "data_schema": [{"name": "main_show", "type": "select", "options": options, "optional": True}]}
        if method == "POST" and path == "/api/config/config_entries/flow/F1":
            self.entry = True
            if body.get("main_show"):
                self.mains = [body["main_show"]]
            return {"type": "create_entry"}
        if method == "POST" and path == "/api/config/config_entries/options/flow":
            return {"type": "form", "flow_id": "O1", "step_id": "init", "data_schema": [
                {"name": "extra_mains", "type": "multi_select", "default": self.extra,
                 "options": {NODE: "Kitchen Show"} if self.offered else {}},
                {"name": "notify_service", "type": "string", "description": {"suggested_value": self.notify}}]}
        if method == "POST" and path == "/api/config/config_entries/options/flow/O1":
            if "next_step_id" in body:
                return {"type": "create_entry"}
            self.extra, self.notify = body["extra_mains"], body["notify_service"]
            return {"type": "menu", "flow_id": "O1", "step_id": "second", "menu_options": ["second_no", "second_yes"]}
        if method == "DELETE":
            return None
        if path == "/api/states/sensor.genbu_house_version":
            if not self.entry:
                raise ha.NotFound("404")
            return {"state": self.version or "unknown",
                    "attributes": {"main_shows": self.mains[:1] + [m for m in self.extra if m not in self.mains[:1]]}}
        if path == "/api/states/select.genbu_main_update_show":
            return {"state": "unknown", "attributes": {"options": [NODE] if self.offered else []}}
        if path == "/api/services/select/select_option":
            self.mains = [body["option"]]
            return []
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


def run(fake, *, role=None, answers=None, wait=900.0):
    lines, asked, clock = [], [], Clock()

    def choose(label, current, options, names):
        asked.append(label)
        return (answers or {}).get(label)

    result = genbu.set_up(fake, "E1", NODE, role=role, choose=choose if answers is not None else None,
                          progress=lines.append, sleep=clock.sleep, clock=clock, wait=wait)
    return result, lines, asked


class NoIntegrationTest(unittest.TestCase):
    def test_without_it_the_show_stays_on_its_own_and_says_how_to_add_it(self):
        fake = FakeHouse(loaded=False)
        result, lines, _ = run(fake)
        self.assertEqual(result, genbu.SKIPPED)
        self.assertTrue(any("/config/custom_components/genbu/" in line for line in lines), lines)
        self.assertFalse(fake.entry)

    def test_again_after_copying_sets_it_up(self):
        fake = FakeHouse(loaded=False)
        tries = iter([False, True])

        def request(method, path, body=None):
            if path == "/api/config/config_entries/flow" and method == "POST":
                fake.loaded = next(tries)
            return FakeHouse.request(fake, method, path, body)
        fake.request = request
        result, _, asked = run(fake, answers={"genbu missing": "again", "main update show": "yes"})
        self.assertEqual(result, genbu.MAIN)
        self.assertEqual(asked, ["genbu missing", "main update show"])
        self.assertEqual(fake.mains, [NODE])

    def test_asked_nothing_it_does_not_set_genbu_up_by_itself(self):
        fake = FakeHouse()
        result, _, _ = run(fake)
        self.assertEqual(result, genbu.SKIPPED)
        self.assertFalse(fake.entry)
        self.assertIn(("DELETE", "/api/config/config_entries/flow/F1", None), fake.calls)


class NoMainTest(unittest.TestCase):
    def test_the_first_show_is_made_main_through_the_setup_form(self):
        fake = FakeHouse()
        result, lines, _ = run(fake, answers={"main update show": "yes"})
        self.assertEqual(result, genbu.MAIN)
        self.assertEqual(fake.posted("/api/config/config_entries/flow/F1"), [{"main_show": NODE}])
        self.assertIn(f"{NODE} is now the main update Show", lines)

    def test_a_show_the_form_does_not_list_yet_is_made_main_through_the_select(self):
        fake = FakeHouse(offered=False)
        offered = iter([False, True])

        def request(method, path, body=None):
            if path == "/api/states/select.genbu_main_update_show":
                fake.offered = next(offered, True)
            return FakeHouse.request(fake, method, path, body)
        fake.request = request
        result, _, _ = run(fake, role=genbu.MAIN)
        self.assertEqual(result, genbu.MAIN)
        self.assertEqual(fake.posted("/api/config/config_entries/flow/F1"), [{}])
        self.assertEqual(fake.mains, [NODE])

    def test_set_up_but_without_a_main_the_switch_makes_it_main(self):
        fake = FakeHouse(entry=True, version="")
        result, _, asked = run(fake, role=genbu.MAIN, answers={})
        self.assertEqual((result, asked, fake.mains), (genbu.MAIN, [], [NODE]))

    def test_no_leaves_the_house_without_a_main(self):
        fake = FakeHouse(entry=True, version="")
        result, lines, _ = run(fake, answers={"main update show": "no"})
        self.assertEqual((result, fake.mains), (genbu.FOLLOW, []))
        self.assertTrue(any("select.genbu_main_update_show" in line for line in lines), lines)


class OneMainTest(unittest.TestCase):
    def test_follow_is_the_default_and_waits_for_the_show_to_move(self):
        fake = FakeHouse(entry=True, mains=["living-show"], statuses=[
            "Waiting for this version to settle before moving to v1.0.3", "unavailable",
            "Following living-show · version v1.0.3"])
        result, lines, asked = run(fake, answers={"follow main": "follow"})
        self.assertEqual((result, asked), (genbu.FOLLOW, ["follow main"]))
        self.assertIn("living-show is your main update Show (version v1.0.3); kitchen-show follows", lines)
        self.assertIn("restarting", lines)
        self.assertEqual(lines[-1], "Following living-show · version v1.0.3")
        self.assertEqual(fake.posted("/api/services/select/select_option"), [])

    def test_a_version_that_will_not_install_is_said_and_not_waited_out(self):
        fake = FakeHouse(entry=True, mains=["living-show"],
                         statuses=["Version v1.0.3 did not install here; staying on v1.0.2"])
        _, lines, _ = run(fake, role=genbu.FOLLOW)
        self.assertEqual(lines[-1], "WARN: Version v1.0.3 did not install here; staying on v1.0.2")

    def test_waiting_ends_and_leaves_it_to_the_show(self):
        fake = FakeHouse(entry=True, mains=["living-show"], statuses=["Moving to v1.0.3 once nobody is using this Show"])
        _, lines, _ = run(fake, role=genbu.FOLLOW, wait=60)
        self.assertIn("carries on by itself", lines[-1])

    def test_a_second_main_needs_the_warning_answered_yes(self):
        fake = FakeHouse(entry=True, mains=["living-show"])
        result, _, asked = run(fake, answers={"follow main": "main", "second main": "no"})
        self.assertEqual((result, asked), (genbu.FOLLOW, ["follow main", "second main"]))
        self.assertEqual(fake.posted("/api/config/config_entries/options/flow"), [])

        result, _, _ = run(fake, answers={"follow main": "main", "second main": "yes"})
        self.assertEqual(result, genbu.MAIN)
        self.assertEqual(fake.posted("/api/config/config_entries/options/flow/O1"), [
            {"extra_mains": [NODE], "notify_service": "mobile_app_phone"}, {"next_step_id": "second_yes"}])
        self.assertEqual(fake.mains, ["living-show"], "the first main stays first")


class TwoMainsTest(unittest.TestCase):
    def test_both_are_named_and_the_show_follows(self):
        fake = FakeHouse(entry=True, mains=["living-show", "office-show"],
                         statuses=["Following living-show · version v1.0.3"])
        result, lines, _ = run(fake, role=genbu.FOLLOW)
        self.assertEqual(result, genbu.FOLLOW)
        self.assertIn("living-show and office-show are your main update Shows (version v1.0.3); kitchen-show follows",
                      lines)

    def test_a_third_main_keeps_the_others(self):
        fake = FakeHouse(entry=True, mains=["living-show", "office-show"])
        run(fake, role=genbu.MAIN)
        self.assertEqual(fake.extra, ["office-show", NODE])

    def test_a_show_that_is_main_already_is_left_alone(self):
        fake = FakeHouse(entry=True, mains=["living-show", NODE])
        result, lines, asked = run(fake, answers={})
        self.assertEqual((result, asked), (genbu.MAIN, []))
        self.assertEqual(lines, [f"{NODE} is a main update Show"])


if __name__ == "__main__":
    unittest.main()
