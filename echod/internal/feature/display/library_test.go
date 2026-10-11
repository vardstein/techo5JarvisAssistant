//go:build !dot && !spot

package display

import (
	"image"
	"path/filepath"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/dashboard"
)

// The library button sits in the cover's corner, clear of the cover's edge and of every other button
// on the page, so it neither takes the head's room nor lands under a finger meant for something else.
func TestTheLibraryButtonIsOnTheCover(t *testing.T) {
	late := time.Date(2026, 10, 10, 12, 59, 0, 0, time.UTC) // "12:59 PM", the widest clock
	for _, panel := range []image.Point{{X: 960, Y: 480}, {X: 1280, Y: 800}} {
		r := newRenderer(image.NewRGBA(image.Rect(0, 0, panel.X, panel.Y)))
		cover, _ := r.nowPlayingLayout()
		lib := r.libraryButton()
		if !lib.In(cover.Inset(r.s(8))) {
			t.Errorf("%v: library button %v is not inside the cover %v", panel, lib, cover)
		}
		fav, back, play, next, stop := r.nowPlayingButtons()
		for _, b := range []image.Rectangle{fav, back, play, next, stop, r.lyricsButton(late)} {
			if lib.Inset(-r.s(8)).Overlaps(b) {
				t.Errorf("%v: library button %v (with its slack) reaches the button at %v", panel, lib, b)
			}
		}
		if lib.Dx() < r.s(48) || lib.Dy() < r.s(40) {
			t.Errorf("%v: library button %v is smaller than a finger", panel, lib)
		}
	}
}

// Back is a tab inside the strip the swipe back starts in, so a finger on it is the Show's and is
// never handed to the page.
func TestTheLibraryBackTabIsInTheEdge(t *testing.T) {
	for _, panel := range []image.Point{{X: 960, Y: 480}, {X: 1280, Y: 800}} {
		r := newRenderer(image.NewRGBA(image.Rect(0, 0, panel.X, panel.Y)))
		b := r.libraryBack()
		if b.Min.X != 0 || b.Max.X > min(r.drawerEdge(), r.margin) {
			t.Errorf("%v: back tab %v is not inside the left edge strip (%d)", panel, b, min(r.drawerEdge(), r.margin))
		}
		if mid := (b.Min.Y + b.Max.Y) / 2; mid < panel.Y/2-1 || mid > panel.Y/2+1 || b.Dy() < r.s(60) {
			t.Errorf("%v: back tab %v is not a tall tab halfway down", panel, b)
		}
	}
}

// Music Assistant comes up streamed even on a Show whose dashboard is off or drawn, since only dashcast
// has it; and like a dashboard opened by hand it is forgotten, back to the dashboard's own page.
func TestTheLibraryIsStreamedAndForgotten(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Server("127.0.0.1:1", ""); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	d.dash, d.dashTouched, d.dashPage = true, time.Now(), dashboard.PageMusic
	s := scene{phase: "idle", nowPlaying: true}
	d.dashScene(&s, false)
	if !s.showDash || s.dashMode != config.DashboardStreamed || s.dashPage != dashboard.PageMusic {
		t.Fatalf("library up: showDash %v mode %v page %q, want streamed music", s.showDash, s.dashMode, s.dashPage)
	}

	d.dashTouched = time.Now().Add(-2 * dashForget)
	s = scene{phase: "idle", nowPlaying: true}
	d.dashScene(&s, false)
	if d.dash || d.dashPage != "" || s.showDash {
		t.Fatalf("library left alone stayed: dash %v page %q shown %v", d.dash, d.dashPage, s.showDash)
	}
}

// Back, and Home Assistant putting its dashboard up, both leave the library behind.
func TestLeavingTheLibrary(t *testing.T) {
	d := &Display{}
	d.dash, d.dashPage = true, dashboard.PageMusic
	d.closeDashboard()
	if d.dash || d.dashPage != "" {
		t.Fatalf("back left dash %v page %q", d.dash, d.dashPage)
	}

	d.dash, d.dashPage = true, dashboard.PageMusic
	d.dashboardAsked(true)
	if d.dashPage != "" {
		t.Fatal("dashboard_show over the library still streams the library")
	}
}

// Back from the library is the Show's own dashboard when it has one, and the page underneath only
// when it has none.
func TestBackFromTheLibraryIsTheDashboard(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Mode(config.DashboardOff); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	d.dash, d.dashPage = true, dashboard.PageMusic
	d.back()
	if d.dash {
		t.Fatal("back with no dashboard of the Show's own left a page up")
	}

	if err := config.Set().Dashboard().Mode(config.DashboardStreamed); err != nil {
		t.Fatal(err)
	}
	d.dash, d.dashPage = true, dashboard.PageMusic
	d.back()
	if !d.dash || d.dashPage != "" {
		t.Fatalf("back from the library: dash %v page %q, want the dashboard", d.dash, d.dashPage)
	}
	d.back()
	if !d.dash || d.dashPage != "" {
		t.Fatalf("back from the dashboard that is the home page: dash %v page %q, want it to stay", d.dash, d.dashPage)
	}
}

// With no dashcast server there is nothing to stream Music Assistant from, so the button does nothing.
func TestNoLibraryWithoutDashcast(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	d := &Display{}
	if d.openLibrary() || d.dash {
		t.Fatal("the library opened with no dashcast server to stream it")
	}
}
