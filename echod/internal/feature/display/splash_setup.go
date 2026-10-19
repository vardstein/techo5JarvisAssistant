//go:build !dot && !spot

package display

import (
	"image"
	"log/slog"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/api"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// A Show that no Home Assistant holds the key for — reset, left the one it had, or from somebody
// else's — never gets past "Connecting to Home Assistant": nothing can connect. It also keeps off mDNS
// (api/mdns.go), so Home Assistant does not find it either. The splash says so and offers the way on:
// a button that opens the window for a Home Assistant to add it, the same window the setup page's
// "Let a Home Assistant add this device" opens. A tap on the device is the proof somebody is standing
// at it, as Allow is for the setup page. Once the window is open the splash says until when, and where
// in Home Assistant the device shows up.

// setupOffer is what the splash offers a device waiting to be added: shown, and until when the window
// is open (zero while it is not).
type setupOffer struct {
	shown bool
	until time.Time
}

// openSetupWindow opens the window. A variable for the tests, which must not touch the device's key.
var openSetupWindow = func() (time.Time, error) { return api.Get().OpenAdoption() }

// offerSetup looks once a second at whether the device is waiting to be added, for the splash to offer
// it while it waits for Home Assistant. Only the frame loop calls it, so the splash's copy needs no
// lock; the tap side's does.
func (d *Display) offerSetup(now time.Time, step bootStep) {
	if d.logo == nil {
		return
	}
	if now.Sub(d.offerAt) >= time.Second {
		a := api.Get()
		d.offerAt = now
		d.logo.offer = setupOffer{shown: a.InSetup(), until: a.AdoptionOpenUntil()}
	}
	o := d.logo.offer
	d.mu.Lock()
	d.setupButtonUp = o.shown && o.until.IsZero() && step == stepHomeAssistant
	d.mu.Unlock()
}

// setupTapped opens the window when a tap during the splash lands on the button for it, and says
// whether it did. One tap opens it once: the button goes until the next look says otherwise.
func (d *Display) setupTapped(x, y int) bool {
	d.mu.Lock()
	up := d.booting && d.setupButtonUp
	d.mu.Unlock()
	if !up || d.r == nil || d.logo == nil || !image.Pt(x, y).In(d.logo.setupButton(d.r)) {
		return false
	}
	d.mu.Lock()
	d.setupButtonUp = false
	d.mu.Unlock()
	slog.Info("splash: opening the window for a Home Assistant to add this device")
	go func() {
		if _, err := openSetupWindow(); err != nil {
			slog.Warn("splash: opening the window failed", "err", err)
		}
	}()
	return true
}

// setupButton is where the splash's button sits: under the step's line, as wide as the bar.
func (s *splash) setupButton(r *renderer) image.Rectangle {
	bar := s.barRect(r)
	y := bar.Max.Y + r.s(66)
	return image.Rect(bar.Min.X, y, bar.Max.X, min(y+r.s(64), r.h-r.s(8)))
}

// drawSetupOffer draws, under the step's line at y, the button or, with the window open, what to do in
// Home Assistant.
func (r *renderer) drawSetupOffer(s *splash, y int) {
	if !s.offer.until.IsZero() {
		r.message(r.tiny, i18n.F("Open until {time}. In Home Assistant: Settings > Devices & services, under Discovered, choose Add.",
			"time", clockText(s.offer.until)), r.margin, y+r.s(56), cream)
		return
	}
	b := s.setupButton(r)
	fg := r.buttonFace(b, float64(b.Dy())/2, btnPrimary)
	label := "Let Home Assistant add it"
	r.text(r.small, label, b.Min.X+(b.Dx()-r.width(r.small, label))/2, (b.Min.Y+b.Max.Y)/2+r.s(10), fg)
}
