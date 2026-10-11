import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import signal
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import home_assistant as ha  # noqa: E402
from jarvis_crown import music_assistant as ma  # noqa: E402
from jarvis_crown import ui  # noqa: E402
from jarvis_crown.settings import Asker, SettingsError, gather  # noqa: E402
from jarvis_crown.sign_ins import SignIns  # noqa: E402


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAuth:
    """Home Assistant's /auth endpoints and one API path, as an opener for HomeAssistant."""

    def __init__(self, *, mfa=False, code="123456", admin=True):
        self.mfa, self.code, self.admin = mfa, code, admin
        self.password = "right"
        self.access = 0
        self.revoked = []
        self.seen = []
        self.reject_next = False

    def tokens(self):
        self.access += 1
        return {"access_token": f"ACCESS-{self.access}", "refresh_token": "REFRESH", "expires_in": 1800}

    def __call__(self, req, timeout):
        path = urllib.parse.urlsplit(req.full_url).path
        raw = req.data or b""
        body = dict(urllib.parse.parse_qsl(raw.decode())) if req.get_header("Content-type", "").startswith(
            "application/x-www-form") else (json.loads(raw) if raw else None)
        self.seen.append((path, req.get_header("Authorization")))
        if path == "/auth/login_flow":
            answer = {"type": "form", "flow_id": "L1", "step_id": "init"}
        elif path == "/auth/login_flow/L1" and "username" in body:
            if body["password"] != self.password:
                answer = {"type": "form", "flow_id": "L1", "step_id": "init", "errors": {"base": "invalid_auth"}}
            elif self.mfa:
                answer = {"type": "form", "flow_id": "L1", "step_id": "select_mfa_module", "data_schema": [
                    {"name": "multi_factor_auth_module", "options": [["totp", "Authenticator app"]]}]}
            else:
                answer = {"type": "create_entry", "result": "CODE"}
        elif path == "/auth/login_flow/L1" and "multi_factor_auth_module" in body:
            answer = {"type": "form", "flow_id": "L1", "step_id": "mfa"}
        elif path == "/auth/login_flow/L1" and "code" in body:
            answer = {"type": "create_entry", "result": "CODE"} if body["code"] == self.code else \
                {"type": "form", "flow_id": "L1", "step_id": "mfa", "errors": {"base": "invalid_code"}}
        elif path == "/auth/token" and body["grant_type"] == "authorization_code":
            assert body["code"] == "CODE"
            answer = self.tokens()
        elif path == "/auth/token" and body["grant_type"] == "refresh_token":
            if body["refresh_token"] in self.revoked:
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b""))
            answer = self.tokens()
        elif path == "/auth/revoke":
            self.revoked.append(body["token"])
            answer = None
        elif path == "/api/":
            if self.reject_next:
                self.reject_next = False
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))
            answer = {"message": "API running."}
        else:
            raise AssertionError(path)
        return Resp(b"" if answer is None else json.dumps(answer).encode())


class SignedInTests(unittest.TestCase):
    def sign_in(self, fake, code=None, now=None):
        now = now if now is not None else [0.0]
        with mock.patch.object(ha.SignedIn, "ws", lambda self, m: {"is_admin": fake.admin}):
            return ha.sign_in("http://ha:8123", "jade", "right", code=code, opener=fake, clock=lambda: now[0])

    def test_signs_in_renews_and_signs_out(self):
        fake, now = FakeAuth(), [0.0]
        signed = self.sign_in(fake, now=now)
        self.assertTrue(signed.signed_in)
        signed.request("GET", "/api/")
        self.assertEqual(fake.seen[-1], ("/api/", "Bearer ACCESS-1"))
        now[0] = 1800 - 59
        signed.request("GET", "/api/")
        self.assertEqual(fake.seen[-1], ("/api/", "Bearer ACCESS-2"), "renewed a minute before it runs out")
        fake.reject_next = True
        signed.request("GET", "/api/")
        self.assertEqual(fake.seen[-1], ("/api/", "Bearer ACCESS-3"), "a 401 renews once and tries again")
        signed.sign_out()
        self.assertEqual(fake.revoked, ["REFRESH"])
        self.assertFalse(signed.signed_in)
        with self.assertRaisesRegex(ha.HomeAssistantError, "signed out"):
            signed.request("GET", "/api/")
        signed.sign_out()
        self.assertEqual(fake.revoked, ["REFRESH"], "signing out twice is harmless")

    def test_wrong_password_says_so(self):
        fake = FakeAuth()
        fake.password = "other"
        with self.assertRaisesRegex(ha.NotSignedIn, "wrong username or password"):
            self.sign_in(fake)
        self.assertNotIn("/auth/token", [p for p, _ in fake.seen])

    def test_two_factor_code_asked_again_when_wrong(self):
        fake, asked = FakeAuth(mfa=True), []
        answers = ["000000", "123456"]

        def code(again):
            asked.append(again)
            return answers.pop(0)
        self.sign_in(fake, code=code)
        self.assertEqual(asked, [False, True])
        with self.assertRaisesRegex(ha.NotSignedIn, "no two-factor code"):
            self.sign_in(FakeAuth(mfa=True), code=lambda again: None)

    def test_not_an_admin_is_signed_out_again(self):
        fake = FakeAuth(admin=False)
        with self.assertRaisesRegex(ha.NotSignedIn, "not an admin"):
            self.sign_in(fake)
        self.assertEqual(fake.revoked, ["REFRESH"])

    def test_sign_in_id_and_ending_an_older_one(self):
        fake = FakeAuth()
        signed = self.sign_in(fake)
        sent = []

        def ws(message):
            sent.append(message)
            if message["type"] == "auth/refresh_tokens":
                return [{"id": "R-old", "is_current": False}, {"id": "R-now", "is_current": True}]
            if message["refresh_token_id"] == "R-gone":
                raise ha.HomeAssistantError("auth/delete_refresh_token: invalid_token_id")
            return {}
        signed.ws = ws
        self.assertEqual(signed.sign_in_id(), "R-now")
        self.assertTrue(signed.end_other("R-old"))
        self.assertFalse(signed.end_other("R-gone"))


class SignInsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.path = pathlib.Path(self.td.name) / "jarvis-show" / "sign-ins.json"

    def test_recorded_while_open_and_gone_when_ended(self):
        run, log, ended = SignIns(lambda: self.path), [], []
        run.opened("Home Assistant", "http://ha:8123", "jade", "R1", end=lambda: ended.append("ha"))
        run.opened("Music Assistant", "10.0.0.6", "jade", "T1", end=lambda: ended.append("ma"))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual([r["id"] for r in json.loads(self.path.read_text())], ["R1", "T1"])
        self.assertTrue(run.active)
        run.end_all(log.append)
        self.assertEqual(ended, ["ma", "ha"], "the last one first")
        self.assertEqual(log, ["signed out of Music Assistant as 'jade'", "signed out of Home Assistant as 'jade'"])
        self.assertFalse(self.path.exists())
        self.assertFalse(run.active)

    def test_a_failed_sign_out_is_kept_and_the_next_run_ends_it(self):
        run, log = SignIns(lambda: self.path), []

        def fails():
            raise ha.HomeAssistantError("cannot reach Home Assistant")
        run.opened("Home Assistant", "http://ha:8123", "jade", "R1", end=fails)
        run.end_all(log.append)
        self.assertTrue(log[0].startswith("WARN: could not sign out of Home Assistant as 'jade'"))
        later, other = SignIns(lambda: self.path), []
        self.assertEqual(later.opened("Home Assistant", "http://ha:8123", "sam", "R5", end=lambda: None,
                                      end_other=other.append), 0, "another user's sign-in is not theirs to end")
        self.assertEqual(later.opened("Home Assistant", "http://ha:8123", "jade", "R2", end=lambda: None,
                                      end_other=lambda i: other.append(i) or True), 1)
        self.assertEqual(other, ["R1"])
        self.assertEqual([r["id"] for r in json.loads(self.path.read_text())], ["R5", "R2"])


def args(**kw):
    base = dict(wifi=None, wifi_passphrase_file=None, ha_url=None, ha_token_file=None, ha_admin_token_file=None,
                dashcast=None, dashcast_key_file=None, music_assistant=None, root_password_file=None)
    base.update(kw)
    return argparse.Namespace(**base)


class FakeSigned:
    def __init__(self):
        self.out = False

    def sign_in_id(self):
        return "R-now"

    def end_other(self, sign_in_id):
        return True

    def sign_out(self):
        self.out = True


class GatherSignInTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.run = SignIns(lambda: pathlib.Path(td.name) / "sign-ins.json")

    def gather(self, answers, secrets, **kw):
        answers, secrets, said = list(answers), list(secrets), []
        asker = Asker(True, ask=lambda _: answers.pop(0), ask_secret=lambda _: secrets.pop(0), say=said.append,
                      out=ui.Out(io.StringIO(), color=False, fancy=True, width=78))
        kw.setdefault("ma_logout", lambda *a: None)
        s = gather(kw.pop("a", args()), asker, defaults=kw.pop("defaults", {}), lookup=lambda *a, **k: None,
                   want_wifi=False, run=self.run, **kw)
        self.assertEqual((answers, secrets), ([], []), "every answer was asked for")
        return s, said

    def test_wrong_password_then_right_with_a_two_factor_code(self):
        signed, tries = FakeSigned(), []

        def login(url, user, password, code=None):
            tries.append(password)
            if password != "pw-0k":
                raise ha.NotSignedIn("wrong username or password")
            self.assertEqual(code(False), "123456")
            return signed
        s, said = self.gather(["http://ha:8123", "jade", "123 456", "-", "-"], ["wrong", "pw-0k"],
                              ha_login=login)
        self.assertEqual(tries, ["wrong", "pw-0k"])
        self.assertEqual((s.ha_user, s.own_ha_user, s.ha_token), ("jade", True, None))
        self.assertIs(s.ha_sign_in, signed)
        self.assertTrue(any("could not sign in: wrong username or password" in line for line in said))
        self.assertFalse(any("pw-0k" in line for line in said), "no password is ever said")
        self.run.end_all(lambda _: None)
        self.assertTrue(signed.out)

    def test_three_wrong_passwords_then_the_hand_made_token(self):
        def login(url, user, password, code=None):
            raise ha.NotSignedIn("wrong username or password")
        s, said = self.gather(["http://ha:8123", "jade", "-", "-"], ["a", "b", "c", "a.b.c-token"], ha_login=login)
        self.assertEqual((s.ha_token, s.own_ha_user, s.ha_sign_in), ("a.b.c-token", False, None))
        self.assertTrue(any("3 tries" in line for line in said))
        self.assertFalse(self.run.active)

    def test_enter_skips_the_admin(self):
        s, _ = self.gather(["http://ha:8123", "", "-", "-"], [""],
                           ha_login=lambda *a, **k: self.fail("nobody signs in"))
        self.assertFalse(s.ha_admin)
        self.assertIn("added to Home Assistant: by hand (no admin sign-in)", s.summary())

    def test_home_assistant_unreachable_stops(self):
        def login(*a, **k):
            raise ha.HomeAssistantError("cannot reach Home Assistant at http://ha:8123: refused")
        with self.assertRaisesRegex(SettingsError, "cannot reach"):
            self.gather(["http://ha:8123", "jade"], ["right"], ha_login=login)

    def test_music_assistant_sign_in_and_its_sign_out(self):
        out = []
        s, _ = self.gather(["-", "-", "10.0.0.6", "jade"], ["right"],
                           ma_login=lambda host, user, password: ("ma-session", "T1"),
                           ma_logout=lambda host, token, token_id: out.append((host, token, token_id)))
        self.assertEqual((s.music_assistant_token, s.music_assistant_user), ("ma-session", "jade"))
        self.run.end_all(lambda _: None)
        self.assertEqual(out, [("10.0.0.6", "ma-session", "T1")])

    def test_music_assistant_admin_refused_means_no_lyrics(self):
        def login(host, user, password):
            raise ma.NotSignedIn("'kid' is not a Music Assistant admin")
        s, said = self.gather(["-", "-", "10.0.0.6", "kid"], ["x", "y", "z"], ma_login=login)
        self.assertIsNone(s.music_assistant_token)
        self.assertTrue(any("lyrics won't work" in line for line in said))


class MusicAssistantSignInTests(unittest.TestCase):
    def server(self, *answers):
        try:
            from test_jarvis_show_home_assistant import MusicAssistantTests
        except ImportError:
            from tests.test_jarvis_show_home_assistant import MusicAssistantTests
        return MusicAssistantTests.server(None, *answers)

    def test_sign_in_finds_its_own_token(self):
        sock = self.server({"message_id": "1", "result": {"success": True, "access_token": "ma-session",
                                                          "user": {"role": "admin"}}},
                           {"message_id": "2", "result": {"authenticated": True}},
                           {"message_id": "3", "result": [{"token_id": "T0", "name": "phone"},
                                                          {"token_id": "T1", "name": "installer x"}]})
        self.assertEqual(ma.sign_in_exchange(sock, "10.0.0.6:8095", "jade", "right", name="installer x",
                                             key="dGhlIHNhbXBsZSBub25jZQ=="), ("ma-session", "T1"))
        sent = sock.messages()
        self.assertEqual([m["command"] for m in sent], ["auth/login", "auth", "auth/tokens"])
        self.assertEqual(sent[0]["args"], {"username": "jade", "password": "right", "device_name": "installer x"})

    def test_wrong_password_and_a_plain_user(self):
        sock = self.server({"message_id": "1", "result": {"success": False, "error": "Invalid credentials"}})
        with self.assertRaisesRegex(ma.NotSignedIn, "Invalid credentials") as cm:
            ma.sign_in_exchange(sock, "10.0.0.6:8095", "jade", "wrong", name="x", key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertNotIn("wrong", str(cm.exception).replace("'jade'", ""))
        sock = self.server({"message_id": "1", "result": {"success": True, "access_token": "s",
                                                          "user": {"role": "user"}}},
                           {"message_id": "2", "result": {"authenticated": True}},
                           {"message_id": "3", "result": [{"token_id": "T1", "name": "x"}]},
                           {"message_id": "4", "result": {}})
        with self.assertRaisesRegex(ma.NotSignedIn, "not a Music Assistant admin"):
            ma.sign_in_exchange(sock, "10.0.0.6:8095", "kid", "pw", name="x", key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual(sock.messages()[-1], {**sock.messages()[-1], "command": "auth/token/revoke",
                                               "args": {"token_id": "T1"}})

    def test_revoke_tolerates_the_closed_connection(self):
        sock = self.server({"message_id": "1", "result": {"authenticated": True}})
        ma.revoke_exchange(sock, "10.0.0.6:8095", "ma-session", "T1", key="dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual([m["command"] for m in sock.messages()], ["auth", "auth/token/revoke"])


class RunEndsSignedOutTests(unittest.TestCase):
    """However the run ends, main signs out of what it signed in to."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("jarvis_show_cli_sign_ins", ROOT / "tools" / "jarvis-show.py")
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.run = SignIns(lambda: pathlib.Path(td.name) / "sign-ins.json")
        self.ended = []
        self.run.opened("Home Assistant", "http://ha:8123", "jade", "R1", end=lambda: self.ended.append("ha"))
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        self.addCleanup(lambda: [signal.signal(sig, h) for sig, h in handlers.items()])

    def main(self, body):
        with mock.patch.object(self.cli, "RUN", self.run), mock.patch.object(self.cli, "_main", body), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                return self.cli.main()
            finally:
                self.out = out.getvalue()

    def test_an_error_still_signs_out(self):
        def fails():
            raise RuntimeError("flash failed")
        with self.assertRaisesRegex(RuntimeError, "flash failed"):
            self.main(fails)
        self.assertEqual(self.ended, ["ha"])
        self.assertIn("signed out of Home Assistant as 'jade'", self.out)

    def test_a_closed_terminal_ends_the_run_like_ctrl_c(self):
        def hung_up():
            signal.getsignal(signal.SIGHUP)(signal.SIGHUP, None)
        with self.assertRaises(SystemExit) as cm:
            self.main(hung_up)
        self.assertEqual(cm.exception.code, 128 + signal.SIGHUP)
        self.assertEqual(self.ended, ["ha"])

    def test_nothing_said_when_nobody_signed_in(self):
        self.run.end_all(lambda _: None)
        self.ended.clear()
        self.assertEqual(self.main(lambda: 0), 0)
        self.assertNotIn("Signing out", self.out)


if __name__ == "__main__":
    unittest.main()
