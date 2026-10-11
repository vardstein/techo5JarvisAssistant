//go:build !dot && !spot

package display

import (
	"sync/atomic"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/buttons"
)

// holdCombo stands in for the two buttons and the reset, with a short count.
func holdCombo(t *testing.T) (held *atomic.Bool, resets *atomic.Int32, how *atomic.Value) {
	held, resets, how = &atomic.Bool{}, &atomic.Int32{}, &atomic.Value{}
	wasHeld, wasReset, wasHold, wasTick := comboHeld, factoryReset, resetHold, comboTick
	comboHeld = held.Load
	factoryReset = func(h string) { how.Store(h); resets.Add(1) }
	resetHold, comboTick = 150*time.Millisecond, 10*time.Millisecond
	t.Cleanup(func() { comboHeld, factoryReset, resetHold, comboTick = wasHeld, wasReset, wasHold, wasTick })
	return held, resets, how
}

func TestResetComboErasesAfterTheHold(t *testing.T) {
	held, resets, how := holdCombo(t)
	d := &Display{r: testRenderer()}

	// One button alone is a volume press, nothing more.
	d.resetCombo(buttons.Event{Name: buttons.VolumeUp, Kind: buttons.Tap})
	if !d.comboAt.IsZero() {
		t.Fatal("one volume button started the count")
	}

	held.Store(true)
	d.resetCombo(buttons.Event{Name: buttons.VolumeDown, Kind: buttons.Tap})
	d.resetCombo(buttons.Event{Name: buttons.VolumeDown, Kind: buttons.Repeat}) // no second count
	d.r.resetCountdown(50 * time.Millisecond)                                   // draws without a panic
	deadline := time.Now().Add(2 * time.Second)
	for resets.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if resets.Load() != 1 || how.Load() != "buttons" {
		t.Fatalf("resets = %d (how %v), want one from the buttons", resets.Load(), how.Load())
	}
}

func TestResetComboStopsWhenLetGo(t *testing.T) {
	held, resets, _ := holdCombo(t)
	d := &Display{r: testRenderer()}

	held.Store(true)
	d.resetCombo(buttons.Event{Name: buttons.VolumeUp, Kind: buttons.Tap})
	time.Sleep(40 * time.Millisecond)
	held.Store(false)
	time.Sleep(300 * time.Millisecond)
	if resets.Load() != 0 {
		t.Fatal("letting go did not stop the reset")
	}
	d.mu.Lock()
	at, undo := d.comboAt, d.comboUndo
	d.mu.Unlock()
	if !at.IsZero() || !undo {
		t.Errorf("after letting go: comboAt %v, comboUndo %v; want zero and true", at, undo)
	}
}
