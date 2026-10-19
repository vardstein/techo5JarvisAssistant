import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from jarvis_crown.catalogue import profile_for_board  # noqa: E402
from jarvis_crown.device_gate import DeviceGateError, DeviceIdentity  # noqa: E402
from jarvis_crown import unlock  # noqa: E402

LANDED = "Sending payload, this might take a few attempts...\nExploit most likely successful!\n"


def fastbrick(output=LANDED, code=0):
    return mock.Mock(return_value=unlock.FastbrickResult(code, output))


class FakeTwrp:
    """adb as seen from the host while the Show writes and then boots TWRP."""

    def __init__(self, *, absent_polls=2, serial="ABC123", device="crown"):
        self.absent_polls = absent_polls
        self.serial = serial
        self.device = device
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv == ["adb", "devices"]:
            if self.absent_polls > 0:
                self.absent_polls -= 1
                return subprocess.CompletedProcess(argv, 0, "List of devices attached\n", "")
            return subprocess.CompletedProcess(argv, 0, "List of devices attached\nTWRP1\trecovery\n", "")
        answers = {
            "getprop ro.product.device": self.device,
            "id": "uid=0(root) gid=0(root)",
            "test -b /dev/block/mmcblk0p9 && echo OK": "OK",
            "getprop ro.serialno": self.serial,
        }
        command = argv[-1]
        if command in answers:
            return subprocess.CompletedProcess(argv, 0, answers[command] + "\n", "")
        if command.startswith("tmp=/tmp/jarvis-boot-prefix"):
            return subprocess.CompletedProcess(argv, 0, "PLAIN\n", "")
        raise AssertionError(argv)


class UnlockTests(unittest.TestCase):
    def identity(self, unlocked=False, serial="ABC123", product="CROWN"):
        return DeviceIdentity(serial=serial, product=product, unlocked=unlocked, lk_build_desc="lk")

    def bundle(self, td, pin=True):
        """A fixture bundle; with pin, its hashes stand in for crown's built-in pins for this test."""
        root = pathlib.Path(td) / "amonet-crown-v2.0.1" / "amonet"
        hashes = {}
        for rel in (
            "fastbrick.sh",
            "profile.sh",
            "device.prop",
            "bin/fastbrick.img",
            "bin/fastboot",
            "bin/fastboot32",
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            body = f"fixture:{rel}\n".encode()
            path.write_bytes(body)
            hashes[rel] = hashlib.sha256(body).hexdigest()
        if pin:
            self.enterContext(mock.patch.dict(unlock.AMONET_UNLOCK_SHA256, hashes))
        return root.parent, hashes

    def unlock(self, amonet, hashes, *, run=None, adb_run=None, identify=None, said=None):
        return unlock.unlock_crown(
            self.identity(), amonet,
            confirmation=unlock.CONFIRM_PHRASE,
            run=run or fastbrick(),
            adb_run=adb_run or FakeTwrp(),
            identify=identify or (lambda: self.fail("fastboot must not be asked while TWRP answers")),
            sleep=lambda _: None,
            expected_hashes=hashes,
            say=(said.append if said is not None else lambda _line: None),
        )

    def test_already_unlocked_skips_amonet_without_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            run = mock.Mock()
            result = unlock.unlock_crown(
                self.identity(unlocked=True), amonet,
                confirmation=None, run=run, expected_hashes=hashes,
            )
            self.assertFalse(result.amonet_invoked)
            run.assert_not_called()

    def test_wrong_product_never_invokes_amonet(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            run = mock.Mock()
            with self.assertRaisesRegex(unlock.UnlockError, "unsupported product"):
                unlock.unlock_crown(
                    self.identity(product="CHECKERS"), amonet,
                    confirmation=unlock.CONFIRM_PHRASE, run=run, expected_hashes=hashes,
                )
            run.assert_not_called()

    def test_locked_device_without_lk_build_is_refused_before_amonet(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            run = mock.Mock()
            identity = DeviceIdentity(serial="ABC123", product="CROWN", unlocked=False, lk_build_desc=None)
            with self.assertRaisesRegex(unlock.UnlockError, "no readable lk_build_desc"):
                unlock.unlock_crown(
                    identity, amonet,
                    confirmation=unlock.CONFIRM_PHRASE, run=run, expected_hashes=hashes,
                )
            run.assert_not_called()

    def test_hash_mismatch_blocks_execution(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            (amonet / "amonet" / "bin" / "fastbrick.img").write_bytes(b"tampered")
            run = mock.Mock()
            with self.assertRaisesRegex(unlock.UnlockError, "hash mismatch"):
                unlock.unlock_crown(
                    self.identity(), amonet,
                    confirmation=unlock.CONFIRM_PHRASE, run=run, expected_hashes=hashes,
                )
            run.assert_not_called()

    def test_a_manifest_must_name_exactly_the_unlock_files(self):
        # GENBU-172: a one-entry manifest left the other five files unchecked.
        identity = DeviceIdentity(serial="CHK123", product="CHECKERS", unlocked=False, lk_build_desc="lk")
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td, pin=False)
            run = mock.Mock()
            for manifest, why in (({"fastbrick.sh": hashes["fastbrick.sh"]}, "missing profile.sh"),
                                  ({**hashes, "bin/twrp.img": "0" * 64}, "unknown bin/twrp.img")):
                with self.assertRaisesRegex(unlock.UnlockError, f"must name exactly .*{why}"):
                    unlock.unlock_show(identity, amonet, confirmation="UNLOCK", run=run, expected_hashes=manifest)
            with self.assertRaisesRegex(unlock.UnlockError, "must name exactly"):
                unlock.verify_amonet_unlock_assets(amonet, expected_hashes={"fastbrick.sh": hashes["fastbrick.sh"]})
            run.assert_not_called()

    def test_crown_keeps_its_built_in_pins(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td, pin=False)
            run = mock.Mock()
            with self.assertRaisesRegex(unlock.UnlockError, "built in"):
                unlock.unlock_crown(self.identity(), amonet, confirmation=unlock.CONFIRM_PHRASE, run=run,
                                    expected_hashes=hashes)
            # Without a manifest the built-in pins apply, and the fixture's files don't match them.
            with self.assertRaisesRegex(unlock.UnlockError, "hash mismatch"):
                unlock.unlock_crown(self.identity(), amonet, confirmation=unlock.CONFIRM_PHRASE, run=run)
            run.assert_not_called()

    def test_locked_crown_requires_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            run = mock.Mock()
            with self.assertRaisesRegex(unlock.UnlockError, "exact confirmation"):
                unlock.unlock_crown(
                    self.identity(), amonet,
                    confirmation="yes", run=run, expected_hashes=hashes,
                )
            run.assert_not_called()

    def test_success_waits_for_twrp_without_touching_fastboot(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            run = fastbrick()
            adb = FakeTwrp(absent_polls=40)
            said = []
            result = self.unlock(amonet, hashes, run=run, adb_run=adb, said=said)
            self.assertTrue(result.amonet_invoked)
            self.assertEqual(result.identity, self.identity(unlocked=True))
            args, kwargs = run.call_args
            self.assertEqual(args[0], amonet / "amonet")
            self.assertEqual(kwargs["timeout"], unlock.FASTBRICK_TIMEOUT_SECONDS)
            self.assertTrue(all(argv[0] == "adb" for argv in adb.calls))
            self.assertEqual(adb.calls[:40], [["adb", "devices"]] * 40)
            self.assertTrue(any("do not unplug" in line for line in said))

    def test_twrp_wait_outlasts_the_five_minute_write(self):
        self.assertGreaterEqual(unlock.VERIFY_ATTEMPTS * unlock.VERIFY_INTERVAL_SECONDS, 600)

    def test_serial_change_after_exploit_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            with self.assertRaisesRegex(unlock.UnlockError, "not fastboot serial ABC123"):
                self.unlock(amonet, hashes, adb_run=FakeTwrp(serial="OTHER"))

    def test_twrp_of_another_board_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            with self.assertRaisesRegex(unlock.UnlockError, "ro.product.device=checkers.*do not unplug"):
                self.unlock(
                    amonet, hashes, adb_run=FakeTwrp(device="checkers"),
                    identify=lambda: (_ for _ in ()).throw(DeviceGateError("no fastboot device")),
                )

    def test_no_twrp_but_unlocked_fastboot_at_the_end_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            identify = mock.Mock(return_value=self.identity(unlocked=True))
            result = self.unlock(amonet, hashes, adb_run=FakeTwrp(absent_polls=10**6), identify=identify)
            self.assertTrue(result.identity.unlocked)
            identify.assert_called_once_with()

    def test_no_twrp_and_still_locked_says_keep_it_powered(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            with self.assertRaisesRegex(unlock.UnlockError, "unlock_status=false.*do not unplug"):
                self.unlock(
                    amonet, hashes, adb_run=FakeTwrp(absent_polls=10**6),
                    identify=lambda: self.identity(unlocked=False),
                )

    def test_fastboot_serial_change_at_the_end_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            with self.assertRaisesRegex(unlock.UnlockError, "serial changed"):
                self.unlock(
                    amonet, hashes, adb_run=FakeTwrp(absent_polls=10**6),
                    identify=lambda: self.identity(unlocked=True, serial="OTHER"),
                )

    def test_clean_exit_without_the_success_line_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            adb = mock.Mock()
            with self.assertRaisesRegex(unlock.UnlockError, "without landing the exploit"):
                self.unlock(amonet, hashes, run=fastbrick("Aborting.\n"), adb_run=adb)
            adb.assert_not_called()

    def test_amonet_failure_requires_reidentify_before_retry(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            with self.assertRaisesRegex(unlock.UnlockError, "re-identified"):
                self.unlock(amonet, hashes, run=fastbrick("boom\n", code=3), adb_run=mock.Mock())

    def test_worn_out_emmc_is_told_apart(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            out = "eMMC is in permanent read-only mode!\n... eMMC-RO boots=12 ...\n"
            with self.assertRaisesRegex(unlock.UnlockError, "worn out.*safe to reboot"):
                self.unlock(amonet, hashes, run=fastbrick(out, code=1), adb_run=mock.Mock())

    def test_device_mismatch_is_told_apart(self):
        with tempfile.TemporaryDirectory() as td:
            amonet, hashes = self.bundle(td)
            out = "Device mismatch detected!\n"
            with self.assertRaisesRegex(unlock.UnlockError, "refused this device.*Re-identify"):
                self.unlock(amonet, hashes, run=fastbrick(out, code=1), adb_run=mock.Mock())

    def test_warnings_cover_power_twrp_and_permanent_brick(self):
        cronos = " ".join(unlock.unlock_warnings(profile_for_board("cronos")))
        for words in ("mains power", "USB", "five minutes", "TWRP", "Don't unplug", "permanent"):
            self.assertIn(words, cronos)
        crown = " ".join(unlock.unlock_warnings(profile_for_board("crown")))
        self.assertNotIn("permanent", crown)
        self.assertIn("may not be recoverable", crown)


class FastbrickScriptTests(unittest.TestCase):
    def script(self, td, body):
        root = pathlib.Path(td)
        (root / "fastbrick.sh").write_text(body)
        return root

    def test_output_is_shown_live_and_answers_are_fed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.script(td, (
                'printf "\\033[H\\033[2J"\n'
                'read yes; echo "got $yes"\n'
                'echo -e "\\033[32mExploit most likely successful!\\033[0m"\n'
                'read -n 1 -s key; echo "key $key"\n'
            ))
            lines = []
            result = unlock.run_fastbrick_script(root, lines.append, timeout=20)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(lines, ["got YES", unlock.EXPLOIT_LANDED, "key x"])
            self.assertNotIn("\x1b", result.output)

    def test_timeout_kills_the_whole_group_and_says_keep_it_powered(self):
        with tempfile.TemporaryDirectory() as td:
            pidfile = pathlib.Path(td) / "child.pid"
            root = self.script(td, f'sleep 60 & echo $! > {pidfile}\necho started\nwait\n')
            started = time.monotonic()
            with self.assertRaisesRegex(unlock.UnlockError, "may still have landed.*do not unplug"):
                unlock.run_fastbrick_script(root, lambda _line: None, timeout=1)
            self.assertLess(time.monotonic() - started, 15)
            child = int(pidfile.read_text())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(child, 9)
                self.fail("fastboot child survived the timeout")

    def test_ctrl_c_stops_it_instead_of_leaving_it_orphaned(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.script(td, 'echo "Exploit most likely successful!"\nsleep 60\n')

            def interrupt(_line):
                raise KeyboardInterrupt

            with self.assertRaisesRegex(unlock.UnlockError, "interrupted.*already landed.*do not unplug"):
                unlock.run_fastbrick_script(root, interrupt, timeout=20)



class HashManifestTests(unittest.TestCase):
    """jarvis-show.py --amonet-hashes is checked the same way before anything runs (GENBU-172)."""

    def load(self, payload, board="checkers"):
        import importlib.util
        import json
        spec = importlib.util.spec_from_file_location("jarvis_show_cli", TOOLS / "jarvis-show.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "hashes.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return cli.load_hash_manifest(path, board)

    def test_only_the_six_unlock_files_for_a_board_without_pins(self):
        six = {name: "a" * 64 for name in unlock.UNLOCK_FILES}
        self.assertEqual(self.load(six), six)
        with self.assertRaisesRegex(ValueError, "must name exactly"):
            self.load({"fastbrick.sh": "a" * 64})
        with self.assertRaisesRegex(ValueError, "built in"):
            self.load(six, board="crown")


if __name__ == "__main__":
    unittest.main()
