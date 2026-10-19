import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.device_gate import DeviceIdentity  # noqa: E402
from jarvis_crown import recovery  # noqa: E402


def cp(argv, stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)


class FakeClient:
    def __init__(self, *, corrupt=None, empty=None):
        self.serial = "ADB-CROWN"
        self.corrupt = corrupt
        self.empty = dict(empty or {})  # block: how many reads come back empty first
        self.data = {}
        self.names = {}
        for number in recovery.SMALL_PARTITIONS:
            sys_name = f"mmcblk0p{number}"
            body = (f"partition-{number}".encode() * 128)[:512]
            self.data[f"/dev/block/{sys_name}"] = body
            self.names[sys_name] = f"part{number}"
        for boot in recovery.BOOT_AREAS:
            sys_name = f"mmcblk0{boot}"
            self.data[f"/dev/block/{sys_name}"] = (boot.encode() * 128)[:512]

    def shell(self, command):
        if command.startswith("grep PARTNAME"):
            sys_name = command.split("/sys/class/block/", 1)[1].split("/", 1)[0]
            return self.names[sys_name]
        if command.startswith("cat /sys/class/block/"):
            sys_name = command.split("/sys/class/block/", 1)[1].split("/", 1)[0]
            block = f"/dev/block/{sys_name}"
            return str(len(self.data[block]) // 512)
        if command.startswith("sha256sum "):
            block = command.split(" ", 1)[1]
            body = self.data[block]
            return hashlib.sha256(body).hexdigest() + "  " + block
        raise AssertionError(command)

    def dump_block(self, block, destination):
        body = self.data[block]
        if self.empty.get(block):
            self.empty[block] -= 1
            body = b""
        if self.corrupt == block:
            body = b"X" * len(body)
        destination.write_bytes(body)


class RecoveryTests(unittest.TestCase):
    def identity(self, unlocked=True):
        return DeviceIdentity("FAST-CROWN", "CROWN", unlocked, "lk")

    def adb_runner(self, *, devices=("ADB-CROWN",), product="crown"):
        def run(argv, **kwargs):
            if argv == ["adb", "devices"]:
                lines = "List of devices attached\n" + "".join(f"{s}\trecovery\n" for s in devices)
                return cp(argv, stdout=lines)
            command = argv[-1]
            if command == "getprop ro.product.device":
                return cp(argv, stdout=product + "\n")
            if command == "id":
                return cp(argv, stdout="uid=0(root) gid=0(root)\n")
            if command.startswith("test -b /dev/block/mmcblk0p9"):
                return cp(argv, stdout="OK\n")
            if command.startswith("tmp=/tmp/jarvis-boot-prefix."):
                return cp(argv, stdout="PLAIN\n")
            raise AssertionError(argv)
        return run

    def test_existing_twrp_avoids_all_fastboot_writes(self):
        fastboot = mock.Mock()
        session = recovery.ensure_twrp(
            self.identity(), pathlib.Path("/unused"),
            fastboot_run=fastboot,
            adb_run=self.adb_runner(),
            identify=mock.Mock(side_effect=AssertionError("identify should not run")),
        )
        self.assertFalse(session.twrp_flashed)
        fastboot.assert_not_called()

    def test_recovery_identity_requires_one_matching_root_twrp(self):
        identity = recovery.identify_recovery_show("crown", run=self.adb_runner(devices=("ADB-CROWN",)))
        self.assertEqual(identity.serial, "ADB-CROWN")
        self.assertEqual(identity.product, "CROWN")
        self.assertTrue(identity.unlocked)

        with self.assertRaisesRegex(recovery.RecoveryError, "no TWRP"):
            recovery.identify_recovery_show("crown", run=self.adb_runner(devices=()))

        with self.assertRaisesRegex(recovery.RecoveryError, "not crown"):
            recovery.identify_recovery_show("crown", run=self.adb_runner(product="checkers"))

    def test_unlocked_fastboot_flashes_only_recovery_and_swdl(self):
        with tempfile.TemporaryDirectory() as td:
            amonet = pathlib.Path(td) / "amonet-crown-v2.0.1"
            image = amonet / "amonet" / "bin" / "twrp.img"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"known-twrp")
            twrp_hash = hashlib.sha256(b"known-twrp").hexdigest()
            old = recovery.TWRP_SHA256
            recovery.TWRP_SHA256 = twrp_hash
            try:
                calls = []
                def fastboot(argv, **kwargs):
                    calls.append(tuple(argv))
                    return cp(argv)
                adb_calls = {"n": 0}
                good = self.adb_runner()
                def adb(argv, **kwargs):
                    if argv == ["adb", "devices"]:
                        adb_calls["n"] += 1
                        if adb_calls["n"] == 1:
                            return cp(argv, stdout="List of devices attached\n")
                    return good(argv, **kwargs)
                session = recovery.ensure_twrp(
                    self.identity(), amonet,
                    fastboot_run=fastboot,
                    adb_run=adb,
                    identify=lambda: self.identity(),
                    sleep=lambda _: None,
                    wait_attempts=2,
                )
            finally:
                recovery.TWRP_SHA256 = old
            self.assertTrue(session.twrp_flashed)
            self.assertEqual([c[4] if c[3] == "flash" else c[3] for c in calls], ["recovery", "swdl", "reboot"])
            joined = " ".join(" ".join(c) for c in calls)
            for forbidden in (" lk ", " expdb ", " preloader ", " tee1 ", " tee2 ", " boot ", " system ", " userdata "):
                self.assertNotIn(forbidden, joined)

    def test_legacy_amonet1_microloader_is_rejected_in_twrp(self):
        base = self.adb_runner()

        def adb(argv, **kwargs):
            if argv and isinstance(argv[-1], str) and argv[-1].startswith("tmp=/tmp/jarvis-boot-prefix."):
                return cp(argv, stdout="AMONET1\n")
            return base(argv, **kwargs)

        with self.assertRaisesRegex(recovery.RecoveryError, "legacy Amonet 1.x boot microloader"):
            recovery.ensure_twrp(
                self.identity(), pathlib.Path("/unused"), adb_run=adb,
            )

    def test_amonet1_hint_only_sends_mapped_boards_to_amonet_upgrade(self):
        from jarvis_crown.amonet_upgrade import TARGETS

        for board in ("crown", "checkers", "cronos"):
            hint = recovery.amonet1_upgrade_hint(board)
            if board in TARGETS:
                self.assertIn(f"amonet-upgrade --board {board}", hint)
            else:
                self.assertNotIn("jarvis-show.py amonet-upgrade", hint)
        cronos = recovery.amonet1_upgrade_hint("cronos")
        self.assertIn("in TWRP", cronos)
        self.assertIn("xdaforums.com", cronos)

    def test_unreadable_boot_layout_is_rejected_in_twrp(self):
        base = self.adb_runner()

        def adb(argv, **kwargs):
            if argv and isinstance(argv[-1], str) and argv[-1].startswith("tmp=/tmp/jarvis-boot-prefix."):
                return cp(argv, stdout="READ-FAIL\n")
            return base(argv, **kwargs)

        with self.assertRaisesRegex(recovery.RecoveryError, "cannot prove a modern/plain boot layout"):
            recovery.ensure_twrp(
                self.identity(), pathlib.Path("/unused"), adb_run=adb,
            )

    def test_locked_identity_never_reaches_twrp_write(self):
        fastboot = mock.Mock()
        with self.assertRaisesRegex(recovery.RecoveryError, "proven unlocked"):
            recovery.ensure_twrp(
                self.identity(unlocked=False), pathlib.Path("/unused"),
                fastboot_run=fastboot, adb_run=self.adb_runner(devices=()),
            )
        fastboot.assert_not_called()

    def test_wrong_twrp_product_fails(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "not crown"):
            recovery.ensure_twrp(
                self.identity(), pathlib.Path("/unused"),
                adb_run=self.adb_runner(product="checkers"),
            )

    def test_atomic_backup_covers_small_partitions_and_boot_areas(self):
        with tempfile.TemporaryDirectory() as td:
            result = recovery.backup_recovery_state(
                FakeClient(), pathlib.Path(td), fastboot_identity=self.identity()
            )
            self.assertFalse(result.reused)
            self.assertEqual(result.files, len(recovery.SMALL_PARTITIONS) + 2)
            verified = recovery.verify_backup(result.path)
            self.assertTrue(verified.reused)
            self.assertFalse((pathlib.Path(td) / "partitions.partial").exists())

    def test_an_empty_read_from_a_twrp_just_up_is_read_again(self):
        block = "/dev/block/mmcblk0p1"
        with tempfile.TemporaryDirectory() as td, mock.patch.object(recovery, "POLL_SECONDS", 0):
            result = recovery.backup_recovery_state(
                FakeClient(empty={block: 2}), pathlib.Path(td), fastboot_identity=self.identity()
            )
            self.assertEqual(result.files, len(recovery.SMALL_PARTITIONS) + 2)

    def test_reads_that_stay_empty_still_stop_the_install(self):
        block = "/dev/block/mmcblk0p1"
        with tempfile.TemporaryDirectory() as td, mock.patch.object(recovery, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(recovery.RecoveryError, "size mismatch.*got 0"):
                recovery.backup_recovery_state(
                    FakeClient(empty={block: recovery.READ_ATTEMPTS}), pathlib.Path(td),
                    fastboot_identity=self.identity()
                )
            self.assertFalse((pathlib.Path(td) / "partitions").exists())

    def test_hash_mismatch_removes_partial_and_never_certifies_backup(self):
        with tempfile.TemporaryDirectory() as td:
            block = "/dev/block/mmcblk0p7"
            with self.assertRaisesRegex(recovery.RecoveryError, "hash mismatch"):
                recovery.backup_recovery_state(
                    FakeClient(corrupt=block), pathlib.Path(td), fastboot_identity=self.identity()
                )
            self.assertFalse((pathlib.Path(td) / "partitions").exists())
            self.assertFalse((pathlib.Path(td) / "partitions.partial").exists())

    def test_valid_existing_backup_is_reused(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first = recovery.backup_recovery_state(FakeClient(), root, fastboot_identity=self.identity())
            second = recovery.backup_recovery_state(
                mock.Mock(side_effect=AssertionError("client must not be used")),
                root,
                fastboot_identity=self.identity(),
            )
            self.assertEqual(first.path, second.path)
            self.assertTrue(second.reused)


if __name__ == "__main__":
    unittest.main()
