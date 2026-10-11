//go:build !dot && !spot

package display

import (
	"image"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// settle draws the splash at step until at, the way the display loop does, and returns it.
func settle(r *renderer, s *splash, step bootStep, from, at time.Duration) {
	for e := from; e <= at; e += 80 * time.Millisecond {
		r.drawSplash(s, e, step)
	}
}

func TestSplashBarFillsStepByStep(t *testing.T) {
	r := newRenderer(image.NewRGBA(image.Rect(0, 0, 960, 480)))
	s := newSplash(r)
	at := time.Duration(0)
	last := -1.0
	for step := stepStarting; step <= stepReady; step++ {
		settle(r, s, step, at, at+2*time.Second)
		at += 2 * time.Second
		want := float64(step) / bootSteps
		if math.Abs(s.shown-want) > 0.01 {
			t.Errorf("step %d: bar at %.2f, want %.2f", step, s.shown, want)
		}
		if s.shown <= last {
			t.Errorf("step %d: bar went from %.2f to %.2f", step, last, s.shown)
		}
		last = s.shown
	}
}

func TestSplashHintStaysOnThePanel(t *testing.T) {
	img := image.NewRGBA(image.Rect(0, 0, 960, 480))
	r := newRenderer(img)
	s := newSplash(r)
	settle(r, s, stepHomeAssistant, 0, waitingAfter+time.Second)
	edge := r.s(textEdge)
	for y := 0; y < img.Rect.Dy(); y++ {
		for _, x := range []int{0, edge - 2, r.w - edge + 1, r.w - 1} {
			if img.RGBAAt(x, y) != walnut {
				t.Fatalf("the splash drew into the panel edge at %d,%d", x, y)
			}
		}
	}
	if bar := s.barRect(r); bar.Max.Y+r.s(44)+r.s(44) >= r.h {
		t.Error("the hint line is below the panel")
	}
}

func TestSplashWordmarkIsTheProductName(t *testing.T) {
	if mark, sub := wordmark(); mark != "GENBU" || sub != "SHOW" {
		t.Errorf("wordmark = %q over %q, want GENBU over SHOW", mark, sub)
	}
}

// On the Show 8 the faces are a third bigger, so the wordmark's lines have to move apart by as much:
// at the Show 5's spacing SHOW ran up into GENBU. With SHOW_PREVIEW set the splash is written there
// for each panel.
func TestSplashWordmarkScalesWithThePanel(t *testing.T) {
	for _, panel := range []struct {
		name       string
		wide, high int
	}{{"", showWide, showHigh}, {"-show8", show8Wide, show8High}} {
		img := image.NewRGBA(image.Rect(0, 0, panel.wide, panel.high))
		r := newRenderer(img)
		s := newSplash(r)
		settle(r, s, stepHomeAssistant, 0, 2*time.Second)
		if dir := os.Getenv("SHOW_PREVIEW"); dir != "" {
			f, err := os.Create(filepath.Join(dir, "splash"+panel.name+".png"))
			if err != nil {
				t.Fatal(err)
			}
			if err := png.Encode(f, img); err != nil {
				t.Fatal(err)
			}
			f.Close()
		}

		// The lowest row of GENBU and the highest of SHOW, in the middle third where the words are.
		top := s.markRect(r).Max.Y
		lastMark, firstSub := -1, -1
		for y := top; y <= s.subY; y++ {
			for x := r.w / 3; x < 2*r.w/3; x++ {
				switch img.RGBAAt(x, y) {
				case cream:
					lastMark = y
				case amber:
					if firstSub < 0 {
						firstSub = y
					}
				}
			}
		}
		if lastMark < 0 || firstSub < 0 {
			t.Fatalf("%s: wordmark not found (GENBU to row %d, SHOW from row %d)", panel.name, lastMark, firstSub)
		}
		// The Show 5 has twenty; the Show 8 had eleven before the offsets were scaled.
		if gap := firstSub - lastMark; gap < r.s(16) {
			t.Errorf("%s: SHOW starts %dpx under GENBU, want at least %d", panel.name, gap, r.s(16))
		}
	}
}
