import argparse
import hashlib
import importlib.util
import io
import pathlib
import re
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import amonet_upgrade as up  # noqa: E402
from jarvis_crown import catalogue  # noqa: E402
from jarvis_crown import recovery  # noqa: E402
from jarvis_crown.assets import Asset  # noqa: E402

BINS = {
    "preloader.img": b"PRELOADER" * 10,
    "lk.bin": b"LK2" * 20,
    "tz.img": b"TZ" * 30,
    "tee-payload.bin": b"TEEPAYLOAD" * 5,
    "checkers-kaeru.bin": b"KAERU" * 9,
    "twrp.img": b"TWRP" * 40,
}
NAMES = {1: "kb", 2: "dkb", 3: "lk", 4: "tee1", 5: "logo", 6: "tee2", 7: "expdb", 8: "MISC", 9: "boot",
         10: "recovery", 11: "swdl", 14: "persist", 15: "metadata"}
LINKS = {"lk_real": 3, "tee1_real": 4, "tee2_real": 6, "expdb": 7, "recovery": 10, "swdl": 11, "MISC": 8}
BOOT = b"ANDROID!" + b"lineage-boot" * 50
BLOCK = 8192


def filled(seed):
    return (seed * (BLOCK // len(seed) + 1))[:BLOCK]


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def amonet_files(device="checkers", drop=None):
    files = {up.UPDATE_BINARY: b"#!/sbin/sh\n", "amonet/device.prop": f"DEVICE={device}\n".encode()}
    files.update({f"amonet/bin/{k}": v for k, v in BINS.items() if k != drop})
    return files


class FakeTwrp:
    """A checkers in TWRP: partitions in memory, and a twrp install that does what the update-binary does."""

    def __init__(self, *, layout=b"microloader by xyz", links=None, install="ok", device="checkers"):
        self.serial = "ADB-CHECKERS"
        self.device = device
        self.blocks = {f"/dev/block/mmcblk0p{n}": filled(f"old-{n}".encode()) for n in NAMES}
        self.blocks["/dev/block/mmcblk0p9"] = (layout + b"\0" * BLOCK)[:BLOCK]
        self.blocks["/dev/block/mmcblk0boot0"] = filled(b"oldpre")
        self.blocks["/dev/block/mmcblk0boot1"] = filled(b"oldb1")
        self.links = dict(LINKS if links is None else links)
        self.files = {}
        self.install = install
        self.boot = 1
        self.up = True
        self.writes = []

    # recovery client
    def shell(self, command):
        if command == "getprop ro.product.device":
            return self.device
        if command == "id":
            return "uid=0(root)"
        if command == recovery.BOOT_LAYOUT_PROBE:
            return "AMONET1" if b"microloader" in self.blocks["/dev/block/mmcblk0p9"][:1024] else "PLAIN"
        if command.startswith("sh -c "):
            names = re.search(r"for n in ([^;]+);", command).group(1).split()
            for n in names:
                if n in self.links:
                    return f"/dev/block/mmcblk0p{self.links[n]}"
            return "MISSING"
        if command.startswith("test -b /dev/block/mmcblk0boot0"):
            return "OK"
        m = re.fullmatch(r"grep PARTNAME /sys/class/block/mmcblk0p(\d+)/uevent \| cut -d= -f2", command)
        if m:
            return NAMES[int(m.group(1))]
        m = re.fullmatch(r"cat /sys/class/block/(\S+)/size", command)
        if m:
            return str(len(self.blocks["/dev/block/" + m.group(1)]) // 512)
        m = re.fullmatch(r"sha256sum (\S+)", command)
        if m:
            data = self.blocks.get(m.group(1), self.files.get(m.group(1)))
            return hashlib.sha256(data).hexdigest() + "  " + m.group(1)
        m = re.fullmatch(r"head -c (\d+) (\S+) \| sha256sum", command)
        if m:
            return hashlib.sha256(self.blocks[m.group(2)][: int(m.group(1))]).hexdigest() + "  -"
        m = re.fullmatch(r"dd if=(\S+) of=(\S+) bs=4096 && sync && rm -f \S+", command)
        if m:
            self.write(m.group(2), self.files.pop(m.group(1)))
            return ""
        if command == "cat /proc/sys/kernel/random/boot_id":
            return f"boot-{self.boot}"
        raise AssertionError(command)

    def dump_block(self, block, destination):
        destination.write_bytes(self.blocks[block])

    # upgrade client
    def push(self, local, remote):
        self.files[remote] = pathlib.Path(local).read_bytes()

    def write(self, block, data):
        self.writes.append(block)
        old = self.blocks[block]
        self.blocks[block] = data + old[len(data):]

    def install_zip(self, remote):
        z = zipfile.ZipFile(io.BytesIO(self.files[remote]))
        if self.install == "abort":
            return "Could not find the lk_real partition"
        b = {k: z.read(f"amonet/bin/{k}") for k in BINS}
        if self.install == "bad-lk":
            b["lk.bin"] = b"X" * len(b["lk.bin"])
        self.write("/dev/block/mmcblk0boot0", b["preloader.img"])
        for name, part in (("lk.bin", 3), ("tz.img", 6), ("tee-payload.bin", 4), ("checkers-kaeru.bin", 7),
                           ("twrp.img", 10), ("twrp.img", 11)):
            self.write(f"/dev/block/mmcblk0p{part}", b[name])
        self.write("/dev/block/mmcblk0p8", b"\0" * len(self.blocks["/dev/block/mmcblk0p8"]))
        self.boot += 1
        return "- Done, device will reboot to recovery in 5s"

    def boot_id(self):
        return f"boot-{self.boot}"

    def in_recovery(self):
        return self.up


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)
        self.amonet = make_zip(self.dir / "amonet.zip", amonet_files())
        self.lineage = make_zip(self.dir / "lineage.zip", {"boot.img": BOOT, "system/x": b"x"})
        self.pin_assets()

    def tearDown(self):
        self.td.cleanup()

    def pin_assets(self, amonet_sha=None):
        def asset(kind, path, sha):
            return Asset(kind, path.name, "https://example/", sha, path.stat().st_size, "test", manual=kind == "amonet")
        table = (
            asset("lineage", self.lineage, recovery._sha256(self.lineage)),
            asset("amonet", self.amonet, recovery._sha256(self.amonet) if amonet_sha is None else amonet_sha),
        )
        patcher = mock.patch.object(up, "assets_for", lambda board: table)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_upgrade(self, device, phrase="UPGRADE AMONET CHECKERS", **kw):
        asked = []

        def confirm(p):
            asked.append(p)
            return phrase

        kw.setdefault("wait", lambda client, before, output: None)
        result = up.upgrade_amonet(device, "checkers", self.amonet, self.lineage, self.dir / "backups",
                                   confirm=confirm, **kw)
        return result, asked

    def test_upgrades_verifies_and_leaves_a_plain_boot(self):
        device = FakeTwrp()
        result, asked = self.run_upgrade(device)
        self.assertTrue(result.upgraded)
        self.assertEqual(asked, ["UPGRADE AMONET CHECKERS"])
        self.assertTrue(device.blocks["/dev/block/mmcblk0p3"].startswith(BINS["lk.bin"]))
        self.assertTrue(device.blocks["/dev/block/mmcblk0p9"].startswith(BOOT))
        self.assertEqual(device.shell(recovery.BOOT_LAYOUT_PROBE), "PLAIN")
        self.assertEqual(result.backup, self.dir / "backups" / "before-amonet2" / "partitions")
        saved = (result.backup / "p9-boot.img").read_bytes()
        self.assertIn(b"microloader", saved)
        recovery.verify_backup(result.backup, expected_product="CHECKERS")

    def test_wrong_phrase_writes_nothing_but_keeps_the_backup(self):
        device = FakeTwrp()
        with self.assertRaisesRegex(up.UpgradeError, "nothing was written"):
            self.run_upgrade(device, phrase="yes")
        self.assertEqual(device.writes, [])
        self.assertEqual(device.files, {})
        self.assertTrue((self.dir / "backups" / "before-amonet2" / "partitions").is_dir())

    def test_unpinned_zip_is_refused_before_touching_the_show(self):
        self.pin_assets(amonet_sha="")
        device = mock.Mock()
        with self.assertRaisesRegex(up.UpgradeError, "not pinned yet"):
            self.run_upgrade(device)
        device.shell.assert_not_called()

    def test_zip_for_another_board_or_incomplete_is_refused(self):
        for files in (amonet_files(device="crown"), amonet_files(drop="tz.img")):
            make_zip(self.amonet, files)
            self.pin_assets()
            with self.assertRaises(up.UpgradeError):
                self.run_upgrade(mock.Mock())

    def test_plain_boot_needs_no_upgrade(self):
        device = FakeTwrp(layout=b"ANDROID!")
        result, asked = self.run_upgrade(device)
        self.assertFalse(result.upgraded)
        self.assertEqual(asked, [])
        self.assertEqual(device.writes, [])
        self.assertFalse((self.dir / "backups").exists())

    def test_twrp_is_checked_against_the_catalogue_name(self):
        # GENBU-178: the catalogue's identity.twrp_device, which need not be the board id.
        real = catalogue.Catalogue()

        class Renamed:
            def resolve(self, board):
                out = real.resolve(board)
                if board == "checkers":
                    out["identity"]["twrp_device"] = "checkers_twrp"
                return out

            def __getattr__(self, name):
                return getattr(real, name)

        with mock.patch.object(catalogue, "default", Renamed):
            result, _ = self.run_upgrade(FakeTwrp(layout=b"ANDROID!", device="checkers_twrp"))
            self.assertFalse(result.upgraded)
            device = FakeTwrp(device="checkers")
            with self.assertRaisesRegex(up.UpgradeError, "ro.product.device=checkers, not checkers_twrp"):
                self.run_upgrade(device)
            self.assertEqual(device.writes, [])

    def with_map(self, change):
        """The real catalogue, with checkers's amonet_upgrade block changed by change (GENBU-184)."""
        real = catalogue.Catalogue()

        class Changed:
            def resolve(self, board):
                out = real.resolve(board)
                if board == "checkers":
                    change(out)
                return out

            def __getattr__(self, name):
                return getattr(real, name)

        return mock.patch.object(catalogue, "default", Changed)

    def test_the_map_is_read_from_the_catalogue(self):
        # GENBU-184: the partition map is checkers.json's amonet_upgrade block, read on every call.
        writes = catalogue.Catalogue().resolve("checkers")["amonet_upgrade"]["writes"]
        self.assertEqual(up.amonet_upgrade_writes("checkers"),
                         tuple((tuple(w["names"]), w["partition"], w["binary"]) for w in writes))
        device = FakeTwrp()

        def expdb_on_logo(board):
            board["amonet_upgrade"]["writes"][3]["partition"] = "mmcblk0p5"

        with self.with_map(expdb_on_logo):
            with self.assertRaisesRegex(up.UpgradeError,
                                        "expdb resolves to /dev/block/mmcblk0p7, expected /dev/block/mmcblk0p5"):
                self.run_upgrade(device)
        self.assertEqual(device.writes, [])
        self.assertFalse((self.dir / "backups").exists())

    def test_a_board_without_a_map_is_refused_before_the_show(self):
        device = mock.Mock(side_effect=AssertionError("the Show must not be touched"))
        with self.with_map(lambda board: board.pop("amonet_upgrade")):
            with self.assertRaisesRegex(up.UpgradeError, "only been mapped for no board"):
                self.run_upgrade(device)
        self.assertEqual(device.mock_calls, [])

    def test_a_write_the_backup_misses_is_refused_before_the_show(self):
        def onto_userdata(board):
            board["amonet_upgrade"]["writes"][4]["partition"] = "mmcblk0p16"

        device = mock.Mock(side_effect=AssertionError("the Show must not be touched"))
        with self.with_map(onto_userdata):
            with self.assertRaisesRegex(up.UpgradeError, "writes mmcblk0p16, which the backup doesn't cover"):
                self.run_upgrade(device)
        self.assertEqual(device.mock_calls, [])

    def test_a_target_on_an_unexpected_partition_stops_everything(self):
        device = FakeTwrp(links={**LINKS, "lk_real": 5})
        with self.assertRaisesRegex(up.UpgradeError, "lk_real resolves to /dev/block/mmcblk0p5"):
            self.run_upgrade(device)
        self.assertEqual(device.writes, [])
        self.assertFalse((self.dir / "backups").exists())

    def test_an_image_bigger_than_its_partition_stops_everything(self):
        device = FakeTwrp()
        device.blocks["/dev/block/mmcblk0p11"] = b"s" * 128
        with self.assertRaisesRegex(up.UpgradeError, r"twrp.img \(160 bytes\) does not fit mmcblk0p11"):
            self.run_upgrade(device)
        self.assertEqual(device.writes, [])
        self.assertFalse((self.dir / "backups").exists())

    def test_bytes_that_did_not_land_are_reported_with_the_backup(self):
        device = FakeTwrp(install="bad-lk")
        with self.assertRaisesRegex(up.UpgradeError, r"mmcblk0p3 does not hold.*backed up in"):
            self.run_upgrade(device)
        self.assertNotIn("/dev/block/mmcblk0p9", device.writes)

    def test_update_binary_that_stops_early_is_told_apart_from_a_lost_show(self):
        device = FakeTwrp(install="abort")
        clock = iter(range(0, 1000, 3))
        wait = lambda client, before, output: up.wait_for_reboot(  # noqa: E731
            client, before, output, sleep=lambda s: None, clock=lambda: next(clock))
        with self.assertRaisesRegex(up.UpgradeError, "without rebooting.*lk_real.*backed up in"):
            self.run_upgrade(device, wait=wait)

        device = FakeTwrp()
        device.up = False
        clock = iter(range(0, 1000, 3))
        with self.assertRaisesRegex(up.UpgradeError, "did not come back"):
            up.wait_for_reboot(device, "boot-1", "", sleep=lambda s: None, clock=lambda: next(clock))

    def test_the_install_gate_points_at_this_command(self):
        run = mock.Mock()
        with mock.patch.object(recovery, "_adb_shell", side_effect=lambda s, c, run: {
            "getprop ro.product.device": "checkers", "id": "uid=0",
            "test -b /dev/block/mmcblk0p9 && echo OK": "OK", recovery.BOOT_LAYOUT_PROBE: "AMONET1"}[c]):
            with self.assertRaisesRegex(recovery.RecoveryError, "amonet-upgrade"):
                recovery.verify_twrp_board("ADB", "checkers", run=run)


def load_cli():
    spec = importlib.util.spec_from_file_location("jarvis_show_cli", ROOT / "tools" / "jarvis-show.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.args = argparse.Namespace(board="checkers", amonet_zip=pathlib.Path("/z/amonet.zip"),
                                       lineage_zip=pathlib.Path("/z/lineage.zip"))

    def run_cli(self, serials, upgrade_amonet=None):
        with mock.patch.object(self.cli, "adb_recovery_serials", return_value=serials), \
                mock.patch.object(self.cli, "upgrade_amonet", upgrade_amonet or mock.Mock()), \
                mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            return self.cli.amonet_upgrade(self.args, pathlib.Path("/backups"))

    def test_needs_a_board_and_exactly_one_show_in_twrp(self):
        self.args.board = None
        self.assertEqual(self.run_cli(["A"]), 1)
        self.args.board = "checkers"
        self.assertEqual(self.run_cli([]), 2)
        self.assertEqual(self.run_cli(["A", "B"]), 2)

    def test_backs_up_under_the_serial_and_reports_a_stop(self):
        upgrade = mock.Mock(return_value=up.UpgradeResult(True, pathlib.Path("/b")))
        self.assertEqual(self.run_cli(["SER"], upgrade_amonet=upgrade), 0)
        self.assertEqual(upgrade.call_args.args[1:5], ("checkers", pathlib.Path("/z/amonet.zip"),
                                                      pathlib.Path("/z/lineage.zip"), pathlib.Path("/backups/SER")))
        stop = mock.Mock(side_effect=up.UpgradeError("phrase did not match; nothing was written"))
        self.assertEqual(self.run_cli(["SER"], upgrade_amonet=stop), 4)

    def test_closed_stdin_answers_no_instead_of_crashing(self):
        answers = []
        upgrade = mock.Mock(side_effect=lambda *a, confirm, **k: answers.append(confirm("PHRASE")) or
                            up.UpgradeResult(True, pathlib.Path("/b")))
        with mock.patch("builtins.input", side_effect=EOFError):
            self.run_cli(["SER"], upgrade_amonet=upgrade)
        self.assertEqual(answers, [""])


if __name__ == "__main__":
    unittest.main()
