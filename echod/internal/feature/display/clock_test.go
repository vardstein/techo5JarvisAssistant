package display

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

func TestClockFormats(t *testing.T) {
	at := time.Date(2026, 9, 18, 15, 4, 0, 0, time.UTC)
	defer clock24.Store(false)
	for _, c := range []struct {
		h24                 bool
		hm, suffix, inALine string
	}{
		{false, "3:04", "PM", "3:04 PM"},
		{true, "15:04", "", "15:04"},
	} {
		clock24.Store(c.h24)
		if got := clockHM(at); got != c.hm {
			t.Errorf("24h=%v clockHM = %q, want %q", c.h24, got, c.hm)
		}
		if got := clockSuffix(at); got != c.suffix {
			t.Errorf("24h=%v clockSuffix = %q, want %q", c.h24, got, c.suffix)
		}
		if got := clockText(at); got != c.inALine {
			t.Errorf("24h=%v clockText = %q, want %q", c.h24, got, c.inALine)
		}
	}
}

// TestTheClockFollowsALanguageThatArrivesLater: a screen matching the assistant learns its language
// from Home Assistant after it is up; the next frames take the clock format along, and so does the
// setting in Home Assistant.
func TestTheClockFollowsALanguageThatArrivesLater(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	t.Cleanup(func() {
		// Later tests draw with whatever config is left: leave it English again.
		_ = config.Set().Screen().Language("")
		i18n.Changed()
		clock24.Store(false)
	})
	sel := clockSelect(func() {})
	setClock24(sel)
	if clock24.Load() || sel.Get() != clock12Label {
		t.Fatalf("English: 24h %v, setting %q", clock24.Load(), sel.Get())
	}
	if err := config.Set().Screen().Language("de"); err != nil {
		t.Fatal(err)
	}
	i18n.Changed()
	followClock(sel)
	if !clock24.Load() || sel.Get() != clock24Label {
		t.Fatalf("German: 24h %v, setting %q", clock24.Load(), sel.Get())
	}
	sel.OnCommand(clock12Label)
	followClock(sel)
	if clock24.Load() || sel.Get() != clock12Label {
		t.Fatalf("12-hour chosen: 24h %v, setting %q", clock24.Load(), sel.Get())
	}
}
