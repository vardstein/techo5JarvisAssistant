//go:build !dot && !spot

package display

import (
	"context"
	"fmt"
	"image"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wifi"
)

// The networks the Show keeps are listed under the ones it hears, each with its own Forget, at every
// menu size on both panels: inside the panel once scrolled to, and none on top of another.
func TestSavedNetworksEachHaveForget(t *testing.T) {
	t.Cleanup(func() { menuZoom.Store(100) })
	nets := []wifi.Network{{SSID: "HomeWiFi", Signal: -48, Secured: true}, {SSID: "Cafe guest", Signal: -78}}
	saved := []string{"HomeWiFi", "Grandma's", "Office"}
	home := wifi.Status{SSID: "HomeWiFi", Connected: true, State: "COMPLETED"}
	for _, m := range menuSizes {
		useMenuSize(menuSizeIndexOf(m.percent))
		for _, panel := range []image.Point{{showWide, showHigh}, {show8Wide, show8High}} {
			img := image.NewRGBA(image.Rect(0, 0, panel.X, panel.Y))
			r := newRenderer(img)
			what := fmt.Sprintf("%d%% %v", m.percent, panel)
			found := map[string]image.Rectangle{}
			for _, scroll := range []int{0, 1 << 20} {
				r.draw(scene{phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: nets, saved: saved, scroll: scroll}})
				for _, z := range r.zones {
					ssid, ok := strings.CutPrefix(z.id, "forget:")
					if !ok || z.kind != zoneAction {
						continue
					}
					if !z.r.In(img.Rect) {
						t.Errorf("%s: Forget for %q is off the panel: %v", what, ssid, z.r)
					}
					if hit, ok := r.zoneAt((z.r.Min.X+z.r.Max.X)/2, (z.r.Min.Y+z.r.Max.Y)/2); !ok || hit.id != z.id {
						t.Errorf("%s: a tap on Forget for %q found %+v", what, ssid, hit)
					}
					found[ssid] = z.r
				}
			}
			for _, ssid := range saved {
				if _, ok := found[ssid]; !ok {
					t.Errorf("%s: no Forget for %q", what, ssid)
				}
			}
		}
	}
}

// Forget asks twice: the first tap only arms it and shows Confirm, a second within the window forgets
// the network, and a second after the window arms it again instead.
func TestForgetAsksTwice(t *testing.T) {
	var mu sync.Mutex
	var forgot []string
	done := make(chan struct{}, 4)
	old := forgetWifi
	forgetWifi = func(_ context.Context, ssid string) error {
		mu.Lock()
		forgot = append(forgot, ssid)
		mu.Unlock()
		done <- struct{}{}
		return nil
	}
	t.Cleanup(func() { forgetWifi = old })

	d := &Display{r: testRenderer(), poke: make(chan struct{}, 1), on: true, wifiOpen: true}
	d.wifi = wifiState{status: wifi.Status{SSID: "HomeWiFi", Connected: true}, saved: []string{"HomeWiFi", "Office"}}
	tap := func(ssid string) {
		t.Helper()
		d.mu.Lock()
		w := d.wifi
		d.mu.Unlock()
		d.r.draw(scene{now: time.Now(), phase: "idle", showWifi: true, wifi: w})
		for _, z := range d.r.zones {
			if z.id == "forget:"+ssid {
				d.wifiGesture(touch.Gesture{Kind: touch.Tap, X: (z.r.Min.X + z.r.Max.X) / 2, Y: (z.r.Min.Y + z.r.Max.Y) / 2})
				return
			}
		}
		t.Fatalf("no Forget drawn for %q", ssid)
	}
	shows := func(ssid, want string) {
		t.Helper()
		d.mu.Lock()
		w := d.wifi
		d.mu.Unlock()
		armed := w.forget == ssid && time.Since(w.forgetAt) < restartWindow
		if got := map[bool]string{true: "Confirm", false: "Forget"}[armed]; got != want {
			t.Errorf("%q shows %s, want %s", ssid, got, want)
		}
	}

	tap("Office")
	shows("Office", "Confirm")
	if len(forgot) != 0 {
		t.Fatal("one tap forgot a network")
	}

	// A first tap on another network moves the question there; Office is not forgotten by it.
	tap("HomeWiFi")
	shows("HomeWiFi", "Confirm")
	shows("Office", "Forget")

	// Too late: the window has passed, so this tap asks again.
	d.mu.Lock()
	d.wifi.forgetAt = time.Now().Add(-restartWindow - time.Second)
	d.mu.Unlock()
	tap("HomeWiFi")
	shows("HomeWiFi", "Confirm")

	tap("HomeWiFi")
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("the second tap did not forget the network")
	}
	mu.Lock()
	defer mu.Unlock()
	if strings.Join(forgot, ",") != "HomeWiFi" {
		t.Errorf("forgot %q, want just HomeWiFi", forgot)
	}
	if !d.wifiOpen {
		t.Error("forgetting the network in use closed the page; it is where another is joined")
	}
}
