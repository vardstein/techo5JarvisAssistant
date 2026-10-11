import argparse
import contextlib
import hashlib
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "tests"))

import techo5lib  # noqa: E402
from jarvis_crown import releases  # noqa: E402
from jarvis_crown.install import InstallError, make_install_plan  # noqa: E402

from test_jarvis_show_multiboard import digest, make_backup, make_shared_rootfs  # noqa: E402

SPEC = importlib.util.spec_from_file_location("jarvis_show_cli_release_files", TOOLS / "jarvis-show.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

INSTALL_SPEC = importlib.util.spec_from_file_location("jarvis_install_show_channel", TOOLS / "install-show.py")
install_show = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(install_show)

BOARDS = ("crown", "checkers")


class FakeRelease:
    """techo5lib.Release as far as fetch_rootfs uses it; writes a root filesystem into the work folder."""
    calls = []

    def __init__(self, repo, tag, workdir, expected_product=None, expected_board=None):
        FakeRelease.calls.append((repo, tag, expected_product, expected_board))
        self.dir = pathlib.Path(workdir)
        self.version = "v1.0.1-dev.41"

    def rootfs(self, arch):
        path = self.dir / f"jarvis-show-rootfs-{self.version}.tar.gz"
        path.write_bytes(b"rootfs for " + arch.encode())
        return str(path)


class MissingRelease:
    def __init__(self, *args, **kwargs):
        techo5lib.fail("could not read the release manifest: HTTP Error 404: Not Found")


class FetchRootfsTests(unittest.TestCase):
    def test_each_channel_and_board_asks_the_signed_release_for_that_board(self):
        tags = {"stable": "latest", "staging": "channel-staging", "dev": "channel-dev"}
        for board in BOARDS:
            for channel, tag in tags.items():
                with self.subTest(board=board, channel=channel), tempfile.TemporaryDirectory() as td:
                    FakeRelease.calls = []
                    got = releases.fetch_rootfs(channel, board, pathlib.Path(td), release=FakeRelease)
                    self.assertEqual(FakeRelease.calls, [(techo5lib.JARVIS_SHOW_REPO, tag, "genbu-show-v1", board)])
                    self.assertEqual(got.sha256, hashlib.sha256(b"rootfs for arm").hexdigest())
                    self.assertEqual((got.version, got.channel), ("v1.0.1-dev.41", channel))

    def test_a_channel_without_a_release_says_which_others_to_try(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(releases.ReleaseError,
                                        r"no usable stable release for checkers: .*404.*--channel staging or --channel dev"):
                releases.fetch_rootfs("stable", "checkers", pathlib.Path(td), release=MissingRelease)

    def test_unknown_channel_is_refused(self):
        with self.assertRaisesRegex(releases.ReleaseError, "no release channel 'nightly'"):
            releases.channel_tag("nightly")


class FakeFetcher:
    def __init__(self, cache):
        self.cache = cache
        self.boards = []

    def wait_for_browser(self, *args, **kwargs):
        return False

    def fetch_board(self, board, cache, browser=None):
        self.boards.append(board)
        amonet = self.cache / board / f"amonet-{board}"
        amonet.mkdir(parents=True, exist_ok=True)
        return {"lineage": self.cache / board / "lineage.zip", "boot": self.cache / board / "boot.img",
                "amonet": self.cache / board / "amonet.zip", "amonet_dir": amonet}


def args(**kw):
    base = dict(lineage_zip=None, boot_image=None, amonet_dir=None, rootfs=None, channel="stable")
    base.update(kw)
    return argparse.Namespace(**base)


class InstallFilesTests(unittest.TestCase):
    def rootfs_from(self, channel, board, work):
        self.asked.append((channel, board))
        return releases.ReleaseRootfs(path=work / "r.tar.gz", sha256="a" * 64, version="v1.0.1-dev.41",
                                      channel=channel)

    def setUp(self):
        self.asked = []

    def test_nothing_passed_fetches_the_detected_boards_files_and_the_channels_rootfs(self):
        for board in BOARDS:
            with self.subTest(board=board), tempfile.TemporaryDirectory() as td:
                td = pathlib.Path(td)
                fetcher = FakeFetcher(td)
                with contextlib.redirect_stdout(io.StringIO()):
                    files = cli._install_files(args(channel="dev"), board, td / "missing-amonet", td,
                                               fetcher=lambda: fetcher, rootfs_from=self.rootfs_from)
                self.assertEqual(fetcher.boards, [board])
                self.assertEqual(files["lineage"], td / board / "lineage.zip")
                self.assertEqual(files["boot"], td / board / "boot.img")
                self.assertEqual(files["amonet_dir"], (td / board / f"amonet-{board}").resolve())
                self.assertEqual(files["rootfs_sha256"], "a" * 64)
                self.assertEqual(self.asked[-1], ("dev", board))

    def test_everything_passed_fetches_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            amonet = td / "amonet"; amonet.mkdir()
            given = args(lineage_zip=td / "l.zip", boot_image=td / "b.img", rootfs=td / "r.tar.gz")

            def no_fetcher():
                raise AssertionError("nothing should be fetched")

            files = cli._install_files(given, "crown", amonet, td, fetcher=no_fetcher, rootfs_from=self.rootfs_from)
            self.assertEqual((files["lineage"], files["boot"], files["amonet_dir"]), (td / "l.zip", td / "b.img", amonet))
            self.assertNotIn("rootfs", files)
            self.assertEqual(self.asked, [])

    def test_a_missing_amonet_package_stops_before_any_question(self):
        class NoAmonet(FakeFetcher):
            def fetch_board(self, board, cache, browser=None):
                return {"lineage": cache / "l.zip", "boot": cache / "b.img"}

        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            with self.assertRaisesRegex(cli.InstallFilesError, "no Amonet package for checkers"):
                cli._install_files(args(), "checkers", td / "none", td, fetcher=lambda: NoAmonet(td),
                                   rootfs_from=self.rootfs_from)


class UpdateChannelTests(unittest.TestCase):
    def plan(self, td, board, **kw):
        rootfs = make_shared_rootfs(td)
        boot = td / f"{board}-boot.img"; boot.write_bytes(b"boot " + board.encode())
        backup = make_backup(td, product=board.upper(), serial=f"{board}-ADB")
        return make_install_plan(
            repo_root=ROOT, adb_serial=f"{board}-ADB", name="Kitchen", backup_root=backup,
            work_dir=td / "work", boot_image=boot, boot_sha256=digest(boot), rootfs=rootfs,
            rootfs_sha256=digest(rootfs), board=board, confirmation="ERASE", **kw)

    def test_the_channel_reaches_the_slot_store_installer_for_each_board(self):
        for board in BOARDS:
            with self.subTest(board=board), tempfile.TemporaryDirectory() as td:
                argv = list(self.plan(pathlib.Path(td), board, update_channel="dev").argv)
                self.assertEqual(argv[argv.index("--update-channel") + 1], "dev")

    def test_unknown_channel_is_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(InstallError, "unknown update channel"):
                self.plan(pathlib.Path(td), "crown", update_channel="nightly")

    def test_first_state_follows_a_non_stable_channel_only(self):
        self.assertEqual(install_show.initial_state(False, {"update_channel": "dev"}), {"update": {"channel": "dev"}})
        self.assertEqual(install_show.initial_state(False, {"update_channel": "stable"}), {})
        self.assertIn("updates from the dev channel", install_show.describe_settings(False, {"update_channel": "dev"}))


if __name__ == "__main__":
    unittest.main()
