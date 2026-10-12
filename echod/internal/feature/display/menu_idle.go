//go:build !dot && !spot

package display

import (
	"log/slog"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/announce"
	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
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
	d.putMenusAway(wifiUp, pairing)
}

// putMenusAway closes the drawer and whichever settings, Wi-Fi or Bluetooth page is up.
func (d *Display) putMenusAway(wifiUp, pairing bool) {
	if wifiUp {
		d.closeWifi()
	}
	if pairing {
		btaudio.Get().SetPairing(false)
	}
	d.showSheet(false)
	d.closeDrawer()
	d.mu.Lock()
	d.btFromSheet, d.btScroll = false, 0
	d.dashAwayUntil = time.Time{} // home is the dashboard again, not the clock it was put away for
	d.menuUp = false
	d.mu.Unlock()
}

// keepsWork reports whether an open page would lose what somebody is in the middle of if it were
// put away: a Wi-Fi join or a half-typed password, a Bluetooth pairing running, an announcement being
// recorded. Wi-Fi with no network at all is left to menuIdle, which also keeps it.
func keepsWork(w wifiState, wifiUp, pairing bool) bool {
	if wifiUp && (w.busy != "" || w.text != "") {
		return true
	}
	if pairing {
		for _, dev := range btaudio.Get().State().Devices {
			if dev.Busy {
				return true
			}
		}
	}
	return announce.Get().Recording()
}

// assistantTakesOver clears the screen for a conversation that has just started (Jade, 2026-10-12:
// the assistant always goes over whatever is on the screen, except over itself). The drawer and the
// settings, Wi-Fi and Bluetooth pages are put away, and so are the pages a tap or Home Assistant put
// up: an alert, the calendar, the forecast, a camera. A follow-up is the same conversation and does
// not get here (changed), so a camera or a forecast its last answer showed stays up for the next
// question. A call, a ringing alarm and the setup question stay above it, as they always have.
func (d *Display) assistantTakesOver() {
	pairing := btaudio.Get().Pairing()
	d.mu.Lock()
	if !d.takeOver {
		d.mu.Unlock()
		return
	}
	d.takeOver = false
	w, wifiUp := d.wifi, d.wifiOpen
	menus := d.drawer || d.sheet || wifiUp || pairing
	d.closeAlert()
	d.calUntil, d.calDetail = time.Time{}, nil
	d.weatherUntil = time.Time{}
	d.mu.Unlock()
	if _, up := home.Get().Camera(); up {
		go home.Get().HideCamera()
	}
	if !menus || keepsWork(w, wifiUp, pairing) {
		return
	}
	slog.Info("menus put away for the assistant")
	d.putMenusAway(wifiUp, pairing)
}
