//go:build !dot && !spot

package display

import (
	"image"
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
)

// A finger drags the settings, a pixel at a time and from where it set off; one that barely moved is
// still a tap, one that went sideways is still the swipe back, and the finger that pulled the settings
// down moves nothing.
func TestTheSettingsFollowTheFinger(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Screen().Welcomed(true); err != nil {
		t.Fatal(err)
	}
	d := &Display{r: testRenderer(), poke: make(chan struct{}, 1), on: true, view: voice.State{Phase: "idle"}}
	d.showSheet(true)
	// The longest card, drawn, so there is somewhere to scroll to.
	var long category
	most := 0
	for c := category(0); c < categories; c++ {
		s := sheetScene(c)
		s.showSheet = true
		d.r.draw(s)
		if m, _ := d.r.scrollLimits(); m > most {
			long, most = c, m
		}
	}
	s := sheetScene(long)
	s.showSheet = true
	d.r.draw(s)
	if most < 80 {
		t.Fatalf("no card scrolls far enough to test: %d", most)
	}
	d.mu.Lock()
	d.root, d.cat = false, long
	d.mu.Unlock()
	finger := func(k touch.Kind, x, y int) { d.gesture(touch.Gesture{Kind: k, X: x, Y: y}) }

	finger(touch.Hold, 480, 400)
	finger(touch.Drag, 480, 380) // within the slack: nothing moves yet
	if d.cardScroll != 0 {
		t.Fatalf("a finger inside the slack scrolled to %d", d.cardScroll)
	}
	finger(touch.Drag, 480, 360) // it sets off here
	finger(touch.Drag, 480, 323)
	if d.cardScroll != 37 {
		t.Fatalf("37 pixels up scrolled to %d, want 37", d.cardScroll)
	}
	finger(touch.Drag, 480, 340)
	finger(touch.Release, 480, 340)
	if d.cardScroll != 20 || !d.sheet || d.root {
		t.Fatalf("after the drag: scroll %d sheet %v root %v, want 20, the card still up", d.cardScroll, d.sheet, d.root)
	}

	// Sideways, far enough: the swipe back, to the categories.
	finger(touch.Hold, 200, 300)
	finger(touch.Drag, 260, 304)
	finger(touch.Drag, 400, 310)
	finger(touch.Release, 400, 310)
	if !d.root {
		t.Fatal("a swipe to the right did not go back")
	}

	// The finger that opened the settings, still moving: nothing.
	d.mu.Lock()
	d.root, d.cardScroll, d.openedBy = false, 0, image.Pt(500, 5)
	d.mu.Unlock()
	finger(touch.Hold, 500, 5)
	finger(touch.Drag, 500, 100)
	finger(touch.Drag, 500, 20)
	finger(touch.Release, 520, 20)
	if d.cardScroll != 0 || d.root {
		t.Fatalf("the opening finger: scroll %d root %v, want nothing", d.cardScroll, d.root)
	}
}

// The drawer's shelf slides under the finger sideways, and a swipe to the right with the shelf at
// its start still puts the drawer away; one that slid the shelf back to its start does not.
func TestTheShelfFollowsTheFinger(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Screen().Welcomed(true); err != nil {
		t.Fatal(err)
	}
	d := &Display{r: testRenderer(), poke: make(chan struct{}, 1), on: true, view: voice.State{Phase: "idle"}}
	d.openDrawer(drawerRadio)
	full := scene{phase: "idle", showDrawer: true, drawerTab: drawerRadio, demo: true}
	full.radio.Configured, full.radio.Stations = true, []string{"101.1 A", "88.5 B", "93.3 C", "104.7 D", "99.9 E"}
	d.r.draw(full)
	if m, _ := d.r.scrollLimits(); m < 150 {
		t.Fatalf("the shelf scrolls %d, too little to test", m)
	}
	finger := func(k touch.Kind, x, y int) { d.gesture(touch.Gesture{Kind: k, X: x, Y: y}) }

	finger(touch.Hold, 700, 250)
	finger(touch.Drag, 670, 250)
	finger(touch.Drag, 550, 255)
	if d.drawerScroll != 120 {
		t.Fatalf("120 pixels left slid the shelf to %d", d.drawerScroll)
	}
	finger(touch.Drag, 750, 255)
	finger(touch.Release, 750, 255)
	if !d.drawer || d.drawerScroll != 0 {
		t.Fatalf("slid back to the start: drawer %v scroll %d, want it in at 0", d.drawer, d.drawerScroll)
	}

	finger(touch.Hold, 300, 250)
	finger(touch.Drag, 330, 250)
	finger(touch.Drag, 500, 255)
	finger(touch.Release, 500, 255)
	if d.drawer {
		t.Fatal("a swipe right at the start of the shelf left the drawer in")
	}
}
