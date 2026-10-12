//go:build !dot && !spot

package display

import (
	"testing"
	"time"

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
