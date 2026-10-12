//go:build !dot && !spot

package display

import (
	"image"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
)

// The lists follow the finger. The touch screen reports up and down in notches of 40 pixels, made for
// the volume it once turned, and a list moved by them moves in jolts: the settings a notch at a time,
// the drawer's shelf a whole tile when the finger lifted. While one of them is up the screen is put in
// follow mode, as it is for the dashboard, and the list goes where the finger goes. A finger that
// barely moved is still a tap and one that went sideways is still the swipe it was, handed on as one,
// so what each page does with a tap or a swipe is decided where it always was.

// listOn is which page a finger is moving a list on.
type listOn int

const (
	listNone listOn = iota
	listSheet
	listDrawer
	listWifi
	listBT
)

// listDrag is a finger on a list, from where it came down.
type listDrag struct {
	on     listOn
	down   image.Point // where it came down: a tap or a swipe handed on is said to start there
	from   image.Point // where the list's movement is measured from: down, until it is moving
	axis   byte        // 0 until it is a scroll: 'v' up and down, 'h' sideways
	start  int         // the scroll when it came down
	ignore bool        // the finger that pulled the settings down, still on the glass
}

const (
	// fingerSlack is how far a finger moves before it moves the list rather than tapping: the touch
	// screen's own allowance for a tap, so nothing a tap used to reach is lost to a scroll.
	fingerSlack = 24

	// fingerSwipe is how far sideways a finger goes to be a swipe when it lifts, as the touch screen
	// counts one.
	fingerSwipe = 120
)

// listsFollow is whether the screen that was just drawn is one whose lists follow the finger: the
// settings, the drawer, the Wi-Fi or Bluetooth page, with nothing over them that takes the fingers
// itself.
func (d *Display) listsFollow(s scene, ringing bool) bool {
	if !(s.showSheet || s.showDrawer || s.showWifi || s.bt.Pairing) {
		return false
	}
	if ringing || s.call.Phase != phone.Idle || s.setupAsking || s.showDash || s.showCamera || !config.Get().Screen.Welcomed {
		return false
	}
	return d.r == nil || d.r.modalUp() == modalNone
}

// followDash and followLists put the touch screen in follow mode or take it out, for the dashboard
// and for the lists; it stays in while either wants it.
func (d *Display) followDash(on bool)  { d.follow(func() { d.dashFollow = on }) }
func (d *Display) followLists(on bool) { d.follow(func() { d.listFollow = on }) }

func (d *Display) follow(set func()) {
	d.mu.Lock()
	was := d.dashFollow || d.listFollow
	set()
	now := d.dashFollow || d.listFollow
	d.mu.Unlock()
	if now != was {
		touch.Get().SetFollow(now)
	}
}

// listFinger takes a Hold, Drag or Release on a page whose list follows the finger, in the order
// gesture asks the pages. It says whether the finger was the list's; a page that is not one of them
// (the dashboard's own) is left to gesture.
func (d *Display) listFinger(g touch.Gesture) bool {
	if d.r == nil {
		return false
	}
	switch g.Kind {
	case touch.Hold:
		on := d.listUnder()
		if on == listNone {
			d.mu.Lock()
			d.list = listDrag{}
			d.mu.Unlock()
			return false
		}
		pt := image.Pt(g.X, g.Y)
		d.mu.Lock()
		d.list = listDrag{on: on, down: pt, from: pt, ignore: on == listSheet && pt == d.openedBy}
		if p, _, _ := d.listScroll(); p != nil {
			d.list.start = *p
		}
		d.mu.Unlock()
		return true
	case touch.Drag:
		cardMax, pickMax := d.r.scrollLimits()
		slack := d.r.s(fingerSlack)
		d.mu.Lock()
		defer d.mu.Unlock()
		l := &d.list
		if l.on == listNone {
			return false
		}
		if l.ignore {
			return true
		}
		dx, dy := g.X-l.from.X, g.Y-l.from.Y
		if l.axis == 0 {
			// Measured on from where it set off, so the list does not jump by the slack.
			switch {
			case abs(dy) > slack && abs(dy) >= abs(dx):
				l.axis, l.from = 'v', image.Pt(g.X, g.Y)
			case abs(dx) > slack:
				l.axis, l.from = 'h', image.Pt(g.X, g.Y)
			}
			return true
		}
		p, pick, sideways := d.listScroll()
		most := cardMax
		if pick {
			most = pickMax
		}
		switch {
		case p == nil:
		case l.axis == 'v':
			*p = min(max(l.start-dy, 0), most)
		case l.axis == 'h' && sideways:
			*p = min(max(l.start-dx, 0), most)
		}
		d.wake()
		return true
	case touch.Release:
		d.mu.Lock()
		l := d.list
		_, _, sideways := d.listScroll()
		d.list = listDrag{}
		d.mu.Unlock()
		if l.on == listNone {
			return false
		}
		if l.ignore {
			return true
		}
		dx, dy := g.X-l.down.X, g.Y-l.down.Y
		var k touch.Kind
		switch {
		case l.axis == 0:
			// It never went far enough to move anything: a tap, wherever it lifted.
			k = touch.Tap
		case l.axis == 'h' && abs(dx) >= d.r.s(fingerSwipe) && abs(dx) > 2*abs(dy):
			k = touch.SwipeRight
			if dx < 0 {
				k = touch.SwipeLeft
			}
			// The shelf followed it, so it was a slide, not a swipe; except a swipe to the right
			// with the shelf at its start, which puts the drawer away, as it did.
			if sideways && (k == touch.SwipeLeft || l.start > 0) {
				k = ""
			}
		}
		if k != "" {
			d.gesture(touch.Gesture{Kind: k, X: l.down.X, Y: l.down.Y})
		}
		d.wake()
		return true
	}
	return false
}

// listUnder is the page a finger coming down now lands on, asked in gesture's order.
func (d *Display) listUnder() listOn {
	if !config.Get().Screen.Welcomed {
		return listNone
	}
	if btaudio.Get().Pairing() {
		return listBT
	}
	d.mu.Lock()
	dashUp, wifiOpen, drawerIn, sheet := d.dashShowing, d.wifiOpen, d.drawer, d.sheet
	d.mu.Unlock()
	if dashUp {
		return listNone
	}
	if _, up := home.Get().Camera(); up {
		return listNone
	}
	switch {
	case wifiOpen:
		return listWifi
	case drawerIn:
		return listDrawer
	case sheet:
		return listSheet
	}
	return listNone
}

// listScroll is what the finger on d.list scrolls: the scroll itself, whether it is a list of choices
// (which has its own limit), and whether a sideways finger moves it too (the drawer's shelf). nil is
// nothing to scroll: the Wi-Fi keyboard. d.mu is held.
func (d *Display) listScroll() (p *int, pick, sideways bool) {
	switch d.list.on {
	case listSheet:
		if d.picker != "" {
			return &d.pickScroll, true, false
		}
		return &d.cardScroll, false, false
	case listDrawer:
		if d.drawerPick != "" {
			return &d.pickScroll, true, false
		}
		return &d.drawerScroll, false, true
	case listWifi:
		if d.wifi.pick != nil {
			return nil, false, false
		}
		return &d.wifi.scroll, false, false
	case listBT:
		return &d.btScroll, false, false
	}
	return nil, false, false
}
