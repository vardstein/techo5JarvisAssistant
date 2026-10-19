//go:build !dot && !spot

package display

import (
	"image"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A device waiting to be added gets the button on both panels: under the bar, inside the screen, and
// drawn. With the window open the button goes and the words take its place.
func TestTheSplashOffersToLetHomeAssistantAddIt(t *testing.T) {
	for _, size := range []image.Point{{960, 480}, {1280, 800}} {
		r := newRenderer(image.NewRGBA(image.Rect(0, 0, size.X, size.Y)))
		s := newSplash(r)
		b := s.setupButton(r)
		if !b.In(r.dst.Rect) || b.Min.Y <= s.barRect(r).Max.Y || b.Dy() < r.s(48) {
			t.Errorf("%v: the button is at %v, the bar ends at %d", size, b, s.barRect(r).Max.Y)
		}

		// Low in the button's round right end: inside it, and under the words that replace it.
		mid := image.Pt(b.Max.X-b.Dy()/2, b.Max.Y-r.s(3))
		r.drawSplash(s, 0, stepHomeAssistant)
		plain := r.dst.RGBAAt(mid.X, mid.Y)

		s.offer = setupOffer{shown: true}
		r.drawSplash(s, 0, stepHomeAssistant)
		if got := r.dst.RGBAAt(mid.X, mid.Y); got == plain {
			t.Errorf("%v: nothing drawn where the button is", size)
		}
		saveSetupShot(t, r, "splash-setup")

		s.offer.until = time.Date(2026, 10, 19, 15, 4, 0, 0, time.Local)
		r.drawSplash(s, 0, stepHomeAssistant)
		if got := r.dst.RGBAAt(mid.X, mid.Y); got != plain {
			t.Errorf("%v: the button is still drawn with the window open", size)
		}
		saveSetupShot(t, r, "splash-setup-open")

		// Before there is a network, there is nothing to offer.
		s.offer = setupOffer{}
		r.drawSplash(s, 0, stepNetwork)
		before := r.dst.RGBAAt(mid.X, mid.Y)
		s.offer = setupOffer{shown: true}
		r.drawSplash(s, 0, stepNetwork)
		if got := r.dst.RGBAAt(mid.X, mid.Y); got != before {
			t.Errorf("%v: the button is drawn before there is a network", size)
		}
	}
}

// Only a tap on the button opens the window, only while it is up, and only once.
func TestATapOnTheButtonOpensTheWindowOnce(t *testing.T) {
	opened := make(chan struct{}, 4)
	old := openSetupWindow
	openSetupWindow = func() (time.Time, error) { opened <- struct{}{}; return time.Now(), nil }
	t.Cleanup(func() { openSetupWindow = old })

	r := newRenderer(image.NewRGBA(image.Rect(0, 0, 960, 480)))
	d := &Display{r: r, logo: newSplash(r), booting: true}
	b := d.logo.setupButton(r)
	in := image.Pt((b.Min.X+b.Max.X)/2, (b.Min.Y+b.Max.Y)/2)

	if d.setupTapped(in.X, in.Y) {
		t.Error("a tap opened the window with no button up")
	}
	d.setupButtonUp = true
	if d.setupTapped(in.X, b.Min.Y-r.s(20)) {
		t.Error("a tap above the button opened the window")
	}
	if !d.setupTapped(in.X, in.Y) {
		t.Fatal("a tap on the button did nothing")
	}
	if d.setupTapped(in.X, in.Y) {
		t.Error("a second tap opened it again")
	}
	select {
	case <-opened:
	case <-time.After(time.Second):
		t.Fatal("the window was not opened")
	}
	if len(opened) != 0 {
		t.Error("the window was opened more than once")
	}

	d.setupButtonUp, d.booting = true, false
	if d.setupTapped(in.X, in.Y) {
		t.Error("a tap after the splash opened the window")
	}
}

// saveSetupShot writes the panel for a look when SHOW_PREVIEW names a folder.
func saveSetupShot(t *testing.T, r *renderer, name string) {
	dir := os.Getenv("SHOW_PREVIEW")
	if dir == "" {
		return
	}
	f, err := os.Create(filepath.Join(dir, name+"-"+itoa(r.w)+".png"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := png.Encode(f, r.dst); err != nil {
		t.Fatal(err)
	}
}
