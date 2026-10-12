//go:build !dot && !spot

package display

import (
	"image"
	"image/draw"
	"log/slog"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/dashboard"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
)

// The dashboard page: a Home Assistant dashboard over the whole screen, drawn here or streamed from a
// dashcast server. Standing in for the clock (the default), it is the home page and the clock only
// shows while it can't be; a swipe in from the left edge brings it up from there, and on a dashboard
// goes to the room's when the house has one, and back; "go home" brings it up too. The screen's own
// edges keep working on it: down from the top is the settings, in from the right the drawer, so a
// dashboard that is the home page does not lock anybody out of the rest.
const (
	// dashForget is how long an opened dashboard stays up untouched before the clock comes back,
	// when it is not also the idle page.
	dashForget = 10 * time.Minute

	// dashAway is how long the clock stays up when the dashboard is the idle page and somebody put
	// it away.
	dashAway = 2 * time.Minute
)

// Where a finger on a streamed dashboard started, when that was one of the screen's own edges.
const (
	edgeNone = iota
	edgeLeft
	edgeTop
	edgeRight
	edgeBottom
)

// openDashboard puts the dashboard up, if there is one to put up.
func (d *Display) openDashboard() bool {
	if dashboard.Get().Mode() == config.DashboardOff {
		return false
	}
	d.mu.Lock()
	d.dash, d.dashHeld, d.dashTouched, d.dashPage = true, false, time.Now(), ""
	d.drawer, d.sheet = false, false
	d.mu.Unlock()
	slog.Info("dashboard up", "mode", dashboard.Get().Mode())
	d.wake()
	return true
}

// openLibrary puts Music Assistant's own pages up in the dashboard's place, from the now-playing
// page's library button: its library, queue and groups, streamed by dashcast like a dashboard and
// worked by touch the same way. Dashcast shows that page and nothing else in it, settings included.
// Back - the swipe in from the left, or the tab at the left edge - is the Show's own dashboard, or the
// now-playing page again when it has none.
func (d *Display) openLibrary() bool {
	if config.Get().Dashboard.Server == "" {
		return false
	}
	d.mu.Lock()
	d.dash, d.dashHeld, d.dashTouched, d.dashPage = true, false, time.Now(), dashboard.PageMusic
	d.drawer, d.sheet = false, false
	d.mu.Unlock()
	slog.Info("music library up")
	d.wake()
	return true
}

// closeDashboard takes the dashboard down: back to the clock, and when the dashboard is the idle page,
// the clock for a while.
func (d *Display) closeDashboard() {
	d.mu.Lock()
	d.dash, d.dashEdge, d.dashPage = false, edgeNone, ""
	if dashboard.Get().Idle() {
		d.dashAwayUntil = time.Now().Add(dashAway)
	}
	d.mu.Unlock()
	d.wake()
}

// back is the tab at the left edge and the swipe in from it. With the dashboard standing in for the
// clock it goes round the Show's own dashboard, the room's when the house has one
// (dashboard.RoomPath) and the recipe page when there is one (dashboard.RecipePath), and never to the
// clock: that is only for when there is no dashboard to show. Otherwise it goes round the clock, the
// Show's dashboard, the room's, the recipe and the clock again. Over Music Assistant's pages it is the
// Show's own dashboard, when it has one, rather than the page the library was opened from.
func (d *Display) back() {
	room := dashboard.Get().RoomPath()
	recipe := dashboard.Get().RecipePath()
	home := dashboard.Get().Idle()
	d.mu.Lock()
	page := d.dashPage
	next := ""
	switch {
	case page == dashboard.PageMusic && dashboard.Get().Mode() != config.DashboardOff:
		d.dashPage, d.dashTouched, d.dashEdge = "", time.Now(), edgeNone
		d.mu.Unlock()
		slog.Info("music library: back to the dashboard")
		d.wake()
		return
	case page == "" && room != "":
		next = room
	case (page == "" || page == room) && recipe != "" && page != recipe:
		next = recipe
	}
	if next != "" {
		// The recipe stays up until it is swiped on, like a dashboard Home Assistant put up.
		d.dash, d.dashPage, d.dashTouched, d.dashEdge = true, next, time.Now(), edgeNone
		d.dashHeld = next == recipe
		d.mu.Unlock()
		slog.Info("dashboard: next page", "path", next)
		d.wake()
		return
	}
	if home && page != "" {
		d.dash, d.dashHeld, d.dashPage, d.dashTouched, d.dashEdge = true, false, "", time.Now(), edgeNone
		d.mu.Unlock()
		slog.Info("dashboard: the Show's own")
		d.wake()
		return
	}
	if home {
		d.mu.Unlock()
		return
	}
	d.mu.Unlock()
	d.closeDashboard()
}

// openRecipe puts the recipe page up, held, from a swipe up or from Home Assistant (recipe_show).
// The settings or the drawer, if they are open, stay over it until they are put away, as with
// dashboard_show.
func (d *Display) openRecipe() bool {
	recipe := dashboard.Get().RecipePath()
	if recipe == "" {
		return false
	}
	d.mu.Lock()
	d.dash, d.dashHeld, d.dashTouched, d.dashAwayUntil, d.dashPage = true, true, time.Now(), time.Time{}, recipe
	d.dashEdge = edgeNone
	d.mu.Unlock()
	slog.Info("recipe up")
	d.wake()
	return true
}

// dashboardAsked is Home Assistant's dashboard_show and dashboard_hide. Shown, it stays until it is
// hidden or put away by hand, rather than the ten minutes one opened by a finger stays. Whatever else
// has the screen - the settings, the drawer, a camera - keeps it until it is done, the dashboard
// waiting behind it.
func (d *Display) dashboardAsked(up bool) {
	d.mu.Lock()
	if up {
		d.dash, d.dashHeld, d.dashTouched, d.dashAwayUntil, d.dashPage = true, true, time.Now(), time.Time{}, ""
		d.mu.Unlock()
		d.wake()
		return
	}
	showing := d.dash || d.dashShowing
	d.mu.Unlock()
	if showing {
		d.closeDashboard()
	}
}

// dashScene decides whether the dashboard is the page, and fetches what it shows. Everything else
// that takes the screen - a ringing alarm or a call (busy), the drawer, a camera, the weather, a
// turn - comes first; music comes first only when the dashboard is standing in for the clock rather
// than asked for. The settings and the drawer only cover it: it stays connected under them, and the
// drawer is drawn over it, so putting either away is the dashboard again at once.
func (d *Display) dashScene(s *scene, busy bool) {
	f := dashboard.Get()
	mode := f.Mode()
	d.mu.Lock()
	if d.dash && !d.dashHeld && time.Since(d.dashTouched) > dashForget {
		d.dash, d.dashPage = false, ""
	}
	asked := d.dash
	page := d.dashPage
	away := time.Now().Before(d.dashAwayUntil)
	d.mu.Unlock()

	// Music Assistant's pages and the room's dashboard are always streamed, whatever the dashboard is:
	// only dashcast has the one, and the drawn dashboard is the device's own alone.
	if page != "" {
		mode = config.DashboardStreamed
	}
	covered := s.showSheet || s.showDrawer
	shown := mode != config.DashboardOff && s.phase == "idle" && !busy &&
		!s.showCamera && !s.showWeather && !s.showRadar && !s.showCalendar && !s.showWifi && !s.bt.Pairing &&
		(asked || (f.Idle() && !away && !s.nowPlaying))
	want, behind := shown && !covered, shown && covered
	s.dashMode, s.dashPage = mode, page

	streamed := shown && mode == config.DashboardStreamed
	// Paused music gives way to the dashboard once the player goes idle, so the stream connects while
	// the music page is still up and the switch is the page itself rather than a wait on black.
	warm := !shown && !asked && mode == config.DashboardStreamed && s.phase == "idle" && s.nowPlaying &&
		!s.playing && !s.music.Playing && f.Idle() && !away
	if (streamed || warm) && d.r != nil {
		s.dash = f.Stream(d.r.w, d.r.h, page)
	}
	// Standing in for the clock, the dashboard waits behind it for its first picture, and one that
	// can't be reached leaves the clock up rather than an error on black. One asked for shows that it
	// is connecting, or what went wrong, since somebody is looking for it.
	if streamed && !asked && !s.dash.Ready {
		want, behind = false, false
	}
	s.showDash, s.dashBehind = want, behind
	if (want || behind) && mode == config.DashboardDrawn && d.r != nil {
		s.drawn = f.Drawn(d.r.w)
		d.mu.Lock()
		// A different dashboard starts at its top, and none is scrolled past its end: a short one
		// chosen after a long one scrolled down would otherwise be all above the screen.
		if path := config.Get().Dashboard.Path; path != d.dashScrollFor {
			d.dashScroll, d.dashScrollFor = 0, path
		}
		if _, content := d.r.dash(); content > 0 {
			d.dashScroll = min(d.dashScroll, max(content-d.r.h, 0))
		}
		s.dashScroll, s.dashAdjust = d.dashScroll, d.dashAdjust
		d.mu.Unlock()
	}

	d.mu.Lock()
	d.dashShowing = want
	d.mu.Unlock()
	// Either way the page wants every finger as it moves: streamed, to scroll the page under it;
	// drawn, to scroll and to slide a tile's level. The lists want them too (follow.go); the rest of
	// the screen wants swipes.
	d.followDash(want)
}

// dashGesture is a finger on the dashboard. It goes to the page as it moves, except a finger that
// starts at one of the screen's edges: the left takes the dashboard away, the top brings the
// settings down, the right the drawer in. Streamed, the page is the browser's; drawn, a finger
// moving up or down scrolls it and one moving along a tile with a level slides the level.
func (d *Display) dashGesture(g touch.Gesture) {
	if d.r == nil {
		return
	}
	d.mu.Lock()
	d.dashTouched = time.Now()
	d.mu.Unlock()
	edge := d.r.drawerEdge()
	f := dashboard.Get()
	d.mu.Lock()
	library := d.dashPage != ""
	d.mu.Unlock()
	streamed := f.Mode() == config.DashboardStreamed || library

	switch g.Kind {
	case touch.Tap:
		if library && image.Pt(g.X, g.Y).In(d.r.libraryBack().Inset(-d.r.s(8))) {
			d.back()
			return
		}
		if streamed {
			f.Touch("tap", g.X, g.Y)
		} else {
			d.drawnTap(g.X, g.Y)
		}
	case touch.Hold:
		from := edgeNone
		// On the dashboard the edges are the strips beside the page, not the clock's wider bands, and
		// a finger that lands on a tile is the tile's even there: sliding a light on the right-hand
		// side is not opening the drawer.
		edge = min(edge, d.r.margin)
		onTile := !streamed && d.tileAt(g.X, g.Y) != nil
		switch {
		case onTile:
		case g.X < edge:
			from = edgeLeft
		case g.X >= d.r.w-edge:
			from = edgeRight
		case g.Y < topEdge/3:
			// A thinner band than the clock's: the top of a dashboard is where its own tabs are.
			from = edgeTop
		case g.Y >= d.r.h-topEdge/3:
			from = edgeBottom
		}
		d.mu.Lock()
		d.dashEdge, d.dashEdgeAt = from, image.Pt(g.X, g.Y)
		d.dashDrag = drawnDrag{startScroll: d.dashScroll}
		d.mu.Unlock()
		if from == edgeNone {
			if streamed {
				f.Touch("down", g.X, g.Y)
			} else {
				d.drawnHold(g.X, g.Y)
			}
		}
	case touch.Drag:
		d.mu.Lock()
		from := d.dashEdge
		d.mu.Unlock()
		if from != edgeNone {
			return
		}
		if streamed {
			f.Touch("move", g.X, g.Y)
		} else {
			d.drawnMove(g.X, g.Y)
		}
	case touch.Release:
		d.mu.Lock()
		from, start := d.dashEdge, d.dashEdgeAt
		d.dashEdge = edgeNone
		d.mu.Unlock()
		far := d.r.s(80)
		switch from {
		case edgeNone:
			if streamed {
				f.Touch("up", g.X, g.Y)
			} else {
				d.drawnRelease()
			}
		case edgeLeft:
			if g.X-start.X > far {
				d.back()
			}
		case edgeRight:
			if start.X-g.X > far {
				d.openDrawerOver()
			}
		case edgeTop:
			if g.Y-start.Y > far {
				d.showSheet(true)
			}
		case edgeBottom:
			if start.Y-g.Y > far {
				d.openRecipe()
			}
		}
	}
}

// openDrawerOver brings the drawer in over the dashboard, on the tab it was last on.
func (d *Display) openDrawerOver() {
	d.mu.Lock()
	tab := d.drawerTab
	d.mu.Unlock()
	d.openDrawer(tab)
}

// drawnDashboard is the drawn dashboard over the whole panel.
func (r *renderer) drawnDashboard(s scene) {
	r.dashPage(s.drawn, s.dashScroll, s.dashAdjust, r.dst.Rect)
}

// dashboardPage draws the dashboard over the whole panel.
func (r *renderer) dashboardPage(s scene) {
	if s.dashMode == config.DashboardDrawn {
		r.drawnDashboard(s)
		return
	}
	v := s.dash
	drawn := v.Ready && dashboard.Get().DrawStream(r.dst)
	if s.dashPage != "" {
		defer r.libraryBackTab()
	}
	msg := v.Problem
	if msg == "" && !drawn {
		msg = "Connecting to the dashboard…"
		if s.dashPage == dashboard.PageMusic {
			msg = "Connecting to Music Assistant…"
		}
	}
	if msg == "" {
		return
	}
	if !drawn {
		r.message(r.small, msg, r.margin, r.h/2, dim)
		return
	}
	// Over the picture: the last one stays up, with what went wrong along the foot.
	draw.Draw(r.dst, image.Rect(0, r.h-r.s(36), r.w, r.h), image.NewUniform(shade), image.Point{}, draw.Over)
	r.text(r.tiny, r.fit(r.tiny, msg, r.w-2*r.margin), r.margin, r.h-r.s(11), dim)
}

// libraryBack is the tab at the left edge over Music Assistant's pages, inside the strip the swipe back
// starts in, so a finger there is the Show's rather than the page's. A tap on it is back, the same as
// the swipe, for whoever does not know the swipe.
func (r *renderer) libraryBack() image.Rectangle {
	w, h := min(r.margin, r.s(30)), r.s(84)
	return image.Rect(0, (r.h-h)/2, w, (r.h+h)/2)
}

// libraryBackTab draws it: a tab coming out of the edge with an arrow pointing back.
func (r *renderer) libraryBackTab() {
	b := r.libraryBack()
	// Lighter than the pills on the now-playing page: it stands over Music Assistant's own dark ground.
	ground := shift(walnut, 26)
	if !dark() {
		ground = shift(walnut, -6)
	}
	rad := float64(b.Dx()) * 0.6
	r.roundFillF(float64(b.Min.X)-rad, float64(b.Min.Y), float64(b.Max.X), float64(b.Max.Y), rad, ground)
	cx, cy, u := float64(b.Min.X+b.Max.X)/2, float64(b.Min.Y+b.Max.Y)/2, float64(r.s(9))
	r.aaPoly([][2]float64{{cx + u*0.5, cy - u}, {cx - u*0.6, cy}, {cx + u*0.5, cy + u}}, cream)
}
