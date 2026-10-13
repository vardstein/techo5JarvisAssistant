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


def run(fake, *, answers=None, wait=900.0):
    lines, asked, clock = [], [], Clock()

    def choose(label, current, options, names):
        asked.append(label)
        return (answers or {}).get(label)

    result = genbu.set_up(fake, "E1", NODE, choose=choose if answers is not None else None,
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
        self.assertIn("restarting", lines)
        self.assertEqual(lines[-1], "Following the house · version v1.0.3")
        self.assertEqual(fake.posted("/api/config/config_entries/flow"), [])

    def test_a_version_that_will_not_install_is_said_and_not_waited_out(self):
        fake = FakeHouse(entry=True, statuses=["Version v1.0.3 did not install here; staying on v1.0.2"])
        _, lines, _ = run(fake)
        self.assertEqual(lines[-1], "WARN: Version v1.0.3 did not install here; staying on v1.0.2")

    def test_waiting_ends_and_leaves_it_to_the_show(self):
        fake = FakeHouse(entry=True, statuses=["Moving to v1.0.3 once nobody is using this Show"])
        _, lines, _ = run(fake, wait=60)
        self.assertIn("carries on by itself", lines[-1])

    def test_the_house_reads_only_its_version(self):
        self.assertEqual(genbu.house(FakeHouse(entry=True)), genbu.House("v1.0.3"))
        self.assertEqual(genbu.house(FakeHouse(entry=True, version="")), genbu.House(""))


if __name__ == "__main__":
    unittest.main()
