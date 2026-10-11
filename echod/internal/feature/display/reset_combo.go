//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"image/draw"
	"log/slog"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/buttons"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// The buttons' factory reset: both volume buttons held together for resetHold erase the Show, the way
// the settings' Factory reset does. It is there for a Show whose screen cannot be used - a cracked
// panel, a touch layer that died, a setup that put the screen out - so it works on every page, over
// the splash and the welcome card, and lights a dark panel. The screen counts down while they are
// held; letting go of either one stops it. It is checked while the daemon runs rather than at power
// on, where the bootloader reads the volume buttons itself and nothing could show the countdown.

// resetHold is how long both buttons have to stay down, and comboTick how often they are looked at.
// Variables so a test need not wait ten seconds.
var (
	resetHold = 10 * time.Second
	comboTick = 100 * time.Millisecond
)

// comboHeld reports whether both volume buttons are down. A variable so a test can hold them.
var comboHeld = func() bool {
	b := buttons.Get()
	return b.Down(buttons.VolumeUp) && b.Down(buttons.VolumeDown)
}

// resetCombo is the buttons listener. The second of the two buttons going down starts the count; one
// count runs at a time.
func (d *Display) resetCombo(e buttons.Event) {
	if e.Kind != buttons.Tap || (e.Name != buttons.VolumeUp && e.Name != buttons.VolumeDown) || !comboHeld() {
		return
	}
	d.mu.Lock()
	if !d.comboAt.IsZero() {
		d.mu.Unlock()
		return
	}
	d.comboAt = time.Now()
	d.mu.Unlock()
	slog.Warn("factory reset: both volume buttons held, counting down", "hold", resetHold)
	d.wake()
	go d.comboWatch()
}

// comboWatch follows the count until the buttons are let go or it runs out.
func (d *Display) comboWatch() {
	t := time.NewTicker(comboTick)
	defer t.Stop()
	for range t.C {
		d.mu.Lock()
		at := d.comboAt
		d.mu.Unlock()
		if !comboHeld() {
			d.mu.Lock()
			d.comboAt, d.comboUndo = time.Time{}, true
			d.mu.Unlock()
			slog.Info("factory reset: buttons let go, nothing erased", "after", time.Since(at).Round(100*time.Millisecond))
			d.wake()
			return
		}
		if time.Since(at) >= resetHold {
			slog.Warn("factory reset asked for with the buttons")
			factoryReset("buttons")
			return
		}
		d.wake()
	}
}

// comboFrame draws the count when one is running, lighting the panel for it, and puts the panel back
// out after one stopped if it was the count that lit it. It reports whether it drew.
func (d *Display) comboFrame(now time.Time, on bool) bool {
	d.mu.Lock()
	at, undo, lit := d.comboAt, d.comboUndo, d.comboLit
	d.comboUndo = false
	if undo {
		d.comboLit = false
	}
	d.mu.Unlock()
	if at.IsZero() {
		if undo && lit {
			d.apply(false, d.ceilingOrDefault(), false)
		}
		return false
	}
	if !on {
		d.mu.Lock()
		d.comboLit = true
		d.mu.Unlock()
		d.apply(true, d.ceilingOrDefault(), false)
	}
	d.r.resetCountdown(now.Sub(at))
	return true
}

// resetCountdown is the count's page: plain, in the warning colour, with how long is left.
func (r *renderer) resetCountdown(held time.Duration) {
	draw.Draw(r.dst, r.dst.Rect, image.NewUniform(walnut), image.Point{}, draw.Src)
	left := max(resetHold-held, 0)
	secs := int((left + time.Second - 1) / time.Second)
	title := i18n.F("Factory reset in {n} s", "n", fmt.Sprint(secs))
	y := r.h/2 - r.s(50)
	r.text(r.title, title, (r.w-r.width(r.title, title))/2, y, danger)
	for i, line := range []string{"Everything on this Show will be erased.", "Let go of a button to stop."} {
		r.text(r.small, line, (r.w-r.width(r.small, line))/2, y+r.s(68+i*42), cream)
	}
	bw := min(r.w-2*r.margin, r.s(520))
	top := y + r.s(150)
	r.progressBar(image.Rect((r.w-bw)/2, top, (r.w+bw)/2, top+r.s(14)), float64(held)/float64(resetHold), false, 0)
}
