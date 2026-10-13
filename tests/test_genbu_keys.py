import hashlib
import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENBU = ROOT / "ha" / "custom_components" / "genbu"
SPEC = importlib.util.spec_from_file_location("genbu_keys", GENBU / "keys.py")
keys = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = keys  # dataclasses look their module up
SPEC.loader.exec_module(keys)

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def show_user(**kw):
    kw.setdefault("id", "u1")
    kw.setdefault("usernames", ("show_kitchen_show",))
    kw.setdefault("tokens", ("rt1",))
    return keys.User(**kw)


class WhyTests(unittest.TestCase):
    """GENBU-196: why a Show was turned away, and so which Repairs issue it gets."""

    def test_nothing_to_say(self):
        for state in (keys.OK, keys.NONE, "", "unavailable", "unknown"):
            self.assertIsNone(keys.why(state, "", True, show_user()), state)

    def test_came_from_outside(self):
        # Whatever the user: the fix is the way in, never the user.
        self.assertEqual(keys.why(keys.REMOTE, "", False, None), keys.HOME_ONLY)
        self.assertEqual(keys.why(keys.REMOTE, "", True, show_user(home_only=False)), keys.HOME_ONLY)

    def test_switched_off(self):
        for state in (keys.GONE, keys.OFF):
            self.assertEqual(keys.why(state, "", True, show_user(active=False)), keys.USER_OFF)
        # Before the encryption check: an admin has to switch it on either way.
        self.assertEqual(keys.why(keys.GONE, "", False, show_user(active=False)), keys.USER_OFF)

    def test_no_key_over_plain_connections(self):
        self.assertEqual(keys.why(keys.GONE, "", False, show_user()), keys.NOT_ENCRYPTED)
        self.assertEqual(keys.why(keys.GONE, "", False, None), keys.NOT_ENCRYPTED)

    def test_no_user(self):
        self.assertEqual(keys.why(keys.GONE, "", True, None), keys.NO_USER)
        self.assertEqual(keys.why(keys.OFF, "", True, None), keys.NO_USER)

    def test_not_a_show_user(self):
        self.assertEqual(keys.why(keys.GONE, "", True, show_user(admin=True)), keys.NOT_A_SHOW_USER)
        self.assertEqual(keys.why(keys.GONE, "", True, show_user(home_only=False)), keys.NOT_A_SHOW_USER)
        # Even when Home Assistant still has its key: no try for a user like that.
        key = keys.key_name("rt1")
        self.assertEqual(keys.why(keys.GONE, key, True, show_user(admin=True)), keys.NOT_A_SHOW_USER)

    def test_key_still_there_tries_again(self):
        key = keys.key_name("rt1")
        self.assertEqual(keys.why(keys.GONE, key, True, show_user()), keys.TRY_AGAIN)
        self.assertEqual(keys.why(keys.OFF, key, True, show_user()), keys.TRY_AGAIN)

    def test_key_gone(self):
        self.assertEqual(keys.why(keys.GONE, keys.key_name("rt9"), True, show_user()), keys.KEY_GONE)
        self.assertEqual(keys.why(keys.GONE, "", True, show_user()), keys.KEY_GONE)
        self.assertEqual(keys.why(keys.GONE, "", True, show_user(tokens=())), keys.KEY_GONE)

    def test_only_the_fixable_get_a_button(self):
        self.assertEqual(set(keys.FIXABLE), {keys.KEY_GONE, keys.NO_USER, keys.HOME_ONLY})
        self.assertNotIn(keys.TRY_AGAIN, keys.FIXABLE)


class UserTests(unittest.TestCase):
    def test_key_name_matches_echod(self):
        # echod's hass.sum: hex SHA-256 of the refresh token id.
        self.assertEqual(keys.key_name("rt-id"), hashlib.sha256(b"rt-id").hexdigest())

    def test_usernames_are_the_installers(self):
        self.assertEqual(keys.usernames("kitchen_show"), ("show_kitchen_show", "show_kitchen_show-2"))

    def test_owner(self):
        a = show_user(id="a", tokens=("rt1", "rt2"))
        b = show_user(id="b", tokens=("rt3",))
        self.assertEqual(keys.owner(keys.key_name("rt2"), [a, b]), (a, "rt2"))
        self.assertEqual(keys.owner(keys.key_name("rt3"), [a, b]), (b, "rt3"))
        self.assertIsNone(keys.owner(keys.key_name("rt4"), [a, b]))
        self.assertIsNone(keys.owner("", [a, b]))

    def test_show_user_prefers_the_recorded_one(self):
        recorded = show_user(id="rec", usernames=("someone",))
        named = show_user(id="named", usernames=("show_kitchen_show",))
        self.assertIs(keys.show_user([named, recorded], "rec", "kitchen_show"), recorded)
        self.assertIs(keys.show_user([named, recorded], "", "kitchen_show"), named)
        self.assertIs(keys.show_user([named], "gone", "kitchen_show"), named)

    def test_show_user_by_second_name(self):
        second = show_user(id="s", usernames=("show_kitchen_show-2",))
        other = show_user(id="o", usernames=("show_hall_show",))
        self.assertIs(keys.show_user([other, second], "", "kitchen_show"), second)
        self.assertIsNone(keys.show_user([other], "", "kitchen_show"))

    def test_first_name_wins_over_second(self):
        first = show_user(id="1", usernames=("show_kitchen_show",))
        second = show_user(id="2", usernames=("show_kitchen_show-2",))
        self.assertIs(keys.show_user([second, first], "", "kitchen_show"), first)


class RenewTests(unittest.TestCase):
    def test_may_renew(self):
        self.assertIsNone(keys.may_renew(show_user(), True))
        self.assertIsNone(keys.may_renew(None, True))
        self.assertEqual(keys.may_renew(None, False), keys.NOT_ENCRYPTED)
        self.assertEqual(keys.may_renew(show_user(), False), keys.NOT_ENCRYPTED)
        self.assertEqual(keys.may_renew(show_user(active=False), True), keys.USER_OFF)
        self.assertEqual(keys.may_renew(show_user(admin=True), True), keys.NOT_A_SHOW_USER)
        self.assertEqual(keys.may_renew(show_user(home_only=False), True), keys.NOT_A_SHOW_USER)

    def test_retire_keeps_only_the_new_key(self):
        self.assertEqual(keys.retire(("a", "new", "b"), "new"), ["a", "b"])
        self.assertEqual(keys.retire(("new",), "new"), [])
        self.assertEqual(keys.retire((), "new"), [])

    def test_password_is_long_and_fresh(self):
        a, b = keys.password(), keys.password()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 40)

    def test_home_address(self):
        for url in ("", "http://homeassistant.local:8123", "https://ha.example.org", "http://10.0.0.5:8123"):
            self.assertIsNone(keys.home_address(url), url)
        for url in ("https://abc123.ui.nabu.casa", "https://ui.nabu.casa", "https://X.UI.NABU.CASA:443"):
            self.assertEqual(keys.home_address(url), "cloud_address", url)
        for url in ("homeassistant.local:8123", "ftp://ha.example.org", "http://", "http://[::1", "nabu.casa"):
            self.assertEqual(keys.home_address(url), "not_an_address", url)
        # Only the cloud's own name, not one that ends like it.
        self.assertIsNone(keys.home_address("https://notui.nabu.casa.example.org"))
        self.assertIsNone(keys.home_address("https://myui.nabu.casa.example.org"))
        self.assertIsNone(keys.home_address("https://notnabu.casa"))


class AskTests(unittest.TestCase):
    """Each ask is a call on the Show, so Genbu asks on a change, and a turned-away Show at most hourly."""

    def test_on_a_change(self):
        self.assertTrue(keys.ask(keys.OK, None, None, NOW))
        self.assertTrue(keys.ask(keys.GONE, keys.OK, NOW, NOW))
        self.assertTrue(keys.ask(keys.OK, keys.GONE, NOW, NOW))
        self.assertTrue(keys.ask(keys.REMOTE, keys.GONE, NOW, NOW))

    def test_not_for_other_states(self):
        for state in (keys.NONE, "", "unavailable", "unknown"):
            self.assertFalse(keys.ask(state, None, None, NOW), state)
            self.assertFalse(keys.ask(state, keys.OK, None, NOW), state)

    def test_ok_is_asked_once(self):
        self.assertFalse(keys.ask(keys.OK, keys.OK, None, NOW))
        self.assertFalse(keys.ask(keys.OK, keys.OK, NOW - timedelta(days=2), NOW))

    def test_refused_again_hourly(self):
        for state in keys.REFUSED:
            self.assertFalse(keys.ask(state, state, NOW - timedelta(minutes=59), NOW), state)
            self.assertTrue(keys.ask(state, state, NOW - keys.ASK_EVERY, NOW), state)
            self.assertTrue(keys.ask(state, state, None, NOW), state)

    def test_due(self):
        self.assertTrue(keys.due(None, NOW))
        self.assertFalse(keys.due(NOW, NOW))
        self.assertTrue(keys.due(NOW - timedelta(hours=1), NOW))
        self.assertFalse(keys.due(NOW - timedelta(minutes=59, seconds=59), NOW))


class TextTests(unittest.TestCase):
    """Every issue kind and refusal has its words, in English and German, with the same placeholders."""

    def load(self, name):
        import json
        return json.loads((GENBU / name).read_text())

    def test_every_issue_has_text(self):
        import re
        en = self.load("strings.json")
        self.assertEqual(en, self.load("translations/en.json"))
        kinds = {keys.KEY_GONE, keys.NO_USER, keys.USER_OFF, keys.NOT_A_SHOW_USER, keys.NOT_ENCRYPTED,
                 keys.HOME_ONLY}
        for lang in (en, self.load("translations/de.json")):
            self.assertEqual(set(lang["issues"]), kinds)
            for kind, text in lang["issues"].items():
                self.assertEqual(kind in keys.FIXABLE, "fix_flow" in text, kind)
                ph = set(re.findall(r"{(\w+)}", text["title"] + text["description"]))
                self.assertLessEqual(ph, {"show", "user"}, kind)
            for kind in keys.FIXABLE:
                aborts = lang["issues"][kind]["fix_flow"]["abort"]
                for reason in ("no_show", "still_away", "no_answer", "not_stored", keys.NOT_ENCRYPTED,
                               keys.USER_OFF, keys.NOT_A_SHOW_USER, "no_provider", "name_taken"):
                    self.assertIn(reason, aborts, f"{kind}: {reason}")
            self.assertEqual(set(lang["services"]["show_key"]["fields"]), {"node", "url"})


if __name__ == "__main__":
    unittest.main()
