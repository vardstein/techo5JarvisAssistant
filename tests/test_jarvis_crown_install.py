import hashlib
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
import sys
sys.path.insert(0, str(TOOLS))

from jarvis_crown.install import (  # noqa: E402
    CROWN_BOOT_SHA256,
    FINAL_CONFIRM_PHRASE,
    InstallError,
    execute_install,
    make_install_plan,
    verify_crown_boot,
    verify_jarvis_rootfs,
)
from jarvis_crown.recovery import BOOT_AREAS, SMALL_PARTITIONS  # noqa: E402

PINNED_BOOT = ROOT / "build" / "release-techo5-v0.8.0" / "techo5-boot-crown-v0.8.0.img"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def make_rootfs(folder, *, product="genbu-show-v1", board="crown", version="v1-test"):
    path = pathlib.Path(folder) / "rootfs.tar.gz"
    payload = json.dumps({"product": product, "boards": [board], "version": version}).encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("./etc/jarvis-show-release.json")
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
        init = b"#!/bin/sh\n"
        info = tarfile.TarInfo("./sbin/init")
        info.size = len(init)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(init))
    return path


def make_backup(folder, serial="CROWN123"):
    root = pathlib.Path(folder) / serial
    parts = root / "partitions"
    parts.mkdir(parents=True)
    entries = []
    for number in SMALL_PARTITIONS:
        name = f"p{number}-part.img"
        data = (f"p{number}" * 20).encode()
        (parts / name).write_bytes(data)
        entries.append((hashlib.sha256(data).hexdigest(), name))
    for boot in BOOT_AREAS:
        name = f"mmcblk0{boot}.img"
        data = (boot * 20).encode()
        (parts / name).write_bytes(data)
        entries.append((hashlib.sha256(data).hexdigest(), name))
    (parts / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for digest, name in entries), encoding="utf-8"
    )
    (parts / "manifest.json").write_text(
        json.dumps({
            "complete": True,
            "product": "CROWN",
            "fastboot_serial": serial,
            "adb_serial": serial,
            "files": len(entries),
            "partitions": list(SMALL_PARTITIONS),
            "boot_areas": list(BOOT_AREAS),
        }),
        encoding="utf-8",
    )
    return root


class JarvisCrownInstallTests(unittest.TestCase):
    def test_pinned_boot_is_exact_known_good_image(self):
        verify_crown_boot(PINNED_BOOT)
        self.assertEqual(sha256(PINNED_BOOT), CROWN_BOOT_SHA256)

    def test_modified_boot_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            fake = pathlib.Path(td) / "boot.img"
            fake.write_bytes(b"not crown boot")
            with self.assertRaisesRegex(InstallError, "not the pinned"):
                verify_crown_boot(fake)

    def test_rootfs_requires_hash_and_product_marker(self):
        with tempfile.TemporaryDirectory() as td:
            rootfs = make_rootfs(td)
            ident = verify_jarvis_rootfs(rootfs, sha256(rootfs))
            self.assertEqual(ident.product, "genbu-show-v1")
            self.assertEqual(ident.board, "crown")
            self.assertEqual(ident.version, "v1-test")
            with self.assertRaisesRegex(InstallError, "SHA-256 mismatch"):
                verify_jarvis_rootfs(rootfs, "0" * 64)

    def test_actual_archived_techo5_rootfs_is_rejected_as_not_jarvis(self):
        upstream = ROOT / "build" / "release-techo5-v0.9.21" / "rootfs-v0.9.21.tar.gz"
        with self.assertRaisesRegex(InstallError, "does not contain a regular"):
            verify_jarvis_rootfs(upstream, sha256(upstream))

    def test_upstream_or_wrong_board_rootfs_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            rootfs = make_rootfs(td, product="techo5", board="crown")
            with self.assertRaisesRegex(InstallError, "rootfs marker is not 'genbu-show-v1'"):
                verify_jarvis_rootfs(rootfs, sha256(rootfs))
            # An image from before the rename names the old product and is refused the same way.
            rootfs = make_rootfs(td, product="jarvis-show-v1", board="crown")
            with self.assertRaisesRegex(InstallError, "rootfs marker is not 'genbu-show-v1'"):
                verify_jarvis_rootfs(rootfs, sha256(rootfs))

    def test_final_confirmation_is_exact(self):
        with tempfile.TemporaryDirectory() as td:
            rootfs = make_rootfs(td)
            backup = make_backup(td)
            with self.assertRaisesRegex(InstallError, "final confirmation"):
                make_install_plan(
                    repo_root=ROOT,
                    adb_serial="CROWN123",
                    name="Living Room",
                    backup_root=backup,
                    work_dir=pathlib.Path(td) / "work",
                    boot_image=PINNED_BOOT,
                    rootfs=rootfs,
                    rootfs_sha256=sha256(rootfs),
                    confirmation="yes",
                )

    def test_backup_must_match_current_recovery_serial(self):
        with tempfile.TemporaryDirectory() as td:
            rootfs = make_rootfs(td)
            backup = make_backup(td, serial="OTHER")
            with self.assertRaisesRegex(InstallError, "backup root must|not current recovery serial"):
                make_install_plan(
                    repo_root=ROOT,
                    adb_serial="CROWN123",
                    name="Living Room",
                    backup_root=backup,
                    work_dir=pathlib.Path(td) / "work",
                    boot_image=PINNED_BOOT,
                    rootfs=rootfs,
                    rootfs_sha256=sha256(rootfs),
                    confirmation=FINAL_CONFIRM_PHRASE,
                )

    def test_plan_forces_safe_prestaged_path_and_keeps_expdb_logo_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            rootfs = make_rootfs(td)
            backup = make_backup(td)
            plan = make_install_plan(
                repo_root=ROOT,
                adb_serial="CROWN123",
                name="Living Room",
                backup_root=backup,
                work_dir=pathlib.Path(td) / "work",
                boot_image=PINNED_BOOT,
                rootfs=rootfs,
                rootfs_sha256=sha256(rootfs),
                confirmation=FINAL_CONFIRM_PHRASE,
            )
            argv = list(plan.argv)
            self.assertIn("--jarvis-show-prestaged", argv)
            self.assertIn("--amazon-logo", argv)
            self.assertIn("--force", argv)
            self.assertNotIn("--lineage-zip", argv)
            self.assertEqual(argv[argv.index("--jarvis-show-version") + 1], "v1-test")
            self.assertEqual(argv[argv.index("--jarvis-show-board") + 1], "crown")
            self.assertEqual(argv[argv.index("--serial") + 1], "CROWN123")

    def test_execute_uses_argv_without_shell_and_propagates_failure(self):
        plan = mock.Mock(argv=("python3", "install-show.py"))
        ok = mock.Mock(returncode=0)
        run = mock.Mock(return_value=ok)
        execute_install(plan, run=run)
        run.assert_called_once_with(["python3", "install-show.py"], check=False)
        run.reset_mock()
        run.return_value = mock.Mock(returncode=9)
        with self.assertRaisesRegex(InstallError, "exit code 9"):
            execute_install(plan, run=run)

    def test_plan_passes_show_settings_as_switches_and_files(self):
        with tempfile.TemporaryDirectory() as td:
            rootfs = make_rootfs(td)
            backup = make_backup(td)
            token = pathlib.Path(td) / "ha_token"
            key = pathlib.Path(td) / "dashcast_key"
            common = dict(
                repo_root=ROOT, adb_serial="CROWN123", name="Living Room", backup_root=backup,
                work_dir=pathlib.Path(td) / "work", boot_image=PINNED_BOOT, rootfs=rootfs,
                rootfs_sha256=sha256(rootfs), confirmation=FINAL_CONFIRM_PHRASE,
            )
            argv = list(make_install_plan(
                **common, ha_url="http://ha.example:8123", ha_token_file=token,
                dashcast="10.0.0.5:9555", dashcast_key_file=key, music_assistant="10.0.0.6",
            ).argv)
            self.assertEqual(argv[argv.index("--ha-url") + 1], "http://ha.example:8123")
            self.assertEqual(argv[argv.index("--ha-token-file") + 1], str(token))
            self.assertEqual(argv[argv.index("--dashcast") + 1], "10.0.0.5:9555")
            self.assertEqual(argv[argv.index("--dashcast-key-file") + 1], str(key))
            self.assertEqual(argv[argv.index("--music-assistant") + 1], "10.0.0.6")
            pw = pathlib.Path(td) / "root_password"
            argv = list(make_install_plan(**common, root_password_file=pw).argv)
            self.assertEqual(argv[argv.index("--root-password-file") + 1], str(pw))
            plain = list(make_install_plan(**common).argv)
            for flag in ("--ha-url", "--dashcast", "--music-assistant", "--root-password-file"):
                self.assertNotIn(flag, plain)
            with self.assertRaises(InstallError):
                make_install_plan(**common, ha_url="http://ha.example:8123")
            with self.assertRaises(InstallError):
                make_install_plan(**common, dashcast_key_file=key)


if __name__ == "__main__":
    unittest.main()
