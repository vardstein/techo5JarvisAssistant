import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_channels as rc  # noqa: E402


def pair(tag):
    return {"manifest.json": f"manifest of {tag}".encode(), "manifest.json.sig": f"sig of {tag}".encode()}


class FakeGitHub:
    """Releases and channel tags in memory. hooks[call] is a queue, one per call, to play another run."""

    def __init__(self, *tags, channels=None):
        self.files = {t: pair(t) for t in tags}
        self.drafts = set()
        self.listed = set(tags)
        self.files.update(channels or {})
        self.uploads = []
        self.fail_uploads = 0
        self.hooks = {}

    def _hook(self, name):
        queue = self.hooks.get(name)
        if queue:
            queue.pop(0)(self)

    def releases(self):
        self._hook("releases")
        return [{"tag_name": t, "draft": t in self.drafts, "assets": [{"name": n} for n in self.files[t]]}
                for t in self.files if t in self.listed or t.startswith("channel-")]

    def exists(self, tag):
        return tag in self.files

    def create(self, tag, title, notes, commit):
        self.files[tag] = {}

    def download(self, tag, into):
        self._hook("download")
        return dict(self.files.get(tag, {}))

    def upload(self, tag, files):
        self._hook("upload")
        if self.fail_uploads:
            self.fail_uploads -= 1
            raise rc.ChannelError("HTTP 422: ReleaseAsset.name already exists")
        self.uploads.append(tag)
        for f in files:
            self.files[tag][f.name] = f.read_bytes()


def move(gh, channel, **kw):
    return rc.move(gh, channel, nap=lambda s: None, log=lambda m: None, **kw)


class ChannelMoveTests(unittest.TestCase):
    def test_heals_the_pair_the_parallel_pushes_left(self):
        # What the dev, staging and stable pushes of one commit left on channel-dev: the stable
        # release's manifest next to the dev release's signature.
        mixed = {"manifest.json": pair("v1.0.2-stable.48")["manifest.json"],
                 "manifest.json.sig": pair("v1.0.2-dev.46")["manifest.json.sig"]}
        gh = FakeGitHub("v1.0.2-dev.46", "v1.0.2-staging.47", "v1.0.2-stable.48", channels={"channel-dev": mixed})
        self.assertEqual(move(gh, "dev"), "v1.0.2-stable.48")
        self.assertEqual(gh.files["channel-dev"], pair("v1.0.2-stable.48"))

    def test_an_older_run_moves_the_channel_to_the_newest_release_not_its_own(self):
        gh = FakeGitHub("v1.0.2-dev.46", "v1.0.2-stable.48")
        self.assertEqual(move(gh, "dev", own="v1.0.2-dev.46"), "v1.0.2-stable.48")
        self.assertEqual(gh.files["channel-dev"], pair("v1.0.2-stable.48"))

    def test_a_channel_already_right_is_left_alone(self):
        gh = FakeGitHub("v1.0.2-dev.46", channels={"channel-dev": pair("v1.0.2-dev.46")})
        move(gh, "dev")
        self.assertEqual(gh.uploads, [])

    def test_a_stale_write_landing_after_ours_is_put_right(self):
        gh = FakeGitHub("v1.0.2-dev.46", "v1.0.2-stable.48", channels={"channel-dev": pair("v1.0.2-dev.46")})
        # The dev run decided on dev.46 before stable.48 was listed; its signature lands just after ours.
        def late(g):
            g.files["channel-dev"]["manifest.json.sig"] = pair("v1.0.2-dev.46")["manifest.json.sig"]
        gh.hooks["download"] = [lambda g: None, lambda g: None, lambda g: None, late]
        self.assertEqual(move(gh, "dev"), "v1.0.2-stable.48")
        self.assertEqual(gh.files["channel-dev"], pair("v1.0.2-stable.48"))
        self.assertEqual(len(gh.uploads), 2)

    def test_a_refused_upload_is_tried_again(self):
        gh = FakeGitHub("v1.0.2-dev.46", channels={"channel-dev": {}})
        gh.fail_uploads = 2
        move(gh, "dev")
        self.assertEqual(gh.files["channel-dev"], pair("v1.0.2-dev.46"))

    def test_waits_for_its_own_release_to_be_listed(self):
        gh = FakeGitHub("v1.0.2-dev.46", "v1.0.2-dev.49", channels={"channel-dev": pair("v1.0.2-dev.46")})
        gh.listed.discard("v1.0.2-dev.49")
        gh.hooks["releases"] = [lambda g: None, lambda g: g.listed.add("v1.0.2-dev.49")]
        self.assertEqual(move(gh, "dev", own="v1.0.2-dev.49"), "v1.0.2-dev.49")
        self.assertEqual(gh.files["channel-dev"], pair("v1.0.2-dev.49"))

    def test_gives_up_loudly_when_it_never_settles(self):
        gh = FakeGitHub("v1.0.2-dev.46", channels={"channel-dev": {}})
        gh.fail_uploads = 99
        with self.assertRaisesRegex(rc.ChannelError, "did not settle"):
            move(gh, "dev", attempts=3)

    def test_creates_a_missing_channel(self):
        gh = FakeGitHub("v1.0.2-staging.47")
        move(gh, "staging")
        self.assertEqual(gh.files["channel-staging"], pair("v1.0.2-staging.47"))


class TargetTests(unittest.TestCase):
    def releases(self, *tags, draft=(), bare=()):
        out = []
        for t in tags:
            names = [] if t in bare else list(rc.PAIR)
            out.append({"tag_name": t, "draft": t in draft, "assets": [{"name": n} for n in names]})
        return out

    def test_staging_never_carries_dev(self):
        rs = self.releases("v1.0.2-staging.47", "v1.0.2-dev.50")
        self.assertEqual(rc.target(rs, "staging"), "v1.0.2-staging.47")
        self.assertEqual(rc.target(rs, "dev"), "v1.0.2-dev.50")

    def test_skips_drafts_channels_and_releases_without_a_manifest(self):
        rs = self.releases("v1.0.2-dev.46", "v1.0.2-dev.47", "v1.0.2-dev.48", "channel-dev",
                           draft={"v1.0.2-dev.47"}, bare={"v1.0.2-dev.48"})
        self.assertEqual(rc.target(rs, "dev"), "v1.0.2-dev.46")

    def test_ranking(self):
        self.assertTrue(rc.newer("v1.0.2-dev.49", "v1.0.2-stable.48"))
        self.assertTrue(rc.newer("v1.0.2-stable.48", "v1.0.2-staging.47"))
        self.assertTrue(rc.newer("v1.0.2-dev.48.2", "v1.0.2-dev.48"))
        self.assertTrue(rc.newer("v1.0.2", "v1.0.2-stable.99"))
        self.assertTrue(rc.newer("v1.0.3-dev.1", "v1.0.2"))
        self.assertFalse(rc.newer("v1.0.2-dev.46", "v1.0.2-dev.46"))

    def test_channel_follows_the_name(self):
        for tag, want in [("v1.0.2", "stable"), ("v1.0.2-stable.48", "stable"), ("v1.0.2-staging.47", "staging"),
                          ("v1.0.2-rc.1", "staging"), ("v1.0.2-dev.46", "dev"), ("v1.0.2-beta.1", "dev"),
                          ("channel-dev", None)]:
            self.assertEqual(rc.channel_of(tag), want, tag)


if __name__ == "__main__":
    unittest.main()
