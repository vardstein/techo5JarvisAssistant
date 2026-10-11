//go:build !dot && !spot

package display

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/dashboard"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
)

func useRecipe(t *testing.T, room string) {
	t.Helper()
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Server("127.0.0.1:1", ""); err != nil {
		t.Fatal(err)
	}
	if err := config.Set().Dashboard().Room(room); err != nil {
		t.Fatal(err)
	}
	known := []config.DashboardChoice{{Label: "Rezept", Path: "jarvis-rezept", Streamed: true}}
	if err := config.Set().Dashboard().Known(known); err != nil {
		t.Fatal(err)
	}
}

// The swipe in from the left goes round the Show's dashboard, the room's and the recipe, and the
// recipe stays up past the time a dashboard is otherwise forgotten.
func TestBackGoesRoundThroughTheRecipe(t *testing.T) {
	useRecipe(t, "dashboard-kitchen")
	d := &Display{}
	d.openDashboard()
	for _, want := range []string{"/dashboard-kitchen", "/jarvis-rezept", ""} {
		d.back()
		if !d.dash || d.dashPage != want {
			t.Fatalf("dash %v page %q, want %q", d.dash, d.dashPage, want)
		}
	}

	// No room's dashboard: straight from the Show's to the recipe and back.
	if err := config.Set().Dashboard().Room(config.RoomNone); err != nil {
		t.Fatal(err)
	}
	d.back()
	if d.dashPage != "/jarvis-rezept" || !d.dashHeld {
		t.Fatalf("page %q held %v, want the recipe held", d.dashPage, d.dashHeld)
	}
	d.back()
	if d.dashPage != "" || d.dashHeld {
		t.Fatalf("page %q held %v, want the Show's own, not held", d.dashPage, d.dashHeld)
	}
}

// A swipe up on the clock, or Home Assistant's recipe_show, puts the recipe up and it stays.
func TestTheRecipeComesUpAndStays(t *testing.T) {
	useRecipe(t, config.RoomNone)
	d := &Display{}
	if !d.openRecipe() {
		t.Fatal("the recipe did not come up")
	}
	if !d.dash || !d.dashHeld || d.dashPage != "/jarvis-rezept" {
		t.Fatalf("dash %v held %v page %q", d.dash, d.dashHeld, d.dashPage)
	}
	d.dashTouched = time.Now().Add(-2 * dashForget)
	s := scene{phase: "idle"}
	d.dashScene(&s, false)
	if !d.dash || d.dashPage != "/jarvis-rezept" {
		t.Fatalf("the recipe went away after %v: dash %v page %q", 2*dashForget, d.dash, d.dashPage)
	}
}

// Without a recipe dashboard in Home Assistant a swipe up does nothing.
func TestNoRecipeNoPage(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Dashboard().Server("127.0.0.1:1", ""); err != nil {
		t.Fatal(err)
	}
	d := &Display{}
	if d.openRecipe() || d.dash {
		t.Fatal("a recipe page came up with no recipe dashboard")
	}
}

// On a streamed dashboard a drag up from the bottom edge is the recipe; one too short is not.
func TestASwipeUpFromTheDashboardsBottomIsTheRecipe(t *testing.T) {
	useRecipe(t, config.RoomNone)
	t.Cleanup(dashboard.Get().Close)
	d := &Display{r: testRenderer()}
	d.openDashboard()
	bottom, x := d.r.h-5, d.r.w/2
	d.dashGesture(touch.Gesture{Kind: touch.Hold, X: x, Y: bottom})
	d.dashGesture(touch.Gesture{Kind: touch.Release, X: x, Y: bottom - d.r.s(20)})
	if d.dashPage != "" {
		t.Fatalf("a short drag put up %q", d.dashPage)
	}
	d.dashGesture(touch.Gesture{Kind: touch.Hold, X: x, Y: bottom})
	d.dashGesture(touch.Gesture{Kind: touch.Release, X: x, Y: bottom - d.r.s(120)})
	if d.dashPage != "/jarvis-rezept" || !d.dashHeld {
		t.Fatalf("page %q held %v, want the recipe", d.dashPage, d.dashHeld)
	}
}
