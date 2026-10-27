import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ota_bridge  # noqa: E402

PINS = ["v1.4.0-dev.40", "v1.4.0-staging.41", "v1.4.0-stable.42", "v1.5.0-dev.60"]


class OtaBridgeTest(unittest.TestCase):
    def test_each_channel_copies_the_newest_bridge_it_may_carry(self):
        self.assertEqual(ota_bridge.bridge(PINS, "dev", "v1.6.0-dev.70"), "v1.5.0-dev.60")
        self.assertEqual(ota_bridge.bridge(PINS, "staging", "v1.6.0-staging.70"), "v1.4.0-stable.42")
        self.assertEqual(ota_bridge.bridge(PINS, "stable", "v1.6.0-stable.70"), "v1.4.0-stable.42")

    def test_a_stable_release_never_points_at_a_dev_or_staging_bridge(self):
        with self.assertRaises(SystemExit) as no:
            ota_bridge.bridge(["v1.4.0-dev.40", "v1.4.0-staging.41"], "stable", "v1.6.0-stable.70")
        self.assertIn("GENBU-191", str(no.exception))

    def test_only_bridges_the_release_ranks_above(self):
        self.assertEqual(ota_bridge.bridge(PINS, "dev", "v1.5.0-dev.50"), "v1.4.0-stable.42")
        with self.assertRaises(SystemExit):
            ota_bridge.bridge(PINS, "dev", "v1.4.0-dev.40")

    def test_no_pin_no_release(self):
        with self.assertRaises(SystemExit):
            ota_bridge.bridge([], "dev", "v1.6.0-dev.70")

    def test_the_pin_file_reads_tags_and_skips_comments(self):
        self.assertEqual(ota_bridge.pinned("# note\n\nv1.4.0-dev.40  # first\n"), ["v1.4.0-dev.40"])
        with self.assertRaises(SystemExit):
            ota_bridge.pinned("channel-dev\n")
        ota_bridge.pinned(ota_bridge.PINS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
