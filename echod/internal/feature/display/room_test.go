//go:build !dot && !spot

package display

import (
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/dashboard"
)

// With the dashboard as the home page (the default), the swipe in from the left goes between the
// Show's dashboard and the room's and never to the clock; with no room's dashboard it does nothing.
// The clock is only for when there is no dashboard to show.
func TestBackStaysOnTheDashboardsWhenTheyAreHome(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Server("127.0.0.1:1", ""); err != nil {
		t.Fatal(err)
	}
	if err := config.Set().Dashboard().Room("dashboard-kitchen"); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	if !d.openDashboard() || d.dashPage != "" {
		t.Fatalf("the clock's swipe did not put the Show's dashboard up: page %q", d.dashPage)
	}
	d.back()
	if !d.dash || d.dashPage != "/dashboard-kitchen" {
		t.Fatalf("from the Show's dashboard: dash %v page %q, want the room's", d.dash, d.dashPage)
	}
	s := scene{phase: "idle"}
	d.dashScene(&s, false)
	if !s.showDash || s.dashMode != config.DashboardStreamed || s.dashPage != "/dashboard-kitchen" {
		t.Fatalf("room's page: shown %v mode %v page %q", s.showDash, s.dashMode, s.dashPage)
	}
	d.back()
	if !d.dash || d.dashPage != "" {
		t.Fatalf("from the room's dashboard: dash %v page %q, want the Show's own", d.dash, d.dashPage)
	}

	// From the library, back is still the Show's dashboard.
	d.dash, d.dashPage = true, dashboard.PageMusic
	d.back()
	if !d.dash || d.dashPage != "" {
		t.Fatalf("from the library: page %q", d.dashPage)
	}

	if err := config.Set().Dashboard().Room(config.RoomNone); err != nil {
		t.Fatal(err)
	}
	d.back()
	if !d.dash || d.dashPage != "" {
		t.Fatalf("with no room's dashboard: dash %v page %q, want the Show's to stay", d.dash, d.dashPage)
	}
}

// With the dashboard not the home page, the swipe goes round as it always did: the clock, the Show's
// dashboard, the room's, the clock.
func TestBackGoesRoundThroughTheClockWhenItIsHome(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Server("127.0.0.1:1", ""); err != nil {
		t.Fatal(err)
	}
	if err := config.Set().Dashboard().Idle(false); err != nil {
		t.Fatal(err)
	}
	if err := config.Set().Dashboard().Room("dashboard-kitchen"); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	d.openDashboard()
	d.back()
	if !d.dash || d.dashPage != "/dashboard-kitchen" {
		t.Fatalf("from the Show's dashboard: dash %v page %q, want the room's", d.dash, d.dashPage)
	}
	d.back()
	if d.dash {
		t.Fatalf("from the room's dashboard: still up on %q, want the clock", d.dashPage)
	}

	if err := config.Set().Dashboard().Room(config.RoomNone); err != nil {
		t.Fatal(err)
	}
	d.openDashboard()
	d.back()
	if d.dash {
		t.Fatal("with no room's dashboard the swipe did not take the dashboard away")
	}
}

// A dashboard standing in for the clock that has nothing to show yet, or can't be reached, leaves the
// clock up rather than an error on black; one somebody asked for takes the screen to say what is up.
func TestAHomeDashboardWithNothingToShowLeavesTheClock(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Server("127.0.0.1:1", ""); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(dashboard.Get().Close)
	d := &Display{r: testRenderer()}
	s := scene{phase: "idle"}
	d.dashScene(&s, false)
	if s.showDash || s.dashBehind {
		t.Fatalf("a home dashboard with no picture took the screen: problem %q", s.dash.Problem)
	}
	d.openDashboard()
	s = scene{phase: "idle"}
	d.dashScene(&s, false)
	if !s.showDash {
		t.Fatal("a dashboard asked for did not take the screen")
	}
}
