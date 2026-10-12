//go:build !dot && !spot

package display

import (
	"log/slog"
	"strconv"
	"strings"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
)

// openDrawer brings the drawer in on a tab.
func (d *Display) openDrawer(tab int) {
	d.mu.Lock()
	d.drawer, d.drawerTab, d.drawerScroll, d.drawerPick, d.pickScroll = true, tab, 0, "", 0
	d.weatherUntil, d.radar = time.Time{}, false
	d.mu.Unlock()
	if tab == drawerCameras {
		go home.Get().Prewarm()
	}
	if tab == drawerRadio {
		// Whether this room is in a house is what the Stop row says, and the drawer is opened the moment
		// before it is pressed. See home.PokeGroup.
		go home.Get().PokeGroup()
	}
	slog.Info("drawer", "open", drawerTabs[tab])
	d.wake()
}

// drawerSwipe is a leftward swipe from the right edge: the drawer's way in, from the clock and from
// every page laid over it alike.
func (d *Display) drawerSwipe(g touch.Gesture) bool {
	return d.r != nil && g.Kind == touch.SwipeLeft && g.X >= d.r.w-d.r.drawerEdge()
}

// closeDrawer puts it away. It says so, the way openDrawer does: whether a pick closed the drawer or left
// it over what it had opened is the whole of one of the things the hardware testing found, and a screen
// nobody can see is no place to check it.
func (d *Display) closeDrawer() {
	d.mu.Lock()
	d.drawer, d.drawerPick = false, ""
	d.mu.Unlock()
	slog.Info("drawer closed")
	d.wake()
}

// drawerGesture is a finger on the drawer: taps on what it shows, sideways swipes slide the shelf a tile
// at a time, and a swipe back to the right at its start puts it away. Up and down scroll a list of
// choices opened from it, and slide the shelf too, for a swipe that came out slanted.
func (d *Display) drawerGesture(g touch.Gesture) {
	if d.r == nil {
		return
	}
	cardMax, pickMax := d.r.scrollLimits()
	switch g.Kind {
	case touch.SwipeLeft, touch.SwipeRight, touch.SwipeUp, touch.SwipeDown:
		forward := g.Kind == touch.SwipeLeft || g.Kind == touch.SwipeUp
		d.mu.Lock()
		switch {
		case d.drawerPick != "" && (g.Kind == touch.SwipeUp || g.Kind == touch.SwipeDown):
			by := notchPx
			if !forward {
				by = -notchPx
			}
			d.pickScroll = min(max(d.pickScroll+by, 0), pickMax)
			d.mu.Unlock()
		case d.drawerPick != "":
			// Sideways over a list of choices is back to the shelf.
			if g.Kind == touch.SwipeRight {
				d.drawerPick = ""
			}
			d.mu.Unlock()
		case g.Kind == touch.SwipeRight && d.drawerScroll <= 0:
			d.mu.Unlock()
			d.closeDrawer()
		default:
			by := d.r.shelfStep()
			if !forward {
				by = -by
			}
			d.drawerScroll = min(max(d.drawerScroll+by, 0), cardMax)
			d.mu.Unlock()
		}
	case touch.Tap:
		z, ok := d.r.zoneAt(g.X, g.Y)
		if !ok {
			return
		}
		switch z.kind {
		case zoneBack:
			// From a list of choices, back is the shelf; from the shelf, it puts the drawer away.
			d.mu.Lock()
			picking := d.drawerPick != ""
			d.drawerPick = ""
			d.mu.Unlock()
			if !picking {
				d.closeDrawer()
			}
		case zoneDone:
			d.closeDrawer()
		case zoneTab:
			d.mu.Lock()
			d.drawerTab, d.drawerScroll, d.drawerPick = z.opt, 0, ""
			d.mu.Unlock()
			if z.opt == drawerCameras {
				go home.Get().Prewarm()
			}
			if z.opt == drawerRadio {
				go home.Get().PokeGroup()
			}
		case zoneDismiss:
			d.mu.Lock()
			d.drawerPick = ""
			d.mu.Unlock()
		case zoneOption:
			d.mu.Lock()
			id := d.drawerPick
			d.drawerPick = ""
			d.mu.Unlock()
			if id == "radiosource" {
				if sources := home.RadioSources(); z.opt < len(sources) {
					d.mu.Lock()
					d.drawerScroll = 0
					d.mu.Unlock()
					home.Get().SetRadioSource(sources[z.opt])
				}
			}
		case zoneRow:
			d.drawerRowTap(z.id)
		}
	}
}

// drawerRowTap is a tap on one of the drawer's rows.
func (d *Display) drawerRowTap(id string) {
	if id == "radiosource" {
		d.mu.Lock()
		d.drawerPick, d.pickScroll = id, 0
		d.mu.Unlock()
		return
	}
	kind, n, ok := strings.Cut(id, ":")
	i, err := strconv.Atoi(n)
	if !ok || err != nil || i < 0 {
		return
	}
	switch kind {
	case "announce":
		d.announceTap()
	case "call":
		d.mu.Lock()
		list := d.callees
		d.mu.Unlock()
		if i < len(list) {
			d.closeDrawer()
			go func(c phone.Callee) {
				if err := phone.Get().CallCallee(c); err != nil {
					slog.Warn("screen: call", "err", err)
				}
			}(list[i])
		}
	case "cam":
		if cams := home.Get().Cameras(); i < len(cams) {
			d.closeDrawer()
			home.Get().ShowCamera(cams[i].Entity, cameraScreenTime())
		}
	case "st":
		rows := radioList(home.Get().Radio())
		if i >= len(rows) {
			return
		}
		// The pick is the last thing the drawer is for: what it leaves you looking at is the page
		// for it, so the drawer goes, the way it does for a camera. A station picked here used to
		// leave the list sitting over the station somebody had just chosen, and Stop the radio left
		// it over the clock, though either pick is as finished with the list as a camera is.
		d.closeDrawer()
		if rows[i] == "■ Stop" {
			home.Get().Stop()
			return
		}
		home.Get().Play(rows[i])
	}
}
