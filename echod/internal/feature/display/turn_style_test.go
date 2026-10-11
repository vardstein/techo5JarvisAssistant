//go:build !dot

package display

import (
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// A Show nobody has set a turn screen on draws Glow, and a Show set to Classic keeps it, across a
// restart as well: Classic used to be kept as nothing, so it has a word of its own now.
func TestGlowIsTheTurnScreenUntilSomebodyChooses(t *testing.T) {
	state := filepath.Join(t.TempDir(), "state.json")
	config.Use(state)
	if got := turnStyles[turnStyleIndex()].label; got != "Glow" || !equalizerOn() || !glowOn() {
		t.Fatalf("a fresh Show draws %q (equalizer %v, glow %v), want Glow", got, equalizerOn(), glowOn())
	}
	for i, want := range []string{"Classic", "Wave", "Bars", "Glow"} {
		for j, t2 := range turnStyles {
			if t2.label == want {
				setTurnStyle(nil, j)
			}
		}
		config.Use(state)
		if got := turnStyles[turnStyleIndex()].label; got != want {
			t.Errorf("step %d: chose %s, read back %s after a restart", i, want, got)
		}
	}
	setTurnStyle(nil, 0)
	if equalizerOn() || glowOn() || waveOn() {
		t.Error("Classic still draws a picture")
	}
}
