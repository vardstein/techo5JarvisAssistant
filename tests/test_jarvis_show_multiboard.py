import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.boards import profile_for_board, profile_for_product  # noqa: E402
from jarvis_crown.device_gate import DeviceGateError, DeviceIdentity, identify_show  # noqa: E402
from jarvis_crown import preflight, recovery, unlock  # noqa: E402
from jarvis_crown.install import InstallError, make_install_plan, verify_boot, verify_jarvis_rootfs  # noqa: E402


def cp(argv, stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_amonet(root: pathlib.Path, board: str):
    profile = profile_for_board(board)
    base = root / profile.amonet_dir_name / "amonet"
    names = list(preflight.AMONET_REQUIRED_COMMON) + [f"bin/{profile.amonet_kaeru_name}"]
    # Unlock hashes also cover bundled executable helpers.
    names.extend(["bin/fastboot", "bin/fastboot32"])
    hashes = {}
    for rel in dict.fromkeys(names):
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = f"{board}:{rel}\n".encode()
        path.write_bytes(body)
        hashes[rel] = hashlib.sha256(body).hexdigest()
    (base / "device.prop").write_text(f"DEVICE={board}\n", encoding="utf-8")
    hashes["device.prop"] = digest(base / "device.prop")
    return base.parent, hashes


def make_lineage(root: pathlib.Path, board: str):
    path = root / f"lineage-{board}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/com/android/metadata", f"pre-device={board}\n")
    return path


def make_shared_rootfs(root: pathlib.Path, boards=("crown", "checkers"), version="v1-test", more_boards=None):
    path = root / "jarvis-show-rootfs.tar.gz"
    marker = {
        "product": "genbu-show-v1",
        "boards": list(boards),
        "version": version,
    }
    if more_boards is not None:
        marker["more_boards"] = list(more_boards)
    payload = json.dumps(marker).encode()
    with tarfile.open(path, "w:gz") as archive:
        marker = tarfile.TarInfo("./etc/jarvis-show-release.json")
        marker.mode = 0o644
        marker.size = len(payload)
        archive.addfile(marker, io.BytesIO(payload))
        init = b"#!/bin/sh\n"
        member = tarfile.TarInfo("./sbin/init")
        member.mode = 0o755
        member.size = len(init)
        archive.addfile(member, io.BytesIO(init))
    return path


def make_backup(root: pathlib.Path, *, product: str, serial: str):
    backup_root = root / serial
    parts = backup_root / "partitions"
    parts.mkdir(parents=True)
    entries = []
    for number in recovery.SMALL_PARTITIONS:
        name = f"p{number}-part.img"
        body = (f"p{number}" * 20).encode()
        (parts / name).write_bytes(body)
        entries.append((hashlib.sha256(body).hexdigest(), name))
    for boot in recovery.BOOT_AREAS:
        name = f"mmcblk0{boot}.img"
        body = (boot * 20).encode()
        (parts / name).write_bytes(body)
        entries.append((hashlib.sha256(body).hexdigest(), name))
    (parts / "SHA256SUMS").write_text(
        "".join(f"{sha}  {name}\n" for sha, name in entries), encoding="utf-8"
    )
    (parts / "manifest.json").write_text(json.dumps({
        "complete": True,
        "product": product,
        "fastboot_serial": serial,
        "adb_serial": serial,
        "files": len(entries),
        "partitions": list(recovery.SMALL_PARTITIONS),
        "boot_areas": list(recovery.BOOT_AREAS),
    }), encoding="utf-8")
    return backup_root


class MultiBoardTests(unittest.TestCase):
    def runner(self, product, unlock_status="true", serial="SHOW123"):
        def run(argv, **kwargs):
            if argv == ["fastboot", "devices"]:
                return cp(argv, stdout=f"{serial}\tfastboot\n")
            name = argv[-1]
            values = {
                "product": product,
                "unlock_status": unlock_status,
                "lk_build_desc": f"lk-{product.lower()}",
                "serialno": serial,
            }
            return cp(argv, stderr=f"{name}: {values[name]}\n")
        return run

    def test_board_profiles_are_distinct_and_exact(self):
        crown = profile_for_board("crown")
        checkers = profile_for_board("checkers")
        self.assertEqual((crown.fastboot_product, crown.display_width, crown.display_height), ("CROWN", 1280, 800))
        self.assertEqual((checkers.fastboot_product, checkers.display_width, checkers.display_height), ("CHECKERS", 960, 480))
        self.assertEqual(profile_for_product("CHECKERS"), checkers)
        self.assertEqual((crown.install_confirmation, checkers.install_confirmation), ("ERASE", "ERASE"))
        self.assertEqual((crown.unlock_confirmation, checkers.unlock_confirmation), ("UNLOCK", "UNLOCK"))

    def test_generic_identity_accepts_both_and_cross_target_fails(self):
        self.assertEqual(identify_show(run=self.runner("CROWN")).product, "CROWN")
        self.assertEqual(identify_show(run=self.runner("CHECKERS")).product, "CHECKERS")
        with self.assertRaisesRegex(DeviceGateError, "expected crown"):
            identify_show(expected_board="crown", run=self.runner("CHECKERS"))
        with self.assertRaisesRegex(DeviceGateError, "expected checkers"):
            identify_show(expected_board="checkers", run=self.runner("CROWN"))

    def test_checkers_preflight_accepts_only_checkers_assets(self):
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            repo = td / "repo"; repo.mkdir()
            amonet, _ = make_amonet(td, "checkers")
            lineage = make_lineage(td, "checkers")
            with mock.patch.object(preflight.sys, "platform", "linux"), \
                 mock.patch.object(preflight.sys, "version_info", (3, 13, 0)), \
                 mock.patch.object(preflight.os, "geteuid", return_value=0), \
                 mock.patch.object(preflight.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}"), \
                 mock.patch.object(preflight, "_modemmanager_active", return_value=False), \
                 mock.patch.object(preflight.shutil, "disk_usage", return_value=mock.Mock(free=20 * 1024**3)):
                checks = preflight.run_preflight(
                    repo_root=repo, amonet_dir=amonet,
                    work_dir=td / "work", backup_dir=td / "backups",
                    lineage_zip=lineage, board="checkers",
                )
            self.assertTrue(preflight.preflight_ok(checks))
            wrong = make_lineage(td, "crown")
            with mock.patch.object(preflight.sys, "platform", "linux"), \
                 mock.patch.object(preflight.sys, "version_info", (3, 13, 0)), \
                 mock.patch.object(preflight.os, "geteuid", return_value=0), \
                 mock.patch.object(preflight.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}"), \
                 mock.patch.object(preflight, "_modemmanager_active", return_value=False), \
                 mock.patch.object(preflight.shutil, "disk_usage", return_value=mock.Mock(free=20 * 1024**3)):
                checks = preflight.run_preflight(
                    repo_root=repo, amonet_dir=amonet,
                    work_dir=td / "work2", backup_dir=td / "backups2",
                    lineage_zip=wrong, board="checkers",
                )
            self.assertFalse(preflight.preflight_ok(checks))

    def test_checkers_unlock_is_pin_gated_and_needs_the_exact_confirmation(self):
        identity = DeviceIdentity("CHK123", "CHECKERS", False, "lk")
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            amonet, hashes = make_amonet(td, "checkers")
            run = mock.Mock(return_value=unlock.FastbrickResult(0, "Exploit most likely successful!\n"))
            with self.assertRaisesRegex(unlock.UnlockError, "not been cryptographically pinned"):
                unlock.unlock_show(identity, amonet, confirmation="UNLOCK", run=run)
            run.assert_not_called()
            with self.assertRaisesRegex(unlock.UnlockError, "exact confirmation"):
                unlock.unlock_show(identity, amonet, confirmation="UNLOCK NOW", run=run, expected_hashes=hashes)
            run.assert_not_called()

            def adb(argv, **kwargs):
                if argv == ["adb", "devices"]:
                    return cp(argv, stdout="List of devices attached\nTWRP-CHK\trecovery\n")
                answers = {
                    "getprop ro.product.device": "checkers",
                    "id": "uid=0(root)",
                    "test -b /dev/block/mmcblk0p9 && echo OK": "OK",
                    "getprop ro.serialno": "CHK123",
                }
                return cp(argv, stdout=answers.get(argv[-1], "PLAIN") + "\n")

            result = unlock.unlock_show(
                identity, amonet, confirmation="UNLOCK",
                run=run, expected_hashes=hashes, adb_run=adb,
                identify=lambda: self.fail("fastboot is not asked when TWRP answers"),
                sleep=lambda _: None, say=lambda _line: None,
            )
            self.assertTrue(result.amonet_invoked)
            self.assertTrue(result.identity.unlocked)

    def test_checkers_existing_twrp_requires_no_fastboot_write(self):
        identity = DeviceIdentity("FAST-CHK", "CHECKERS", True, "lk")
        def adb(argv, **kwargs):
            if argv == ["adb", "devices"]:
                return cp(argv, stdout="List of devices attached\nADB-CHK\trecovery\n")
            command = argv[-1]
            if command == "getprop ro.product.device":
                return cp(argv, stdout="checkers\n")
            if command == "id":
                return cp(argv, stdout="uid=0(root) gid=0(root)\n")
            if command.startswith("test -b /dev/block/mmcblk0p9"):
                return cp(argv, stdout="OK\n")
            if command.startswith("tmp=/tmp/jarvis-boot-prefix."):
                return cp(argv, stdout="PLAIN\n")
            raise AssertionError(argv)
        fastboot = mock.Mock()
        session = recovery.ensure_twrp(identity, pathlib.Path("/unused"), adb_run=adb, fastboot_run=fastboot)
        self.assertFalse(session.twrp_flashed)
        fastboot.assert_not_called()

    def test_checkers_twrp_flash_is_held_to_the_pin_and_only_writes_recovery_swdl(self):
        identity = DeviceIdentity("FAST-CHK", "CHECKERS", True, "lk")
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            amonet, _ = make_amonet(td, "checkers")
            twrp = amonet / "amonet" / "bin" / "twrp.img"
            twrp_hash = digest(twrp)
            adb_calls = {"n": 0}
            def adb(argv, **kwargs):
                if argv == ["adb", "devices"]:
                    adb_calls["n"] += 1
                    if adb_calls["n"] == 1:
                        return cp(argv, stdout="List of devices attached\n")
                    return cp(argv, stdout="List of devices attached\nADB-CHK\trecovery\n")
                if argv[-1] == "getprop ro.product.device":
                    return cp(argv, stdout="checkers\n")
                if argv[-1] == "id":
                    return cp(argv, stdout="uid=0(root) gid=0(root)\n")
                if argv[-1].startswith("test -b /dev/block/mmcblk0p9"):
                    return cp(argv, stdout="OK\n")
                if argv[-1].startswith("tmp=/tmp/jarvis-boot-prefix."):
                    return cp(argv, stdout="PLAIN\n")
                raise AssertionError(argv)
            with self.assertRaisesRegex(recovery.RecoveryError, "TWRP image hash mismatch for checkers"):
                recovery.ensure_twrp(identity, amonet, adb_run=lambda a, **k: cp(a, stdout="List of devices attached\n"), identify=lambda: identity, sleep=lambda _: None, wait_attempts=1)
            calls = []
            def fastboot(argv, **kwargs):
                calls.append(tuple(argv)); return cp(argv)
            session = recovery.ensure_twrp(
                identity, amonet, adb_run=adb, fastboot_run=fastboot,
                identify=lambda: identity, expected_twrp_sha256=twrp_hash,
                sleep=lambda _: None, wait_attempts=2,
            )
            self.assertTrue(session.twrp_flashed)
            joined = " ".join(" ".join(call) for call in calls)
            self.assertIn(" flash recovery ", f" {joined} ")
            self.assertIn(" flash swdl ", f" {joined} ")
            for forbidden in (" lk ", " preloader ", " expdb ", " tee1 ", " tee2 ", " boot ", " system ", " userdata "):
                self.assertNotIn(forbidden, f" {joined} ")

    def test_backup_is_board_bound(self):
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            crown = make_backup(td, product="CROWN", serial="CROWN123")
            recovery.verify_backup(crown / "partitions", expected_product="CROWN")
            with self.assertRaisesRegex(recovery.RecoveryError, "expected CHECKERS"):
                recovery.verify_backup(crown / "partitions", expected_product="CHECKERS")

    def test_shared_rootfs_allows_both_but_not_unlisted_board(self):
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            shared = make_shared_rootfs(td)
            sha = digest(shared)
            self.assertEqual(verify_jarvis_rootfs(shared, sha, board="crown").board, "shared")
            self.assertEqual(verify_jarvis_rootfs(shared, sha, board="checkers").board, "shared")
            crown_only = make_shared_rootfs(td, boards=("crown",), version="crown-only")
            with self.assertRaisesRegex(InstallError, "not requested board 'checkers'"):
                verify_jarvis_rootfs(crown_only, digest(crown_only), board="checkers")

    def test_cronos_comes_from_more_boards_and_stays_off_older_markers(self):
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            newer = make_shared_rootfs(td, more_boards=("cronos",), version="with-cronos")
            identity = verify_jarvis_rootfs(newer, digest(newer), board="cronos")
            self.assertEqual(identity.boards, ("crown", "checkers", "cronos"))
            older = make_shared_rootfs(td, version="before-cronos")
            with self.assertRaisesRegex(InstallError, "not requested board 'cronos'"):
                verify_jarvis_rootfs(older, digest(older), board="cronos")
            broken = make_shared_rootfs(td, more_boards=("rooks",), version="unknown-board")
            with self.assertRaisesRegex(InstallError, "invalid supported boards"):
                verify_jarvis_rootfs(broken, digest(broken), board="crown")

    def test_cronos_unlock_stays_closed_and_twrp_is_held_to_its_pin(self):
        identity = DeviceIdentity("CRN123", "CRONOS", False, "lk")
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            amonet, _ = make_amonet(td, "cronos")
            run = mock.Mock(return_value=cp(["bash", "fastbrick.sh"]))
            with self.assertRaisesRegex(unlock.UnlockError, "not been cryptographically pinned"):
                unlock.unlock_show(identity, amonet, confirmation="UNLOCK", run=run)
            run.assert_not_called()
            twrp = td / "bundle" / "amonet" / "bin" / "twrp.img"
            twrp.parent.mkdir(parents=True)
            twrp.write_bytes(b"twrp")
            with self.assertRaisesRegex(recovery.RecoveryError, "TWRP image hash mismatch for cronos"):
                recovery._pinned_twrp(td / "bundle", "cronos")

    def test_cronos_reads_its_own_signed_manifest(self):
        import techo5lib
        self.assertEqual(techo5lib.manifest_name("cronos"), "manifest-cronos.json")
        self.assertEqual(techo5lib.manifest_name("crown"), "manifest.json")
        self.assertEqual(techo5lib.manifest_name("checkers"), "manifest.json")
        self.assertEqual(techo5lib.manifest_name(None), "manifest.json")

    def test_checkers_install_plan_requires_pinned_boot_and_checkers_confirmation(self):
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            rootfs = make_shared_rootfs(td)
            boot = td / "checkers-boot.img"; boot.write_bytes(b"test-checkers-boot")
            backup = make_backup(td, product="CHECKERS", serial="CHK-ADB")
            with self.assertRaisesRegex(InstallError, "not the pinned known-good image"):
                make_install_plan(
                    repo_root=ROOT, adb_serial="CHK-ADB", name="Kitchen",
                    backup_root=backup, work_dir=td / "work", boot_image=boot,
                    rootfs=rootfs, rootfs_sha256=digest(rootfs), board="checkers",
                    confirmation="ERASE",
                )
            with self.assertRaisesRegex(InstallError, "final confirmation"):
                make_install_plan(
                    repo_root=ROOT, adb_serial="CHK-ADB", name="Kitchen",
                    backup_root=backup, work_dir=td / "work", boot_image=boot,
                    boot_sha256=digest(boot), rootfs=rootfs, rootfs_sha256=digest(rootfs),
                    board="checkers", confirmation="ERASE CROWN",
                )
            plan = make_install_plan(
                repo_root=ROOT, adb_serial="CHK-ADB", name="Kitchen",
                backup_root=backup, work_dir=td / "work", boot_image=boot,
                boot_sha256=digest(boot), rootfs=rootfs, rootfs_sha256=digest(rootfs),
                board="checkers", confirmation="ERASE",
            )
            argv = list(plan.argv)
            self.assertEqual(plan.board, "checkers")
            self.assertEqual(argv[argv.index("--jarvis-show-board") + 1], "checkers")
            self.assertIn("--amazon-logo", argv)
            self.assertNotIn("--lineage-zip", argv)


if __name__ == "__main__":
    unittest.main()
