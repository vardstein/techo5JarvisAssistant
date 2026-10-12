//go:build !dot && !spot

package display

import (
	"log/slog"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/announce"
	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
)

// menuForget is how long the drawer or a settings, Wi-Fi or Bluetooth page stays up untouched before
// the home screen comes back (Jade, 2026-10-12: the radio and every menu, after about 20 seconds).
const menuForget = 20 * time.Second

// menuIdle puts away whatever menu is up once nobody has touched it for menuForget, back to the home
// screen: the dashboard, when it stands in for the clock. The time counts from the last finger or
// from the menu coming up, whichever is later, so one opened by voice or from Home Assistant gets its
// full time too. A voice turn over it keeps it up.
//
// A few pages wait for somebody however long it takes, since putting them away loses what they were
// doing: the Wi-Fi page on a device with no network (it is the one page that fixes that), a join
// running, a password half typed, a Bluetooth pairing running, and an announcement being recorded.
func (d *Display) menuIdle(now time.Time, talking bool) {
	bt := btaudio.Get()
	pairing := bt.Pairing()
	d.mu.Lock()
	up := d.drawer || d.sheet || d.wifiOpen || pairing
	if up && (!d.menuUp || talking) {
		d.menuSince = now
	}
	d.menuUp = up
	since := d.menuSince
	if d.touchedAt.After(since) {
		since = d.touchedAt
	}
	w := d.wifi
	wifiUp := d.wifiOpen
	d.mu.Unlock()
	if !up || now.Sub(since) < menuForget {
		return
	}
	if wifiUp && (!w.status.Connected || w.busy != "" || w.text != "") {
		return
	}
	if pairing {
		for _, dev := range bt.State().Devices {
			if dev.Busy {
				return
			}
		}
	}
	if announce.Get().Recording() {
		return
	}
	slog.Info("menus put away, untouched", "for", now.Sub(since).Round(time.Second))
	if wifiUp {
		d.closeWifi()
	}
	if pairing {
		bt.SetPairing(false)
	}
	d.showSheet(false)
	d.closeDrawer()
	d.mu.Lock()
	d.btFromSheet, d.btScroll = false, 0
	d.dashAwayUntil = time.Time{} // home is the dashboard again, not the clock it was put away for
	d.menuUp = false
	d.mu.Unlock()
}
