import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENBU = ROOT / "ha" / "custom_components" / "genbu"
SPEC = importlib.util.spec_from_file_location("genbu_controls", GENBU / "controls.py")
controls = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controls  # dataclasses look their module up
SPEC.loader.exec_module(controls)

MAC = "AA:BB:CC:00:00:01"


def control(uid, category=None):
    return controls.control_for(f"{MAC}{uid}", category)


class ControlForTests(unittest.TestCase):
    """Which of a Show's ESPHome entities get a copy on its genbu device (GENBU-114), by unique ids as a
    live Show has them."""

    def test_everyday_controls_get_a_copy_and_their_original_is_hidden(self):
        for uid, platform in (("/0/light/Screen", "light"), ("/0/switch/Night mode", "switch"),
                              ("/3/select/White noise layer 1", "select"), ("/0/button/Snooze alarm", "button"),
                              ("/0/text_sensor/Next alarm", "sensor"), ("/2/switch/Microphone mute", "switch")):
            with self.subTest(uid):
                c = control(uid)
                self.assertEqual((c.platform, c.category, c.hide, c.special), (platform, None, True, ""))
                self.assertEqual(c.key, uid[1:])
                self.assertEqual(c.name, uid.rsplit("/", 1)[1])

    def test_settings_get_a_copy_but_stay_shown(self):
        c = control("/0/number/Microphone gain", "config")
        self.assertEqual((c.platform, c.category, c.hide), ("number", "config", False))
        self.assertFalse(control("/0/select/Clock style", "config").hide)

    def test_what_stays_on_the_esphome_device_only(self):
        for uid, category in (("/0/text_sensor/IP address", "diagnostic"), ("/0/button/Test playback", "diagnostic"),
                              ("/0/camera/Camera", None), ("/0/event/Ring", None),
                              ("/0/text_sensor/Something", "config"), ("-assist_satellite", None),
                              ("-wifi_signal", "diagnostic")):
            with self.subTest(uid):
                self.assertIsNone(control(uid, category))

    def test_the_speaker_becomes_volume_and_the_update_a_version(self):
        volume = control("/0/media_player/Speaker")
        self.assertEqual((volume.platform, volume.special, volume.category, volume.hide),
                         ("number", "volume", None, False))
        firmware = control("/0/update/Firmware")
        self.assertEqual((firmware.platform, firmware.special, firmware.category, firmware.hide),
                         ("sensor", "firmware", "diagnostic", False))
        self.assertIsNone(control("/0/media_player/Other"))

    def test_the_assist_settings_esphome_adds(self):
        for end, special in (("pipeline", "assistant"), ("pipeline_2", "assistant_2"), ("wake_word", "wake_word"),
                             ("vad_sensitivity", "finished_speaking")):
            with self.subTest(end):
                c = control(f"-{end}", "config")
                self.assertEqual((c.key, c.platform, c.special, c.category, c.hide),
                                 (end, "select", special, "config", False))

    def test_a_name_with_a_slash_stays_whole(self):
        self.assertEqual(control("/0/switch/On/off").name, "On/off")


class ObjectIdTests(unittest.TestCase):
    def test_object_ids_never_take_the_originals(self):
        self.assertEqual(controls.object_id("hallShow", control("/0/switch/Night mode")),
                         "genbu_hallshow_night_mode")
        self.assertEqual(controls.object_id("hall-show", control("/0/media_player/Speaker")),
                         "genbu_hall_show_volume")
        self.assertEqual(controls.object_id("hall", control("-pipeline_2", "config")), "genbu_hall_assistant_2")


if __name__ == "__main__":
    unittest.main()
