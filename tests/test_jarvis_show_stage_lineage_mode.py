import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import recovery  # noqa: E402


def load_install_show():
    path = ROOT / "tools" / "install-show.py"
    spec = importlib.util.spec_from_file_location("jarvis_show_install_stage_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_backup(root: pathlib.Path, product: str, serial: str):
    parts = root / serial / "partitions"
    parts.mkdir(parents=True)
    entries = []
    for number in recovery.SMALL_PARTITIONS:
        name = f"p{number}-part.img"
        data = (f"p{number}" * 20).encode()
        (parts / name).write_bytes(data)
        entries.append((hashlib.sha256(data).hexdigest(), name))
    for boot in recovery.BOOT_AREAS:
        name = f"mmcblk0{boot}.img"
        data = (boot * 20).encode()
        (parts / name).write_bytes(data)
        entries.append((hashlib.sha256(data).hexdigest(), name))
    (parts / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for digest, name in entries), encoding="utf-8"
    )
    (parts / "manifest.json").write_text(json.dumps({
        "complete": True,
        "product": product,
        "fastboot_serial": "FAST123",
        "adb_serial": serial,
        "files": len(entries),
        "partitions": list(recovery.SMALL_PARTITIONS),
        "boot_areas": list(recovery.BOOT_AREAS),
    }), encoding="utf-8")


def make_lineage(root: pathlib.Path, board: str):
    path = root / f"lineage-{board}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/com/android/metadata", f"pre-device={board}\n")
    return path


class FakeAdb:
    def __init__(self, serial, binary, board):
        self.serial = serial
        self.board = board

    def state(self):
        return "recovery"

    def sh(self, command):
        if command == "getprop ro.product.device":
            return self.board
        raise AssertionError(f"unexpected shell command before staged helper: {command}")


class StageLineageModeTests(unittest.TestCase):
    def exercise(self, board, product):
        module = load_install_show()
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            backups = td / "backups"
            work = td / "work"
            serial = "ADB123"
            make_backup(backups, product, serial)
            lineage = make_lineage(td, board)
            calls = []

            def fake_adb(serial_arg, binary):
                return FakeAdb(serial_arg, binary, board)

            argv = [
                "install-show.py",
                "--serial", serial,
                "--lineage-zip", str(lineage),
                "--jarvis-show-stage-lineage-only",
                "--jarvis-show-board", board,
                "--backups", str(backups),
                "--work", str(work),
            ]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(module, "need", return_value=None), \
                 mock.patch.object(module, "pick_unit", return_value=serial), \
                 mock.patch.object(module, "Adb", side_effect=fake_adb), \
                 mock.patch.object(module, "Fastboot", return_value=mock.Mock()), \
                 mock.patch.object(module, "Console", return_value=mock.Mock()), \
                 mock.patch.object(module, "prepare_lineage_vendor", side_effect=lambda adb, lineage_zip=None, prestaged=False, fastboot=None: calls.append((lineage_zip, prestaged))), \
                 mock.patch.object(module, "Release", side_effect=AssertionError("release lookup must never run")), \
                 mock.patch.object(module, "check_serial_access", side_effect=AssertionError("stage-only returned too late")):
                out = io.StringIO()
                with mock.patch.object(sys, "stdout", out):
                    module.main()
            self.assertEqual(calls, [(str(lineage), False)])
            self.assertIn(f"LineageOS drivers for {board} are in place", out.getvalue())
            self.assertNotIn("Done.", out.getvalue())

    def test_crown_stage_only_returns_before_release_flash_store(self):
        self.exercise("crown", "CROWN")

    def test_checkers_stage_only_returns_before_release_flash_store(self):
        self.exercise("checkers", "CHECKERS")


if __name__ == "__main__":
    unittest.main()
