//go:build !dot && !spot

package display

import (
	"image"
	"math"
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
	s := newSplash(960, 480)
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
	s := newSplash(960, 480)
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
