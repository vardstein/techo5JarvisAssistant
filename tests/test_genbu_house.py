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
    def test_newest_committed_among_the_mains(self):
        h = house.House({"mains": ["hall", "kitchen"]})
        h.see([show("hall", "v1.0.2-stable.48"), show("kitchen", "v1.0.3-stable.52"),
               show("attic", "v1.0.4-dev.60")], NOW)
        self.assertEqual(h.version, "v1.0.3-stable.52")  # attic is no main, it cannot pull the house
        self.assertEqual(h.since, NOW.isoformat())

    def test_installed_but_not_committed_does_not_move_the_house(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", "v1.0.2-stable.48")], NOW)
        h.see([show("hall", "v1.0.2-stable.48", installed="v1.0.3-stable.52")], NOW)
        self.assertEqual(h.version, "v1.0.2-stable.48")

    def test_kept_while_the_main_is_unavailable(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", "v1.0.3-stable.52")], NOW)
        h.see([show("hall", "unavailable", online=False)], NOW + timedelta(hours=3))
        self.assertEqual(h.version, "v1.0.3-stable.52")
        self.assertEqual(h.data["committed"]["hall"], "v1.0.3-stable.52")

    def test_no_main_keeps_the_last_house_version(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", "v1.0.3-stable.52")], NOW)
        h.replace("hall", None, NOW)
        h.see([show("hall", "v1.0.4-stable.53")], NOW)
        self.assertEqual(h.version, "v1.0.3-stable.52")

    def test_a_rollback_on_the_main_does_not_move_the_house(self):
        # A release that broke the main never commits, so the main reports the old version again.
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", "v1.0.3-stable.52")], NOW)
        h.see([show("hall", "v1.0.3-stable.52", installed="v1.0.4-stable.53")], NOW)
        self.assertEqual(h.version, "v1.0.3-stable.52")

    def test_survives_a_restart(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", "v1.0.3-stable.52")], NOW)
        again = house.House(json.loads(json.dumps(h.data)))
        self.assertEqual((again.version, again.mains), ("v1.0.3-stable.52", ["hall"]))

    def test_see_says_when_something_changed(self):
        h = house.House({"mains": ["hall"]})
        self.assertTrue(h.see([show("hall", "v1.0.3-stable.52")], NOW))
        self.assertFalse(h.see([show("hall", "v1.0.3-stable.52")], NOW + timedelta(minutes=1)))


class MissingTests(unittest.TestCase):
    def test_timeline(self):
        since = NOW
        for after, want in [
            (timedelta(0), house.QUIET),
            (timedelta(minutes=59), house.QUIET),
            (timedelta(hours=1), house.STATUS),
            (timedelta(hours=23), house.STATUS),
            (timedelta(hours=24), house.ASK),
            (timedelta(days=6, hours=23), house.ASK),
            (timedelta(days=7), house.PICK),
        ]:
            self.assertEqual(house.missing_step(since, since + after), want, after)
        self.assertEqual(house.missing_step(None, NOW), house.PRESENT)

    def test_keep_waiting_moves_the_pick(self):
        since = NOW
        answered = since + timedelta(days=2)
        wait = answered + house.WAIT_MORE
        self.assertEqual(house.missing_step(since, since + timedelta(days=7), wait), house.STATUS)
        self.assertEqual(house.missing_step(since, wait - timedelta(hours=24), wait), house.ASK)
        self.assertEqual(house.missing_step(since, wait, wait), house.PICK)

    def test_one_entry_per_main_however_many_shows(self):
        h = house.House({"mains": ["hall"]})
        shows = [show("hall", "v1.0.3-stable.52", online=False)] + [show(f"room{i}") for i in range(10)]
        h.see(shows, NOW)
        self.assertEqual(h.missing(NOW + timedelta(days=1)), {"hall": house.ASK})

    def test_missing_since_only_when_every_main_is_gone(self):
        h = house.House({"mains": ["hall", "kitchen"]})
        h.see([show("hall", online=False), show("kitchen")], NOW)
        self.assertEqual(h.missing_since(), "")
        later = NOW + timedelta(hours=2)
        h.see([show("hall", online=False), show("kitchen", online=False)], later)
        self.assertEqual(h.missing_since(), later.isoformat())

    def test_coming_back_clears_the_spell(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", online=False)], NOW)
        h.wait_more("hall", NOW + timedelta(days=1))
        self.assertTrue(h.tell_once("hall"))
        self.assertFalse(h.tell_once("hall"))
        h.see([show("hall")], NOW + timedelta(days=2))
        self.assertEqual((h.missing_since(), h.data["wait_until"], h.data["told"]), ("", {}, {}))
        self.assertEqual(h.missing(NOW), {"hall": house.PRESENT})

    def test_a_show_absent_from_home_assistant_counts_as_gone(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("kitchen")], NOW)
        self.assertEqual(h.gone_since("hall"), NOW)


class PickTests(unittest.TestCase):
    def test_recommend_prefers_the_house_version_then_the_newest(self):
        shows = [show("attic", "v1.0.4-dev.60"), show("kitchen", "v1.0.3-stable.52"),
                 show("bath", "v1.0.3-stable.52"), show("cellar", "v1.0.5", online=False)]
        self.assertEqual(house.recommend(shows, "v1.0.3-stable.52"), "bath")
        self.assertEqual(house.recommend(shows, "v0.9.0"), "attic")
        self.assertEqual(house.recommend(shows, "v1.0.3-stable.52", leaving="bath"), "kitchen")
        self.assertIsNone(house.recommend([show("cellar", online=False)], ""))

    def test_recommend_takes_a_show_without_a_version_last(self):
        shows = [show("new", "unknown"), show("old", "v0.9.0")]
        self.assertEqual(house.recommend(shows, ""), "old")

    def test_auto_pick_takes_the_recommended_show(self):
        h = house.House({"mains": ["hall"]})
        shows = [show("hall", "v1.0.3-stable.52")]
        h.see(shows, NOW)
        shows = [show("hall", online=False), show("kitchen", "v1.0.3-stable.52"), show("attic", "v1.0.4-dev.60")]
        h.see(shows, NOW)
        self.assertEqual(h.auto_pick("hall", shows, NOW + timedelta(days=7)), "kitchen")
        self.assertEqual(h.mains, ["kitchen"])
        self.assertEqual(h.version, "v1.0.3-stable.52")
        self.assertEqual(h.missing(NOW), {"kitchen": house.PRESENT})

    def test_auto_pick_with_another_main_just_drops_the_gone_one(self):
        h = house.House({"mains": ["hall", "kitchen"]})
        shows = [show("hall", online=False), show("kitchen", "v1.0.3-stable.52"), show("attic")]
        h.see(shows, NOW)
        self.assertEqual(h.auto_pick("hall", shows, NOW), "")
        self.assertEqual(h.mains, ["kitchen"])

    def test_auto_pick_waits_with_nothing_online(self):
        h = house.House({"mains": ["hall"]})
        shows = [show("hall", online=False), show("kitchen", online=False)]
        h.see(shows, NOW)
        self.assertIsNone(h.auto_pick("hall", shows, NOW))
        self.assertEqual(h.mains, ["hall"])

    def test_old_main_comes_back_as_a_follower(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", online=False), show("kitchen", "v1.0.3-stable.52")], NOW)
        h.replace("hall", "kitchen", NOW)
        h.see([show("hall", "v1.0.2-stable.48"), show("kitchen", "v1.0.3-stable.52")], NOW)
        self.assertEqual(h.mains, ["kitchen"])
        self.assertEqual(h.version, "v1.0.3-stable.52")

    def test_make_main_and_extras(self):
        h = house.House()
        h.make_main("hall", NOW)
        h.set_extra(["kitchen", "hall", "kitchen"], NOW)
        self.assertEqual(h.mains, ["hall", "kitchen"])
        h.make_main("kitchen", NOW)
        self.assertEqual(h.mains, ["kitchen"])
        h.set_extra([], NOW)
        self.assertEqual(h.mains, ["kitchen"])

    def test_replace_keeps_the_place(self):
        h = house.House({"mains": ["hall", "kitchen"]})
        h.replace("hall", "attic", NOW)
        self.assertEqual(h.mains, ["attic", "kitchen"])
        h.replace("attic", "kitchen", NOW)
        self.assertEqual(h.mains, ["kitchen"])

    def test_forgets_state_of_shows_that_are_no_main_any_more(self):
        h = house.House({"mains": ["hall"]})
        h.see([show("hall", online=False)], NOW)
        h.wait_more("hall", NOW)
        h.make_main("kitchen", NOW)
        self.assertEqual((h.data["missing_since"], h.data["wait_until"]), ({}, {}))


    def test_a_renamed_main_keeps_its_place(self):
        h = house.House({"mains": ["hall", "attic"]})
        hall = show("hall", "v1.0.3-stable.52")
        hall.device = "d1"
        h.see([hall, show("attic", online=False)], NOW)
        flur = show("flur", "v1.0.3-stable.52")
        flur.device = "d1"
        h.see([flur, show("attic", online=False)], NOW + timedelta(minutes=1))
        self.assertEqual(h.mains, ["flur", "attic"])
        self.assertEqual(h.missing(NOW), {"flur": house.PRESENT, "attic": house.QUIET})
        self.assertEqual(h.data["committed"], {"flur": "v1.0.3-stable.52"})
        self.assertEqual(h.version, "v1.0.3-stable.52")


class ForgetTests(unittest.TestCase):
    """genbu.forget_show, which the installer's remove-show calls once the Show is out of ESPHome."""

    def house_of(self, mains):
        h = house.House()
        flur, attic = show("flur", "v1.0.3-stable.52"), show("attic", "v1.0.3-stable.50")
        flur.device, attic.device = "d1", "d2"
        for node in reversed(mains):
            h.make_main(node, NOW)
        h.see([flur, attic], NOW)
        h.choose_music("d1", "media_player.kitchen")
        h.choose_music("d2", "media_player.attic")
        return h

    def test_a_follower_leaves_no_trace(self):
        h = self.house_of(["flur"])
        h.forget("attic", NOW)
        self.assertEqual(h.mains, ["flur"])
        self.assertNotIn("attic", h.data["committed"])
        self.assertEqual(h.data["devices"], {"d1": "flur"})
        self.assertEqual(h.data["music"], {"d1": "media_player.kitchen"})

    def test_the_only_main_hands_over(self):
        h = self.house_of(["flur"])
        h.see([show("flur", online=False)], NOW + timedelta(hours=30))
        h.forget("flur", NOW + timedelta(hours=30), new="attic")
        self.assertEqual(h.mains, ["attic"])
        self.assertEqual(h.data["missing_since"], {})
        self.assertNotIn("flur", h.data["committed"])
        self.assertEqual(h.data["devices"], {"d2": "attic"})
        self.assertEqual(h.data["music"], {"d2": "media_player.attic"})

    def test_a_second_main_just_leaves(self):
        h = self.house_of(["flur", "attic"])
        h.forget("attic", NOW)
        self.assertEqual(h.mains, ["flur"])
        h.forget("flur", NOW)
        self.assertEqual(h.mains, [])
        self.assertEqual(h.data["devices"], {})

    def test_an_unknown_show_changes_nothing(self):
        h = self.house_of(["flur"])
        before = repr(h.data)
        h.forget("cellar", NOW)
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

    def test_every_issue_has_its_texts(self):
        strings = json.loads((GENBU / "strings.json").read_text())
        for issue in ("main_missing", "no_main", "main_picked", "main_dropped"):
            self.assertIn("title", strings["issues"][issue], issue)
        steps = strings["issues"]["main_missing"]["fix_flow"]["step"]
        self.assertEqual(set(steps["init"]["menu_options"]), {"pick", "wait", "gone"})
        self.assertEqual(set(strings["options"]["step"]["second"]["menu_options"]), {"second_no", "second_yes"})

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
        for needle in ("sensor.genbu_house_version", "main_shows", "main_missing_since"):
            self.assertIn(needle, go)
        self.assertIn('"sensor.genbu_house_version"', const)


if __name__ == "__main__":
    unittest.main()
