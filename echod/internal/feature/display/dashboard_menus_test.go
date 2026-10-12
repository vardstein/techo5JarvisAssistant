//go:build !dot && !spot

package display

import (
	"image"
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// The settings and the drawer cover the dashboard rather than taking it away: it stays the page under
// them, and fingers go to the menu, not through it to the page.
func TestMenusCoverTheDashboard(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Mode(config.DashboardStreamed); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	d.dashboardAsked(true)
	for _, s := range []scene{{phase: "idle", showDrawer: true}, {phase: "idle", showSheet: true}} {
		d.dashScene(&s, false)
		if s.showDash || !s.dashBehind {
			t.Fatalf("drawer %v sheet %v: showDash %v dashBehind %v, want the dashboard behind", s.showDrawer, s.showSheet, s.showDash, s.dashBehind)
		}
		if d.dashShowing || d.dashFollow {
			t.Fatal("the dashboard kept the fingers under a menu")
		}
		if !d.dash {
			t.Fatal("opening a menu put the dashboard away")
		}
	}
	after := scene{phase: "idle"}
	d.dashScene(&after, false)
	if !after.showDash || after.dashBehind {
		t.Fatal("the dashboard did not come back when the menu went")
	}
}

// A ringing alarm or a call takes the whole screen, dashboard and all.
func TestRingingTakesTheDashboardAway(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Mode(config.DashboardStreamed); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	d.dashboardAsked(true)
	s := scene{phase: "idle", showDrawer: true}
	d.dashScene(&s, true)
	if s.showDash || s.dashBehind {
		t.Fatalf("busy: showDash %v dashBehind %v, want neither", s.showDash, s.dashBehind)
	}
}

// Drawn over the dashboard, the drawer is the same drawer, its back arrow putting it away, and the
// clock is not what is under it.
func TestTheDrawerIsDrawnOverTheDashboard(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	r := newRenderer(image.NewRGBA(image.Rect(0, 0, 960, 480)))
	r.draw(scene{phase: "idle", showDrawer: true, dashBehind: true, dashMode: config.DashboardStreamed})
	r.zmu.Lock()
	defer r.zmu.Unlock()
	closes := false
	for _, z := range r.zones {
		if z.kind == zoneBack && z.r.Min.X == 0 {
			closes = true
		}
	}
	if !closes {
		t.Fatal("no back arrow to close the drawer over the dashboard")
	}
	if !r.dateAt.Empty() {
		t.Fatal("the clock was drawn under the drawer in the dashboard's place")
	}
}
