package mute

import (
	"path/filepath"
	"testing"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/mic"
)

// oneState is a device with its latch at muted and nothing else holding the microphones.
func oneState(t *testing.T, muted bool) (*Mute, *fakeLine) {
	t.Helper()
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	mic.Get().SetTransitionMuted(false)
	line := &fakeLine{muted: muted}
	m := &Mute{sw: &esphome.Switch{}, status: &esphome.TextSensor{}, line: line}
	m.physical.Store(muted)
	m.sw.Set(muted)
	return m, line
}

// GENBU-166: there is one mute, the latch. Home Assistant and the screen can engage it, and only the
// button lets it go; an unmute from anywhere else leaves it on and says to press the button.
func TestOnlyTheButtonUnmutes(t *testing.T) {
	for _, tc := range []struct {
		name string
		try  func(*Mute)
	}{
		{"Home Assistant", func(m *Mute) { m.Set(false) }},
		{"the screen", func(m *Mute) { m.ScreenToggle() }},
	} {
		m, line := oneState(t, true)
		tc.try(m)
		if len(line.sets) != 0 || !line.muted {
			t.Errorf("%s: the latch was moved (%v), it must stay engaged", tc.name, line.sets)
		}
		if !m.sw.Get() {
			t.Errorf("%s: the switch says unmuted while the latch is engaged", tc.name)
		}
		if got := m.status.Get(); got != "press the mute button to unmute" {
			t.Errorf("%s: status %q, want the refusal", tc.name, got)
		}
	}

	m, line := oneState(t, true)
	m.Toggle()
	if line.muted || m.sw.Get() || m.status.Get() != "unmuted" {
		t.Errorf("the button left it muted=%v switch=%v status=%q", line.muted, m.sw.Get(), m.status.Get())
	}
}

func TestHomeAssistantAndTheScreenMute(t *testing.T) {
	for _, tc := range []struct {
		name string
		try  func(*Mute)
	}{
		{"Home Assistant", func(m *Mute) { m.Set(true) }},
		{"the screen", func(m *Mute) { m.ScreenToggle() }},
	} {
		m, line := oneState(t, false)
		tc.try(m)
		if !line.muted || !m.sw.Get() || m.status.Get() != "muted" {
			t.Errorf("%s: latch=%v switch=%v status=%q, want all muted", tc.name, line.muted, m.sw.Get(), m.status.Get())
		}
		if mic.Get().TransitionMuted() {
			t.Errorf("%s: frames still held after the latch took over", tc.name)
		}
		if !config.Get().Microphone.Muted {
			t.Errorf("%s: the mute was not saved", tc.name)
		}
	}
}

// Home Assistant replays the switch after reconnecting. A false with nothing muted is that, not an
// unmute, and must not show a refusal.
func TestAReplayedOffIsNoRefusal(t *testing.T) {
	m, line := oneState(t, false)
	m.Set(false)
	if len(line.sets) != 0 || m.status.Get() != "unmuted" {
		t.Errorf("sets %v, status %q", line.sets, m.status.Get())
	}
}

func TestMuteStatus(t *testing.T) {
	for _, tc := range []struct {
		available, muted, refused bool
		want                      string
	}{
		{false, false, false, "mute unavailable"},
		{false, true, true, "mute unavailable"},
		{true, false, false, "unmuted"},
		{true, false, true, "unmuted"},
		{true, true, false, "muted"},
		{true, true, true, "press the mute button to unmute"},
	} {
		if got := statusLabel(tc.available, tc.muted, tc.refused); got != tc.want {
			t.Errorf("statusLabel(%v, %v, %v) = %q, want %q", tc.available, tc.muted, tc.refused, got, tc.want)
		}
	}
}

// A device saved with the old software mute comes back muted either way: in the latch when it took
// it, or with the frames held until the button is pressed when it did not.
func TestOldSoftwareMuteStaysMuted(t *testing.T) {
	m, _ := oneState(t, true)
	mic.Get().SetTransitionMuted(true)
	m.migrate()
	if mic.Get().TransitionMuted() || !config.Get().Microphone.Muted || !m.effective() {
		t.Errorf("latch took it: held=%v saved=%v muted=%v", mic.Get().TransitionMuted(), config.Get().Microphone.Muted, m.effective())
	}

	m, line := oneState(t, false)
	mic.Get().SetTransitionMuted(true)
	m.migrate()
	if !m.effective() {
		t.Fatal("latch did not take it, and the microphones came back live by themselves")
	}
	m.Set(false)
	if !m.effective() {
		t.Fatal("Home Assistant released an old software mute")
	}
	m.Toggle()
	if m.effective() || line.muted || len(line.sets) != 0 {
		t.Errorf("the button: muted=%v latch=%v sets=%v, want live and the latch untouched", m.effective(), line.muted, line.sets)
	}
}
