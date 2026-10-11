//go:build !dot && !spot

package display

import (
	"image"
	"testing"
	"time"
)

// The device asked about an update with the Updates page open, and listened for fifteen seconds with
// nothing on the screen to say so. A turn under a page that hides its picture is said in the strip
// along the bottom, and the strip takes the tap so the row under it is not pressed blind.
func TestATurnUnderTheSettingsSheetIsShown(t *testing.T) {
	at := time.Date(2026, 9, 16, 14, 7, 0, 0, time.Local)
	sheet := scene{now: at, phase: "idle", showSheet: true,
		sheet: settings{cat: catSound, volume: 15, wakeWord: "Okay Nabu"}}

	for _, panel := range showPanels() {
		r := newRenderer(image.NewRGBA(image.Rect(0, 0, panel.wide, panel.high)))
		strip := image.Pt(r.w/2, r.h-announceInset-announceBar/2)

		r.draw(sheet)
		if r.turnStripTapped(strip) {
			t.Errorf("%s: a strip was found over the sheet with no turn going on", panel.name)
		}

		for _, phase := range []string{"listening", "thinking"} {
			s := sheet
			s.phase, s.heard = phase, "ja bitte"
			r.draw(s)
			if !r.turnStripTapped(strip) {
				t.Errorf("%s: %s under the sheet has no strip", panel.name, phase)
			}
			if r.turnStripTapped(image.Pt(r.w/2, r.h/3)) {
				t.Errorf("%s: %s: a tap on the sheet above the strip was taken by it", panel.name, phase)
			}
		}

		// The answer is being said: the turn has been heard and the page it went to is the one up.
		s := sheet
		s.phase = "replying"
		r.draw(s)
		if r.turnStripTapped(strip) {
			t.Errorf("%s: the strip stayed up while the answer is said", panel.name)
		}

		// The turn's own page is up: nothing to say twice.
		r.draw(scene{now: at, phase: "listening"})
		if r.turnStripTapped(strip) {
			t.Errorf("%s: a strip was drawn over the turn's own page", panel.name)
		}
	}
}
