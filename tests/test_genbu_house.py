import importlib.util
import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENBU = ROOT / "ha" / "custom_components" / "genbu"
SPEC = importlib.util.spec_from_file_location("genbu_house", GENBU / "house.py")
house = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = house  # dataclasses look their module up
SPEC.loader.exec_module(house)

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def show(node, committed="", online=True, installed=None):
    return house.Show(node=node, name=node.title(), installed=committed if installed is None else installed,
                      committed=committed, online=online)


class VersionTests(unittest.TestCase):
    """The same ranking as echod's update/version.go, cases taken from its tests."""

    def test_channel_builds_rank_by_number(self):
        for offered, running, want in [
            ("v1.0.1-stable.10", "v1.0.1-staging.9", True),
            ("v1.0.1-staging.9", "v1.0.1-stable.10", False),
            ("v1.0.1-staging.10", "v1.0.1-dev.9", True),
            ("v1.0.1-dev.11", "v1.0.1-staging.10", True),
            ("v1.0.1-stable.10", "v1.0.1-dev.12", False),
            ("v1.0.1-stable.10", "v1.0.1-stable.9", True),
            ("v1.0.1-dev.12.2", "v1.0.1-dev.12", True),
            ("v1.0.1-stable.12", "v1.0.1-dev.12", True),
            ("v1.0.2-dev.1", "v1.0.1-stable.40", True),
            ("v1.0.1", "v1.0.1-stable.40", True),
            ("v1.0.1-rc.1", "v1.0.1-dev.9", True),
        ]:
            self.assertEqual(house.newer(offered, running), want, (offered, running))

    def test_newer(self):
        for a, b, want in [
            ("v0.9.10", "v0.9.9", True),
            ("v0.9.9", "v0.9.10", False),
            ("v0.9.9", "v0.9.9", False),
            ("v1.0.0-rc.1", "v0.9.10", True),
            ("v0.9.10", "v1.0.0-rc.1", False),
            ("v0.9.9", "family-test", False),
        ]:
            self.assertEqual(house.newer(a, b), want, (a, b))

    def test_build_detail_does_not_count(self):
        self.assertEqual(house.compare("v1.0.3-stable.52_abc123", "v1.0.3-stable.52"), 0)

    def test_valid(self):
        for v in ("v1.0.3-stable.52", "1.0.3", "v1.0.3-stable.52_g1a2b"):
            self.assertTrue(house.valid(v), v)
        for v in ("", None, "unavailable", "unknown", "v1.0", "family-test"):
            self.assertFalse(house.valid(v), v)


class HouseVersionTests(unittest.TestCase):
    """GENBU-119: no main update Show. The house version moves only by an install."""

    def test_a_new_house_starts_at_its_newest_show(self):
        h = house.House()
        self.assertTrue(h.see([show("hall", "v1.0.3-stable.52"), show("kitchen", "v1.0.2-stable.48")], NOW))
        self.assertEqual((h.version, h.since), ("v1.0.3-stable.52", NOW.isoformat()))

    def test_installed_but_not_committed_does_not_count(self):
        h = house.House()
        h.see([show("hall", "", installed="v1.0.4-stable.53")], NOW)
        self.assertEqual(h.version, "")

    def test_a_newer_show_does_not_move_the_house(self):
        h = house.House()
        h.see([show("hall", "v1.0.3-stable.52")], NOW)
        h.see([show("hall", "v1.0.3-stable.52"), show("attic", "v1.0.9-stable.70")], NOW)
        self.assertEqual(h.version, "v1.0.3-stable.52")

    def test_move_up_and_back(self):
        h = house.House()
        h.see([show("hall", "v1.0.3")], NOW)
        h.move("v1.0.4", NOW + timedelta(hours=1))
        self.assertEqual((h.version, h.before), ("v1.0.4", "v1.0.3"))
        h.move("v1.0.3", NOW + timedelta(hours=2))
        self.assertEqual((h.version, h.before), ("v1.0.3", "v1.0.4"))
        h.move("v1.0.3", NOW + timedelta(hours=3))  # the same version is no move
        self.assertEqual((h.before, h.since), ("v1.0.4", (NOW + timedelta(hours=2)).isoformat()))
        with self.assertRaises(ValueError):
            h.move("latest", NOW)

    def test_survives_a_restart_without_the_old_keys(self):
        h = house.House({"mains": ["hall"], "missing_since": {}, "wait_until": {}, "told": [], "seeded": True})
        h.see([show("hall", "v1.0.3-stable.52")], NOW)
        h.pick_channel("staging")
        h.set_night(True)
        again = house.House(json.loads(json.dumps(h.data)))
        self.assertEqual((again.version, again.channel, again.night), ("v1.0.3-stable.52", "staging", True))
        self.assertFalse(set(house.OLD_KEYS) & set(again.data))

    def test_see_says_when_something_changed(self):
        h = house.House()
        self.assertTrue(h.see([show("hall", "v1.0.3-stable.52")], NOW))
        self.assertFalse(h.see([show("hall", "v1.0.3-stable.52")], NOW + timedelta(minutes=1)))
        self.assertFalse(h.see([show("hall", online=False)], NOW + timedelta(minutes=2)))

    def test_a_renamed_show_keeps_its_version(self):
        h = house.House()
        hall = show("hall", "v1.0.3")
        hall.device = "d1"
        h.see([hall], NOW)
        flur = show("flur")
        flur.device = "d1"
        h.see([flur], NOW)
        self.assertEqual(h.data["committed"], {"flur": "v1.0.3"})


class ChannelTests(unittest.TestCase):
    def test_channel_of(self):
        for v, ch in (("v1.4.0", "stable"), ("v1.4.0-stable.60", "stable"), ("v1.4.0-staging.55", "staging"),
                      ("1.4.0-dev.70_abc", "dev"), ("v1.4.0-rc.1", "stable")):
            self.assertEqual(house.channel_of(v), ch, v)

    def test_the_house_reads_its_own_channel_until_one_is_picked(self):
        h = house.House()
        self.assertEqual(h.channel, "stable")
        h.see([show("hall", "v1.0.3-dev.70")], NOW)
        self.assertEqual(h.channel, "dev")
        h.pick_channel("stable")
        self.assertEqual(h.channel, "stable")
        with self.assertRaises(ValueError):
            h.pick_channel("beta")

    def test_manifest_addresses(self):
        self.assertTrue(house.manifest_url("stable").endswith("/releases/latest/download/manifest.json"))
        self.assertTrue(house.manifest_url("dev").endswith("/releases/download/channel-dev/manifest.json"))
        self.assertTrue(house.tag_url("v1.4.0").endswith("/releases/download/v1.4.0/manifest.json"))

    def test_the_addresses_are_echods(self):
        show = (ROOT / "echod" / "internal" / "update" / "releases_cronos.go").read_text()
        channel = (ROOT / "echod" / "internal" / "update" / "channel.go").read_text()
        self.assertIn(f'"{house.RELEASES}"', show)
        self.assertIn('"channel-dev"', channel)


class OverviewTests(unittest.TestCase):
    """GENBU-249: the update card lists every Show and where it is on its way to the house version."""

    def moving(self):
        return [
            show("hall", "v1.5.0"),
            house.Show(node="kitchen", name="Kitchen", installed="v1.4.0", online=True, updating=True, at=40),
            house.Show(node="bath", name="Bath", installed="v1.4.0", online=True, updating=True, at=100),
            house.Show(node="desk", name="Desk", installed="v1.4.0", online=True,
                       status="Moving to v1.5.0 once the music stops"),
            house.Show(node="attic", name="Attic", installed="v1.3.0", online=True,
                       status="Following the house · version v1.3.0"),
            show("cellar", "v1.2.0", online=False, installed=""),
        ]

    def test_each_stage(self):
        lines = dict(line.removeprefix("- **").split("**", 1)
                     for line in house.overview(self.moving(), "v1.5.0").splitlines() if line.startswith("- "))
        self.assertEqual(lines, {
            "Hall": " · v1.5.0 · up to date",
            "Kitchen": " · v1.4.0 · downloading 40%",
            "Bath": " · v1.4.0 · installing",
            "Desk": " · v1.4.0 · waiting: Moving to v1.5.0 once the music stops",
            # Its line is from before the house moved: no reason yet.
            "Attic": " · v1.3.0 · waiting",
            "Cellar": " · offline",
        })

    def test_the_head_counts_the_shows_online(self):
        self.assertTrue(house.overview(self.moving(), "v1.5.0").startswith("**1 of 5 Shows on v1.5.0**\n"))
        self.assertTrue(house.overview(self.moving(), "v1.5.0", "de").startswith("**1 von 5 Shows auf v1.5.0**\n"))
        self.assertIn("Kitchen** · v1.4.0 · lädt 40 %", house.overview(self.moving(), "v1.5.0", "de"))

    def test_each_step_and_failure(self):
        """GENBU-215: the step each Show is at, and a failed one with its reason."""
        def at(status, updating=False, at=None, lang="en"):
            s = house.Show(node="x", name="X", installed="v1.4.0", online=True, updating=updating, at=at,
                           status=status)
            return house.stage(s, "v1.5.0", lang)

        self.assertEqual(at("Checking v1.5.0", True, 0), "checking")
        self.assertEqual(at("Downloading v1.5.0", True, 40), "downloading 40%")
        self.assertEqual(at("Installing v1.5.0", True, 100), "installing")
        self.assertEqual(at("Restarting into v1.5.0", True, 100), "restarting")
        self.assertEqual(at("Restarting into v1.5.0", True, 100, "de"), "startet neu")
        self.assertEqual(at("Update to v1.5.0 failed: can't reach the update server (update: dial tcp: refused)"),
                         "⚠ failed: can't reach the update server")
        self.assertEqual(at("Update to v1.5.0 failed: update: something new"), "⚠ failed: update: something new")
        self.assertEqual(at("Version v1.5.0 did not install here (not enough space on the Show); staying on v1.4.0"),
                         "⚠ failed: not enough space on the Show")
        self.assertEqual(at("Version v1.5.0 did not install here; staying on v1.4.0"), "⚠ failed")
        self.assertEqual(at("Version v1.5.0 could not be fetched: can't reach the update server (dial tcp)"),
                         "⚠ failed: can't reach the update server")
        self.assertEqual(at("rolled back from v1.5.0"), "⚠ failed: didn't come back after the restart")
        self.assertEqual(at("rolled back from v1.5.0", lang="de"),
                         "⚠ fehlgeschlagen: kam nach dem Neustart nicht wieder")

    def test_the_show_writes_the_lines_the_card_reads(self):
        """The lines above are echod's: a change there has to be a change here."""
        firmware = (ROOT / "echod" / "internal" / "feature" / "firmware" / "firmware.go").read_text()
        house_go = (ROOT / "echod" / "internal" / "feature" / "firmware" / "house.go").read_text()
        for line in ('"Checking " + v', '"Downloading " + v', '"Installing " + v', '"Restarting into " + v',
                     '"Update to "+found.Version+" failed: "', '"rolled back from "'):
            self.assertIn(line, firmware)
        for line in ('" did not install here (" + f.why + "); staying on "', '" could not be fetched: "'):
            self.assertIn(line, house_go)

    def test_no_shows_no_list(self):
        self.assertEqual(house.overview([], "v1.5.0"), "")

    def test_oldest_behind(self):
        self.assertEqual(house.oldest_behind(self.moving(), "v1.5.0"), "v1.3.0")  # the cellar is offline
        self.assertEqual(house.oldest_behind([show("hall", "v1.5.0")], "v1.5.0"), "")
        # Going back: a Show still on the newer one is not behind.
        self.assertEqual(house.oldest_behind([show("hall", "v1.5.0")], "v1.4.0"), "")

    def test_every_language_has_every_word(self):
        self.assertEqual(set(house.STAGES["de"]), set(house.STAGES["en"]))


class ForgetTests(unittest.TestCase):
    """genbu.forget_show, which the installer's --remove-show calls once the Show is out of ESPHome."""

    def house_of(self):
        h = house.House()
        flur, attic = show("flur", "v1.0.3-stable.52"), show("attic", "v1.0.3-stable.50")
        flur.device, attic.device = "d1", "d2"
        h.see([flur, attic], NOW)
        h.choose_music("d1", "media_player.kitchen")
        h.choose_music("d2", "media_player.attic")
        h.set_switch("d2", "recipes", False)
        return h

    def test_it_leaves_no_trace(self):
        h = self.house_of()
        h.forget("attic")
        self.assertNotIn("attic", h.data["committed"])
        self.assertEqual(h.data["devices"], {"d1": "flur"})
        self.assertEqual(h.data["music"], {"d1": "media_player.kitchen"})
        self.assertEqual(h.data["switches"], {})
        self.assertEqual(h.version, "v1.0.3-stable.52")  # the house stays where it is

    def test_an_unknown_show_changes_nothing(self):
        h = self.house_of()
        before = repr(h.data)
        h.forget("cellar")
        self.assertEqual(repr(h.data), before)


class FilesTests(unittest.TestCase):
    def test_translations_have_the_same_keys(self):
        def keys(d, at=""):
            out = set()
            for k, v in d.items():
                out |= keys(v, f"{at}.{k}") if isinstance(v, dict) else {f"{at}.{k}"}
            return out

        strings = json.loads((GENBU / "strings.json").read_text())
        for lang in ("en", "de"):
            other = json.loads((GENBU / "translations" / f"{lang}.json").read_text())
            self.assertEqual(keys(other), keys(strings), lang)

    def test_no_main_update_issues_any_more(self):
        # The main update Show's issues went with 1.4.0; the only issues now are a Show's key (GENBU-196).
        strings = json.loads((GENBU / "strings.json").read_text())
        self.assertFalse(set(strings["issues"]) & {"main_missing", "no_main", "main_picked", "main_dropped"})
        self.assertEqual(set(strings["options"]["step"]), {"init"})
        self.assertNotIn("main", (GENBU / "repairs.py").read_text())

    def test_the_house_entities_have_their_texts(self):
        entity = json.loads((GENBU / "strings.json").read_text())["entity"]
        self.assertEqual(set(entity["select"]["release_channel"]["state"]), set(house.CHANNELS))
        self.assertIn("install_at_night", entity["switch"])
        self.assertIn("show_firmware", entity["update"])

    def test_forget_show_is_described(self):
        strings = json.loads((GENBU / "strings.json").read_text())
        self.assertIn("node", strings["services"]["forget_show"]["fields"])
        services = (GENBU / "services.yaml").read_text()
        self.assertTrue(services.startswith("forget_show:"))
        self.assertIn('SERVICE_FORGET = "forget_show"', (GENBU / "const.py").read_text())

    def test_manifest(self):
        m = json.loads((GENBU / "manifest.json").read_text())
        self.assertEqual((m["domain"], m["config_flow"], m["single_config_entry"]), ("genbu", True, True))
        self.assertIn("esphome", m["after_dependencies"])

    def test_fixed_entity_ids_match_the_show(self):
        go = (ROOT / "echod" / "internal" / "feature" / "firmware" / "house.go").read_text()
        const = (GENBU / "const.py").read_text()
        for needle in ("sensor.genbu_house_version", "main_shows", "update_waiting", "update_asks"):
            self.assertIn(needle, go)
        self.assertIn('"sensor.genbu_house_version"', const)


if __name__ == "__main__":
    unittest.main()
