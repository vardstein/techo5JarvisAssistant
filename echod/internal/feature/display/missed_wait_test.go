package display

import (
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/firmware"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// The screen names what a following Show's update waits for (GENBU-247).
func TestWaitNoteNamesTheHold(t *testing.T) {
	// The language is cached for a second; whatever this read is not left for the next test's frames.
	t.Cleanup(i18n.Changed)
	at := time.Date(2026, 10, 19, 18, 12, 0, 0, time.Local)
	if got := waitNote(firmware.Hold{Kind: firmware.HoldTimer, Name: "Pasta", At: at}); !strings.Contains(got, "Pasta") || !strings.Contains(got, clockText(at)) {
		t.Errorf("timer hold reads %q", got)
	}
	if got := waitNote(firmware.Hold{Kind: firmware.HoldAlarm, At: at}); !strings.Contains(got, clockText(at)) {
		t.Errorf("alarm hold reads %q", got)
	}
	if got := waitNote(firmware.Hold{}); got != "" {
		t.Errorf("no hold reads %q", got)
	}
	if got := waitNote(firmware.Hold{Kind: firmware.HoldInstall}); got != "" {
		t.Errorf("an install reads %q; the install line says that", got)
	}
}
