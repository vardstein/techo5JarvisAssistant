import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
MODULE = ROOT / "tools" / "jarvis_crown" / "device_gate.py"
spec = importlib.util.spec_from_file_location("jarvis_crown_device_gate", MODULE)
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


def completed(argv, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


class DeviceGateTests(unittest.TestCase):
    def runner(self, *, product="CROWN", unlock="false", lk="lk-crown", serials=("ABC123",)):
        def run(argv, **kwargs):
            if argv == ["fastboot", "devices"]:
                body = "".join(f"{serial}\tfastboot\n" for serial in serials)
                return completed(argv, stdout=body)
            serial = argv[2]
            self.assertIn(serial, serials)
            name = argv[-1]
            values = {
                "product": product,
                "unlock_status": unlock,
                "lk_build_desc": lk,
                "serialno": serial,
            }
            return completed(argv, stderr=f"{name}: {values[name]}\nFinished. Total time: 0.001s\n")
        return run

    def test_locked_crown_passes_read_only_gate(self):
        identity = gate.identify_crown(run=self.runner())
        self.assertEqual(identity.product, "CROWN")
        self.assertFalse(identity.unlocked)
        self.assertEqual(identity.serial, "ABC123")

    def test_already_unlocked_crown_passes(self):
        identity = gate.identify_crown(run=self.runner(unlock="TRUE"))
        self.assertTrue(identity.unlocked)

    def test_wrong_product_fails_closed(self):
        with self.assertRaisesRegex(gate.DeviceGateError, "expected crown"):
            gate.identify_crown(run=self.runner(product="CHECKERS"))


    def test_cronos_second_gen_is_identified(self):
        identity = gate.identify_show(run=self.runner(product="CRONOS", lk="lk-cronos"))
        self.assertEqual(identity.product, "CRONOS")
        self.assertFalse(identity.unlocked)
        with self.assertRaisesRegex(gate.DeviceGateError, "expected crown"):
            gate.identify_crown(run=self.runner(product="CRONOS"))

    def test_unknown_product_warns_newer_generation_and_fails_closed(self):
        with self.assertRaisesRegex(gate.DeviceGateError, "newer generation.*do not flash.*No write was attempted"):
            gate.identify_show(run=self.runner(product="ROOKS"))

    def test_no_device_fails_closed(self):
        with self.assertRaisesRegex(gate.DeviceGateError, "no fastboot device"):
            gate.identify_crown(run=self.runner(serials=()))

    def test_multiple_devices_fail_closed(self):
        with self.assertRaisesRegex(gate.DeviceGateError, "exactly one"):
            gate.identify_crown(run=self.runner(serials=("A", "B")))

    def test_unknown_unlock_status_fails_closed(self):
        with self.assertRaisesRegex(gate.DeviceGateError, "unrecognized unlock_status"):
            gate.identify_crown(run=self.runner(unlock="unknown"))

    def test_answers_out_of_step_fail_closed(self):
        # What a crown did after an interrupted run: each answer belonged to the question before.
        asked, answers = [], {"product": "CROWN", "unlock_status": "true", "lk_build_desc": "lk", "serialno": "ABC123"}

        def run(argv, **kwargs):
            if argv == ["fastboot", "devices"]:
                return completed(argv, stdout="ABC123\tfastboot\n")
            name = argv[-1]
            said = answers[asked[-1]] if asked else ""
            asked.append(name)
            return completed(argv, stderr=f"{name}: {said}\nFinished. Total time: 0.001s\n")
        with self.assertRaisesRegex(gate.DeviceGateError, "out of step.*nothing was written.*reboot-bootloader"):
            gate.identify_crown(run=run)
        self.assertEqual(asked, ["serialno"], "nothing else is asked once it is out of step")

    def test_falling_out_of_step_halfway_fails_closed(self):
        base, seen = self.runner(), []

        def run(argv, **kwargs):
            if argv[-1:] == ["serialno"]:
                seen.append(1)
                if len(seen) == 2:
                    return completed(argv, stderr="serialno: lk-crown\n")
            return base(argv, **kwargs)
        with self.assertRaisesRegex(gate.DeviceGateError, "out of step"):
            gate.identify_crown(run=run)

    def test_no_serial_number_answer_fails_closed(self):
        base = self.runner()

        def run(argv, **kwargs):
            if argv[-1:] == ["serialno"]:
                return completed(argv, stderr="FAILED (remote: unknown variable)\n", returncode=1)
            return base(argv, **kwargs)
        with self.assertRaisesRegex(gate.DeviceGateError, "out of step.*unknown variable"):
            gate.identify_crown(run=run)

    def test_missing_lk_build_description_is_nonfatal(self):
        base = self.runner()
        def run(argv, **kwargs):
            if argv[-1:] == ["lk_build_desc"]:
                return completed(argv, stderr="FAILED (remote: unknown variable)\n", returncode=1)
            return base(argv, **kwargs)
        identity = gate.identify_crown(run=run)
        self.assertIsNone(identity.lk_build_desc)

    def test_timeout_fails_closed(self):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 8))
        with self.assertRaisesRegex(gate.DeviceGateError, "timed out"):
            gate.identify_crown(run=run)

    def test_only_read_only_fastboot_commands_are_emitted(self):
        seen = []
        base = self.runner()
        def run(argv, **kwargs):
            seen.append(tuple(argv))
            return base(argv, **kwargs)
        gate.identify_crown(run=run)
        self.assertTrue(seen)
        for argv in seen:
            self.assertFalse(any(word in argv for word in ("flash", "erase", "boot", "reboot", "oem", "flashing")))
        self.assertEqual(seen[0], ("fastboot", "devices"))


if __name__ == "__main__":
    unittest.main()
