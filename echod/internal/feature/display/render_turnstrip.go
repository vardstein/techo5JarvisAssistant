//go:build !dot && !spot

package display

import "image"

// A voice turn under a page that is drawn over it.
//
// The turn's own picture ("Listening…") is a page like the clock, and the settings sheet, a camera,
// an alert, the calendar, the rain map and the forecast are all drawn in its place. With one of them
// up the microphone opened with nothing on the screen to say so, and on a Show without a light ring
// nothing anywhere: the device asked whether to install an update with the Updates page open, waited
// fifteen seconds for an answer nobody knew it was waiting for, and gave up. So the turn is said in
// the strip along the bottom, the announcement's place, until it has been heard.

// turnCovered is whether a turn is listening or thinking under a page that hides its picture.
func turnCovered(s scene) bool {
	switch s.phase {
	case "listening", "thinking":
	default:
		return false
	}
	return s.showSheet || s.showCamera || s.showAlert || s.showCalendar || s.showRadar || s.showWeather
}

// turnStrip is the turn, said over the page: listening, or thinking about what it heard.
func (r *renderer) turnStrip(s scene) {
	top := r.h - announceBar - announceInset
	box := image.Rect(announceInset, top, r.w-announceInset, r.h-announceInset)
	r.setTurnStripAt(box)
	// A flat box a step lighter than the page; the word in the accent is what says a turn is on.
	r.overlayBox(box)

	// One line, the word in the big face and what goes with it after: two stacked lines crowded each
	// other in the strip's hundred and thirty pixels.
	in := r.rowIn()
	head, rest, tone := "Listening…", "Go ahead", dim
	if s.phase == "thinking" {
		head, rest, tone = "Thinking…", s.heard, cream
	}
	y := box.Min.Y + 84
	r.text(r.title, head, box.Min.X+in, y, amber)
	if rest == "" {
		return
	}
	x := box.Min.X + in + r.width(r.title, head) + 28
	r.text(r.body, clipText(r, r.body, rest, box.Max.X-in-x), x, y, tone)
}

func (r *renderer) setTurnStripAt(b image.Rectangle) {
	r.weatherMu.Lock()
	r.turnStripAt = b
	r.weatherMu.Unlock()
}

// turnStripTapped is whether a tap at p landed on the strip as last drawn. The strip takes the tap,
// so that a row of the page under it is not pressed blind.
func (r *renderer) turnStripTapped(p image.Point) bool {
	r.weatherMu.Lock()
	defer r.weatherMu.Unlock()
	return !r.turnStripAt.Empty() && p.In(r.turnStripAt)
}
