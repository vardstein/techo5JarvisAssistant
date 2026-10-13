"""The microphone mute contract. J46 had two states, a software mute Home Assistant could hold and the
privacy latch; GENBU-166 replaced that with one: the latch. Home Assistant and the screen engage it, only
the button on the device releases it, and the mute LED shows it."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class OneMuteStateContractTests(unittest.TestCase):
    def test_no_second_software_mute_and_no_remote_unmute_permission(self):
        cfg = text("echod/internal/config/microphone.go")
        setup = text("echod/internal/feature/setup/page.go")
        mute = text("echod/internal/feature/mute/mute.go")
        mic = text("echod/internal/hardware/mic/mic.go")

        self.assertNotIn("AllowRemoteUnmute", cfg)
        self.assertNotIn("remote-unmute", setup)
        self.assertNotIn("canRemoteUnmute", mute)
        self.assertNotIn("LocalToggle", mute)
        self.assertNotIn("SetSoftwareMuted", mic)
        # Kept only to read old saves, which become the latch (migrate).
        self.assertIn('SoftwareMuted bool `json:"software_muted,omitempty"`', cfg)
        self.assertIn("func (w MicrophoneWriter) ClearSoftwareMuted() error", cfg)

    def test_ha_and_screen_mute_but_never_unmute(self):
        mute = text("echod/internal/feature/mute/mute.go")
        set_body = re.search(r"func \(m \*Mute\) Set\(muted bool\) \{(.*?)\n\}", mute, re.S)
        self.assertIsNotNone(set_body)
        body = set_body.group(1)
        self.assertIn("if !muted {\n\t\tm.sw.Set(true)\n\t\tm.refresh(true)", body)
        self.assertIn("m.line.Set(true)", body)
        self.assertNotIn("m.line.Set(false)", body)
        self.assertNotIn("m.line.Set(muted)", body)
        self.assertIn("func (m *Mute) ScreenToggle() { m.Set(!m.effective()) }", mute)

        for path in ("echod/internal/feature/display/sheet.go", "echod/internal/feature/display/display_spot.go"):
            self.assertIn("go mute.Get().ScreenToggle()", text(path), path)

    def test_frames_stop_before_the_latch_settles(self):
        mute = text("echod/internal/feature/mute/mute.go")
        mic = text("echod/internal/hardware/mic/mic.go")
        privacy = text("echod/internal/hardware/privacy/platform_cronos.go")

        self.assertRegex(mic, r"if privacy\.SoftwareCut\(\) \|\| s\.transitionMuted\.Load\(\) \{\s*clear\(raw\)")
        self.assertIn("mic.Get().SetTransitionMuted(true)", mute)
        self.assertIn("func (m *Mute) effective() bool { return m.physical.Load() || mic.Get().TransitionMuted() }", mute)
        self.assertIn("ErrButtonOnly", privacy)
        self.assertIn("only the physical button releases them", privacy)

    def test_ha_status_says_how_to_unmute(self):
        mute = text("echod/internal/feature/mute/mute.go")
        tests = text("echod/internal/feature/mute/remote_test.go")

        self.assertIn('ObjectID: "microphone_mute_status"', mute)
        for state in ("muted", "unmuted", "mute unavailable", "press the mute button to unmute"):
            self.assertIn(f'"{state}"', mute)
            self.assertIn(f'"{state}"', tests)
        self.assertIn("func TestOnlyTheButtonUnmutes", tests)

    def test_screen_says_to_press_the_button(self):
        sheet = text("echod/internal/feature/display/sheet.go")
        self.assertIn('"Press the mute button to turn it back on"', sheet)
        for lang in ("de", "es", "fr", "it", "nl"):
            self.assertIn('"Press the mute button to turn it back on":', text(f"echod/internal/i18n/{lang}.go"), lang)

    def test_runtime_security_gate_includes_mute_package(self):
        gate = text("tools/security-regression.sh")
        self.assertIn("./internal/feature/mute", gate)


if __name__ == "__main__":
    unittest.main()
