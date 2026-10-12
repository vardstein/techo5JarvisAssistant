//go:build !dot && !spot

package display

import (
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wifi"
)

// The drawer and the settings go away after 20 s untouched, counted from the later of the last finger
// and their coming up; the Wi-Fi page of a device with no network, or with a password half typed, stays.
func TestMenusGoHomeUntouched(t *testing.T) {
	at := time.Date(2026, 10, 12, 12, 0, 0, 0, time.Local)
	d := &Display{r: testRenderer(), poke: make(chan struct{}, 1), on: true}
	d.openDrawer(drawerRadio)
	d.touchedAt = at.Add(-time.Hour)
	d.menuIdle(at, false)
	d.menuIdle(at.Add(menuForget-time.Second), false)
	if !d.drawer {
		t.Fatal("the drawer went before its time: it came up at the first frame, not at the last touch")
	}
	d.touchedAt = at.Add(10 * time.Second)
	d.menuIdle(at.Add(menuForget+time.Second), false)
	if !d.drawer {
		t.Fatal("the drawer went although it was touched 11 s ago")
	}
	d.menuIdle(at.Add(10*time.Second+menuForget+time.Second), true)
	if !d.drawer {
		t.Fatal("the drawer went during a voice turn")
	}
	d.menuIdle(at.Add(2*menuForget+12*time.Second), false)
	if d.drawer {
		t.Fatal("the drawer stayed up untouched past its time")
	}

	d.showSheet(true)
	d.mu.Lock()
	d.root, d.page, d.picker = false, "display", "theme"
	d.mu.Unlock()
	d.menuIdle(at.Add(time.Hour), false)
	d.menuIdle(at.Add(time.Hour+menuForget), false)
	if d.sheet {
		t.Fatal("a settings page with a list open stayed up untouched past its time")
	}

	for _, tc := range []struct {
		name string
		w    wifiState
		stay bool
	}{
		{"no network", wifiState{}, true},
		{"joining", wifiState{status: wifi.Status{Connected: true}, busy: "Connecting to Garden…"}, true},
		{"password half typed", wifiState{status: wifi.Status{Connected: true}, text: "correct horse"}, true},
		{"the list, connected", wifiState{status: wifi.Status{Connected: true}}, false},
	} {
		start := at.Add(2 * time.Hour)
		d.mu.Lock()
		d.wifiOpen, d.wifi = true, tc.w
		d.mu.Unlock()
		d.menuIdle(start, false)
		d.menuIdle(start.Add(menuForget), false)
		if d.wifiOpen != tc.stay {
			t.Errorf("Wi-Fi page, %s: up %v after 20 s untouched, want %v", tc.name, d.wifiOpen, tc.stay)
		}
		d.closeWifi()
		d.menuIdle(start.Add(time.Hour), false)
	}
}

// A conversation started with the wake word takes the screen: the drawer and the settings go, and so
// do the pages put up before it. A follow-up is the same conversation and leaves the screen alone, and
// a frame that only comes once the question has been heard does not take down what the answer shows.
func TestTheAssistantTakesTheScreen(t *testing.T) {
	d := &Display{r: testRenderer(), poke: make(chan struct{}, 1), on: true, view: voice.State{Phase: "idle"}}
	turn := func(followUp bool) {
		d.changed(voice.State{Phase: "idle"})
		d.changed(voice.State{Phase: "listening", FollowUp: followUp})
	}

	d.openDrawer(drawerRadio)
	d.mu.Lock()
	d.calUntil, d.weatherUntil = time.Now().Add(time.Minute), time.Now().Add(time.Minute)
	d.mu.Unlock()
	turn(false)
	d.assistantTakesOver()
	if d.drawer || !d.calUntil.IsZero() || !d.weatherUntil.IsZero() {
		t.Fatalf("a new conversation left the screen as it was: drawer=%v calendar=%v forecast=%v", d.drawer, d.calUntil, d.weatherUntil)
	}

	d.showSheet(true)
	turn(true)
	d.assistantTakesOver()
	if !d.sheet {
		t.Fatal("a follow-up put the settings away")
	}

	turn(false)
	d.changed(voice.State{Phase: "thinking", Heard: "show the front door"})
	d.assistantTakesOver()
	if !d.sheet {
		t.Fatal("a frame after the question had been heard still took the screen")
	}

	d.showSheet(false)
	d.mu.Lock()
	d.wifiOpen, d.wifi = true, wifiState{status: wifi.Status{Connected: true}, text: "correct horse"}
	d.mu.Unlock()
	turn(false)
	d.assistantTakesOver()
	if !d.wifiOpen {
		t.Fatal("a half-typed Wi-Fi password was thrown away for the assistant")
	}
}
