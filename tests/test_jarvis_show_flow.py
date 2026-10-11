import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.boards import profile_for_board  # noqa: E402
from jarvis_crown.device_gate import DeviceIdentity  # noqa: E402
from jarvis_crown.flow import (  # noqa: E402
    FlowDeps,
    FlowError,
    InstallInputs,
    lineage_stage_argv,
    run_install_flow,
)
from jarvis_crown.preflight import Check  # noqa: E402
from jarvis_crown.recovery import BackupResult, RecoverySession  # noqa: E402
from jarvis_crown.unlock import UnlockResult  # noqa: E402


class FlowHarness:
    def __init__(self, *, board="crown", locked=False):
        self.board = board
        self.profile = profile_for_board(board)
        self.identity = DeviceIdentity("FAST123", self.profile.fastboot_product, not locked, "lk")
        self.unlocked = DeviceIdentity("FAST123", self.profile.fastboot_product, True, "lk")
        self.calls = []
        self.plan = mock.Mock(board=board, argv=("installer",), rootfs=mock.Mock(), backup=pathlib.Path("/backup"))

    def preflight(self, **kwargs):
        self.calls.append("preflight")
        self.preflight_kwargs = kwargs
        return [Check("PASS", "ready", "ok")]

    def identify(self, **kwargs):
        self.calls.append("identify")
        self.identify_kwargs = kwargs
        return self.identity

    def unlock(self, identity, amonet_dir, **kwargs):
        self.calls.append("unlock")
        self.unlock_kwargs = kwargs
        return UnlockResult(self.unlocked, True)

    def recovery(self, identity, amonet_dir, **kwargs):
        self.calls.append("recovery")
        self.recovery_kwargs = kwargs
        return RecoverySession("ADB123", False)

    def client_factory(self, serial):
        self.calls.append("client")
        return mock.Mock(serial=serial)

    def backup(self, client, backup_root, **kwargs):
        self.calls.append("backup")
        self.backup_root = backup_root
        self.backup_kwargs = kwargs
        return BackupResult(backup_root / "partitions", 15, False)

    def make_plan(self, **kwargs):
        self.calls.append("plan")
        self.plan_kwargs = kwargs
        return self.plan

    def stage(self, inputs, serial):
        self.calls.append("stage")
        self.stage_serial = serial

    def execute(self, plan):
        self.calls.append("execute")
        self.executed_plan = plan

    def deps(self):
        return FlowDeps(
            preflight=self.preflight,
            identify=self.identify,
            unlock=self.unlock,
            recovery=self.recovery,
            backup=self.backup,
            make_plan=self.make_plan,
            stage_lineage=self.stage,
            execute=self.execute,
            client_factory=self.client_factory,
        )


def inputs(td: pathlib.Path, board="crown"):
    return InstallInputs(
        board=board,
        repo_root=ROOT,
        amonet_dir=td / f"amonet-{board}",
        lineage_zip=td / f"lineage-{board}.zip",
        work_dir=td / "work",
        backups_dir=td / "backups",
        name="Living Room" if board == "crown" else "Kitchen",
        boot_image=td / f"boot-{board}.img",
        rootfs=td / "rootfs.tar.gz",
        rootfs_sha256="a" * 64,
        boot_sha256="b" * 64 if board == "checkers" else None,
        twrp_sha256="c" * 64 if board == "checkers" else None,
        amonet_hashes={"fastbrick.sh": "d" * 64} if board == "checkers" else None,
    )


class FlowAcceptanceTests(unittest.TestCase):
    def test_unlocked_crown_one_command_order(self):
        with tempfile.TemporaryDirectory() as td_s:
            td = pathlib.Path(td_s)
            h = FlowHarness(board="crown", locked=False)
            result = run_install_flow(
                inputs(td, "crown"),
                confirm_unlock=lambda _p: self.fail("unlock confirmation must not be requested"),
                confirm_install=lambda p: p.install_confirmation,
                deps=h.deps(),
            )
            self.assertEqual(
                h.calls,
                ["preflight", "identify", "recovery", "client", "backup", "plan", "stage", "execute"],
            )
            self.assertEqual(result.profile.board, "crown")
            self.assertEqual(h.identify_kwargs["expected_board"], "crown")
            self.assertEqual(h.plan_kwargs["confirmation"], profile_for_board("crown").install_confirmation)
            self.assertEqual(h.plan_kwargs["board"], "crown")
            self.assertEqual(h.stage_serial, "ADB123")
            record = json.loads((td / "backups" / "ADB123" / "show.json").read_text())
            self.assertEqual((record["name"], record["board"]), ("Living Room", "crown"))

    def test_verified_recovery_identity_avoids_second_fastboot_query(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness(board="crown", locked=False)
            result = run_install_flow(
                inputs(pathlib.Path(td_s), "crown"),
                confirm_unlock=lambda _p: self.fail("recovery identity is already unlocked"),
                confirm_install=lambda p: p.install_confirmation,
                deps=h.deps(),
                initial_identity=h.identity,
            )
            self.assertEqual(result.identity, h.identity)
            self.assertNotIn("identify", h.calls)
            self.assertEqual(h.calls[0], "preflight")

    def test_locked_crown_unlocks_before_recovery(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness(board="crown", locked=True)
            run_install_flow(
                inputs(pathlib.Path(td_s), "crown"),
                confirm_unlock=lambda p: p.unlock_confirmation,
                confirm_install=lambda p: p.install_confirmation,
                deps=h.deps(),
            )
            self.assertEqual(h.calls[:4], ["preflight", "identify", "unlock", "recovery"])
            self.assertIsNone(h.unlock_kwargs["expected_hashes"])

    def test_checkers_uses_same_flow_with_board_pins(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness(board="checkers", locked=True)
            data = inputs(pathlib.Path(td_s), "checkers")
            result = run_install_flow(
                data,
                confirm_unlock=lambda p: p.unlock_confirmation,
                confirm_install=lambda p: p.install_confirmation,
                deps=h.deps(),
            )
            self.assertEqual(result.profile.board, "checkers")
            self.assertEqual(h.identify_kwargs["expected_board"], "checkers")
            self.assertEqual(h.unlock_kwargs["expected_hashes"], data.amonet_hashes)
            self.assertEqual(h.recovery_kwargs["expected_twrp_sha256"], data.twrp_sha256)
            self.assertEqual(h.plan_kwargs["boot_sha256"], data.boot_sha256)
            self.assertEqual(h.plan_kwargs["board"], "checkers")

    def test_wrong_board_identity_stops_before_unlock_or_recovery(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness(board="crown", locked=False)
            def wrong_identity(**kwargs):
                h.calls.append("identify")
                raise RuntimeError("connected CHECKERS, expected crown")
            deps = h.deps(); deps.identify = wrong_identity
            with self.assertRaisesRegex(RuntimeError, "expected crown"):
                run_install_flow(
                    inputs(pathlib.Path(td_s), "crown"),
                    confirm_unlock=lambda p: p.unlock_confirmation,
                    confirm_install=lambda p: p.install_confirmation,
                    deps=deps,
                )
            self.assertEqual(h.calls, ["preflight", "identify"])

    def test_preflight_failure_stops_before_device_query(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness()
            def bad_preflight(**kwargs):
                h.calls.append("preflight")
                return [Check("FAIL", "lineage", "wrong board")]
            deps = h.deps(); deps.preflight = bad_preflight
            with self.assertRaisesRegex(FlowError, "preflight failed"):
                run_install_flow(
                    inputs(pathlib.Path(td_s)),
                    confirm_unlock=lambda p: p.unlock_confirmation,
                    confirm_install=lambda p: p.install_confirmation,
                    deps=deps,
                )
            self.assertEqual(h.calls, ["preflight"])

    def test_bad_final_confirmation_stops_before_first_userdata_write(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness()
            with self.assertRaisesRegex(FlowError, "final confirmation"):
                run_install_flow(
                    inputs(pathlib.Path(td_s)),
                    confirm_unlock=lambda p: p.unlock_confirmation,
                    confirm_install=lambda _p: "yes",
                    deps=h.deps(),
                )
            self.assertEqual(h.calls, ["preflight", "identify", "recovery", "client", "backup", "plan"])
            self.assertNotIn("stage", h.calls)
            self.assertNotIn("execute", h.calls)

    def test_bad_local_install_input_stops_before_confirmation_and_lineage(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness()
            def bad_plan(**kwargs):
                h.calls.append("plan")
                raise RuntimeError("bad rootfs")
            deps = h.deps(); deps.make_plan = bad_plan
            confirm = mock.Mock(side_effect=AssertionError("confirmation must not be requested"))
            with self.assertRaisesRegex(RuntimeError, "bad rootfs"):
                run_install_flow(
                    inputs(pathlib.Path(td_s)),
                    confirm_unlock=lambda p: p.unlock_confirmation,
                    confirm_install=confirm,
                    deps=deps,
                )
            confirm.assert_not_called()
            self.assertNotIn("stage", h.calls)
            self.assertNotIn("execute", h.calls)

    def test_failed_backup_stops_before_plan_and_lineage(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness()
            def bad_backup(*args, **kwargs):
                h.calls.append("backup")
                raise RuntimeError("backup hash mismatch")
            deps = h.deps(); deps.backup = bad_backup
            with self.assertRaisesRegex(RuntimeError, "backup hash mismatch"):
                run_install_flow(
                    inputs(pathlib.Path(td_s)),
                    confirm_unlock=lambda p: p.unlock_confirmation,
                    confirm_install=lambda p: p.install_confirmation,
                    deps=deps,
                )
            self.assertEqual(h.calls, ["preflight", "identify", "recovery", "client", "backup"])

    def test_failed_lineage_stage_never_executes_slot_store_plan(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness()
            def bad_stage(_inputs, _serial):
                h.calls.append("stage")
                raise FlowError("Lineage failed")
            deps = h.deps(); deps.stage_lineage = bad_stage
            with self.assertRaisesRegex(FlowError, "Lineage failed"):
                run_install_flow(
                    inputs(pathlib.Path(td_s)),
                    confirm_unlock=lambda p: p.unlock_confirmation,
                    confirm_install=lambda p: p.install_confirmation,
                    deps=deps,
                )
            self.assertNotIn("execute", h.calls)

    def test_lineage_stage_argv_is_vendor_only_and_board_bound(self):
        with tempfile.TemporaryDirectory() as td_s:
            for board in ("crown", "checkers"):
                data = inputs(pathlib.Path(td_s), board)
                argv = lineage_stage_argv(data, "ADB123")
                self.assertIn("--jarvis-show-stage-lineage-only", argv)
                self.assertEqual(argv[argv.index("--jarvis-show-board") + 1], board)
                self.assertEqual(argv[argv.index("--serial") + 1], "ADB123")
                self.assertIn("--lineage-zip", argv)
                self.assertNotIn("--rootfs", argv)
                self.assertNotIn("--boot", argv)
                self.assertNotIn("--force", argv)

    def test_install_show_provisions_board_specific_profile(self):
        source = (ROOT / "tools" / "install-show.py").read_text(encoding="utf-8")
        self.assertIn("if a.jarvis_show_prestaged:", source)
        self.assertIn("printf 'jarvis-%s-v1\\\\n'", source)
        self.assertNotIn("if a.jarvis_crown_prestaged:\n        prov +=", source)

    def test_jarvis_prestaged_path_requires_amazon_logo_safety_flag(self):
        source = (ROOT / "tools" / "install-show.py").read_text(encoding="utf-8")
        self.assertIn("if a.jarvis_show_prestaged and not a.amazon_logo:", source)
        self.assertIn("Jarvis Show never modifies expdb/kaeru", source)

    def test_auto_detected_serial_cannot_be_swapped_before_flow_identity(self):
        with tempfile.TemporaryDirectory() as td_s:
            h = FlowHarness(board="crown", locked=False)
            data = inputs(pathlib.Path(td_s), "crown")
            data = InstallInputs(**{**data.__dict__, "expected_fastboot_serial": "ORIGINAL123"})
            h.identity = DeviceIdentity("SWAPPED999", "CROWN", True, "lk")
            with self.assertRaisesRegex(FlowError, "device changed after auto-detection"):
                run_install_flow(
                    data,
                    confirm_unlock=lambda _p: "UNLOCK CROWN",
                    confirm_install=lambda _p: "ERASE",
                    deps=h.deps(),
                )
            self.assertNotIn("recovery", h.calls)
            self.assertNotIn("backup", h.calls)
            self.assertNotIn("stage", h.calls)
            self.assertNotIn("execute", h.calls)



if __name__ == "__main__":
    unittest.main()
