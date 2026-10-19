//go:build !dot && !spot

package display

import (
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"image"
	"image/draw"
	"math"
	"strings"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/metrics"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

// The Genbu Show mark, drawn while the device comes up: the Genbu mark (splash_mark.go) over the
// wordmark, both in the active screen palette rather than an upstream TECHO5 bitmap, so a fresh White
// Jade device has one visual identity from the first daemon-rendered frame onward. Under the mark a bar fills through the
// steps of coming up, the same way JODS shows its setup, with one line saying which step it is on.

const (
	// The bar: barWidth by barHeight in the Show 5's pixels, its fill moving to a new step over
	// barEase, and the current step's sweep crossing it once every sweepPeriod.
	barWidth    = 420
	barHeight   = 10
	barEase     = 300 * time.Millisecond
	sweepPeriod = 1600 * time.Millisecond

	// waitingAfter is when the splash starts saying what it is waiting for: long enough that a
	// device which is already adopted is up and gone before it shows, short enough that nobody
	// sits watching a screen that looks stuck.
	waitingAfter = 12 * time.Second

	// splashMin is the least the splash is shown, so a fast connection still shows the mark.
	splashMin = 4 * time.Second

	// noAddressWait is how long after the start a device with no network address waits before the Wi-Fi
	// page opens by itself, ending the splash: long enough for a lease on a slow network, short enough
	// that a fresh unit, or one in a house it has no network for, is not left on the splash.
	noAddressWait = 45 * time.Second
)

// bootStep is how far coming up has got: each one is a third of the bar.
type bootStep int

const (
	stepStarting      bootStep = iota // the daemon is drawing; nothing else is known yet
	stepNetwork                       // waiting for a network address
	stepHomeAssistant                 // on the network, waiting for Home Assistant to listen
	stepReady                         // Home Assistant is listening; the splash ends at splashMin
)

const bootSteps = 3

func (b bootStep) label() string {
	switch b {
	case stepStarting:
		return "Starting up"
	case stepNetwork:
		return "Joining Wi-Fi"
	case stepHomeAssistant:
		return "Connecting to Home Assistant"
	}
	return "Ready"
}

// splash is the wordmark's geometry, and the bar's fill as last drawn so a new step slides in.
type splash struct {
	cx, cy       float64
	titleY, subY int
	shown        float64
	at           time.Duration
	offer        setupOffer
}

// newSplash places the wordmark on r's screen. Its offsets are in the Show 5's pixels like every
// other size here, so on the Show 8 the two lines keep their room as the faces grow.
func newSplash(r *renderer) *splash {
	// Below the middle, leaving the top of the panel to the Genbu mark.
	cy := float64(r.h)/2 + float64(r.s(18))
	return &splash{
		cx:     float64(r.w) / 2,
		cy:     cy,
		titleY: int(cy) - r.s(30),
		subY:   int(cy) + r.s(14),
	}
}

// wordmark is the product name as the splash draws it: "Genbu Show" is GENBU over a smaller SHOW.
func wordmark() (mark, sub string) {
	mark, sub, _ = strings.Cut(strings.ToUpper(layout.System), " ")
	return mark, sub
}

// drawSplash paints the splash elapsed into coming up, at the given step.
func (r *renderer) drawSplash(s *splash, elapsed time.Duration, step bootStep) {
	mark, sub := wordmark()
	if s == nil {
		draw.Draw(r.dst, r.dst.Rect, image.NewUniform(walnut), image.Point{}, draw.Src)
		r.text(r.title, mark, (r.w-r.width(r.title, mark))/2, r.h/2, cream)
		return
	}
	seated := s.markRect(r).Min.Y >= 0
	draw.Draw(r.dst, r.dst.Rect, r.splashScene(s, seated), image.Point{}, draw.Src)
	if seated {
		r.drawMark(s)
	}
	r.text(r.title, mark, (r.w-r.width(r.title, mark))/2, s.titleY, cream)
	r.text(r.small, sub, (r.w-r.width(r.small, sub))/2, s.subY, amber)

	// The fill eases toward the steps done, so the bar never jumps.
	target := float64(min(step, stepReady)) / bootSteps
	if dt := (elapsed - s.at).Seconds(); dt > 0 {
		s.shown += (target - s.shown) * math.Min(dt/barEase.Seconds(), 1)
	}
	s.at = elapsed
	bar := s.barRect(r)
	r.progressBar(bar, s.shown, step < stepReady, elapsed)

	// One line under the bar for the step, a second once waiting is the explanation.
	y := bar.Max.Y + r.s(44)
	line := step.label()
	offer := step == stepHomeAssistant && s.offer.shown
	if offer {
		line = "Ready to set up"
	}
	if step < stepReady && !offer {
		line += "…"
		count := i18n.Sprintf("  %d of %d", int(step)+1, bootSteps)
		w := r.width(r.tiny, line) + r.width(r.tiny, count)
		x := (r.w - w) / 2
		r.text(r.tiny, line, x, y, cream)
		r.text(r.tiny, count, x+r.width(r.tiny, line), y, dim)
	} else {
		r.text(r.tiny, line, (r.w-r.width(r.tiny, line))/2, y, cream)
	}
	if offer {
		r.drawSetupOffer(s, y)
		return
	}
	if elapsed >= waitingAfter {
		if hint := step.hint(); hint != "" {
			r.message(r.tiny, hint, r.margin, y+r.s(44), dim)
		}
	}
}

func (s *splash) barRect(r *renderer) image.Rectangle {
	w := min(r.s(barWidth), r.w-2*r.margin)
	h := max(r.s(barHeight), 4)
	x := (r.w - w) / 2
	y := s.subY + r.s(46)
	return image.Rect(x, y, x+w, y+h)
}

// hint is what the step is waiting on, said once it has gone on long enough to need saying.
//
// Home Assistant does not listen until somebody accepts the device there. Between flashing a unit and
// adopting it that can be minutes, and a screen that says nothing reads as one that has hung on its
// first boot: somebody sat in front of one for ten minutes before finding out that accepting the
// ESPHome prompt was what freed it (techo5-checkers issue #2). A device that is adopted already is
// past this step in a couple of seconds, so it never sees the hint.
func (b bootStep) hint() string {
	switch b {
	case stepNetwork:
		return "No network yet. The Wi-Fi page opens by itself if this goes on."
	case stepHomeAssistant:
		return "Add it in Home Assistant: Settings > Devices & services > ESPHome"
	}
	return ""
}

// progressBar is the JODS bar: a rounded track, filled to done (0 to 1) in the accent. While busy,
// a soft sweep runs across the part not yet done, so a long step still looks alive.
func (r *renderer) progressBar(b image.Rectangle, done float64, busy bool, elapsed time.Duration) {
	rad := float64(b.Dy()) / 2
	track := lerp(walnut, dim, 0.35)
	r.roundFill(b, rad, track, track)
	fill := b.Min.X + int(math.Round(clamp01(done)*float64(b.Dx())))
	if busy {
		// The sweep is a third of the remaining track wide and fades in and out at its ends.
		rest := b.Max.X - fill
		if rest > b.Dy() {
			phase := math.Mod(elapsed.Seconds()/sweepPeriod.Seconds(), 1)
			w := max(rest/3, b.Dy())
			x := fill - w + int(phase*float64(rest+w))
			glow := lerp(track, amber, 0.45*math.Sin(phase*math.Pi))
			seg := image.Rect(max(x, fill), b.Min.Y, min(x+w, b.Max.X), b.Max.Y)
			if seg.Dx() > 0 {
				r.roundFill(seg, rad, glow, glow)
			}
		}
	}
	if fill-b.Min.X >= b.Dy() {
		r.roundFill(image.Rect(b.Min.X, b.Min.Y, fill, b.Max.Y), rad, lerp(amber, cream, 0.15), amber)
	}
}

// bootStep is which step of coming up the splash shows. The address is looked at once a second, not
// every frame. Only the frame loop calls it, so online needs no lock.
func (d *Display) bootStep(now, started time.Time) bootStep {
	if voice.Get().Ready() {
		return stepReady
	}
	if now.Sub(d.onlineAt) >= time.Second {
		d.online, d.onlineAt = len(metrics.Addresses()) > 0, now
	}
	switch {
	case d.online:
		return stepHomeAssistant
	case now.Sub(started) < splashMin/2:
		return stepStarting
	}
	return stepNetwork
}
