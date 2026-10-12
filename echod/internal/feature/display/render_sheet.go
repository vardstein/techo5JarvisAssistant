//go:build !dot && !spot

package display

import (
	"image"
	"image/color"
)

// The Show's settings screen, in the manner of Spotify's Car Thing: one flat ground, a back arrow and
// "Settings" over the list of categories on the left, the chosen one in a soft box, and that
// category's settings beside it as large plain rows under a big title. No card, no shadows, no
// rules; the accent only where something is on or chosen. The parts are in sheet_widgets.go.

// settingsPage draws the whole settings screen: sel is the category on the rail, v the card, and pick
// a list of choices open over it (scrolled by pickScroll pixels when long), or nil.
func (r *renderer) settingsPage(sel category, v cardView, pick *pickerView, pickScroll int) {
	r.pending = r.pending[:0]
	r.settingsBase(sel, v.title, v.blurb, v.actions)
	for c := category(0); c < categories; c++ {
		top := r.navTop() + int(c)*(r.navH()+r.navGap())
		r.addZone(zone{r: image.Rect(0, top-r.navGap()/2, r.railW(), top+r.navH()+r.navGap()/2), kind: zoneCat, cat: c})
	}
	r.addZone(zone{r: image.Rect(0, 0, r.railW(), r.navTop()-r.navGap()/2), kind: zoneDone})

	// The card stays where the rail leaves it; what is on it, and a list opened over it, take the
	// menu size.
	card := r.nextCard(r.w, r.h)
	maxScroll, pickMax := 0, 0
	r.zoomed(func() {
		fc := r.faces()
		list := image.Rect(card.Min.X, card.Min.Y+r.headerH(), card.Max.X, card.Max.Y-r.s(8))
		if len(v.rows) == 0 && v.note != "" {
			y := list.Min.Y + r.s(50)
			for _, line := range r.wrap(fc.value, v.note, card.Dx()-2*r.rowIn()) {
				r.text(fc.value, line, card.Min.X+r.rowIn(), y, dim)
				y += r.s(36)
			}
		}

		maxScroll = r.rowList(card, list, v.rows, v.scroll, walnut, r.base.Pix)
		x := card.Max.X - r.s(26)
		for _, a := range v.actions {
			left := r.pillButton(x, card.Min.Y+r.headerH()/2+r.s(2), a.label, a.style)
			r.addZone(zone{r: image.Rect(left-r.s(4), card.Min.Y+r.s(8), x+r.s(4), card.Min.Y+r.headerH()-r.s(8)), kind: zoneAction, id: a.id})
			x = left - r.s(12)
		}

		if pick != nil {
			pickMax = r.picker(*pick, pickScroll)
		}
	})

	r.zmu.Lock()
	r.zones, r.pending = r.pending, r.zones
	r.cardMax, r.pickMax = maxScroll, pickMax
	r.zmu.Unlock()
}

// nextCard is where the category's settings sit on a w by h screen: beside the list of categories,
// down to the panel's foot. It is no longer a card that is drawn, only the room the rows have. A
// method because the rail's width and the margin are scaled to the panel in hand.
func (r *paint) nextCard(w, h int) image.Rectangle {
	return image.Rect(r.railW(), 0, w-r.cardIn(), h)
}

// baseKey is what the settings screen's unchanging part depends on: the category, the card's
// heading and the palette.
type baseKey struct {
	sel            category
	title, blurb   string
	actions        string
	g, a, t, d, rl color.RGBA
	w, h           int
	zoom           int32
}

// settingsBase paints the ground, the categories and the heading. It is the same every frame until the
// category, the heading or the theme changes, so it is kept and copied.
func (r *renderer) settingsBase(sel category, title, blurb string, actions []headerAction) {
	labels := ""
	for _, a := range actions {
		labels += a.label + "\x00"
	}
	key := baseKey{sel, title, blurb, labels, walnut, amber, cream, dim, ember, r.w, r.h, menuZoom.Load()}
	if r.base != nil && r.baseKey == key {
		copy(r.dst.Pix, r.base.Pix)
		return
	}
	fc := r.faces()
	r.settingsShell()

	// The list of categories: each its icon and name, the chosen one in a soft box, bold, its icon
	// in the accent.
	for c := category(0); c < categories; c++ {
		top := r.navTop() + int(c)*(r.navH()+r.navGap())
		pill := image.Rect(r.s(12), top, r.railW()-r.s(8), top+r.navH())
		fg, ink, face := dim, dim, fc.nav
		if c == sel {
			r.roundFill(pill, r.sf(14), surface(3), surface(3))
			fg, ink, face = cream, amber, fc.navBold
		}
		r.icon(c, r.s(12+30), top+r.navH()/2, ink)
		// A long name in bold runs out of its box ("Verbindungen"): it keeps the lighter weight then,
		// and is cut only if even that is too wide.
		room := pill.Max.X - r.s(12) - r.s(12+58)
		if r.width(face, categoryNames[c]) > room {
			face = fc.nav
		}
		r.text(face, r.fit(face, categoryNames[c], room), r.s(12+58), top+r.navH()/2+r.s(9), fg)
	}
	card := r.nextCard(r.w, r.h)
	r.zoomed(func() {
		fc := r.faces()
		// The heading keeps clear of the header's buttons, which are drawn over it every frame.
		room := card.Dx() - 2*r.rowIn()
		for _, a := range actions {
			room -= r.width(fc.button, a.label) + r.s(44+12)
		}
		r.text(fc.header, r.fit(fc.header, title, room), card.Min.X+r.rowIn(), card.Min.Y+r.s(56), cream)
	})

	if r.base == nil || r.base.Rect != r.dst.Rect {
		r.base = image.NewRGBA(r.dst.Rect)
	}
	copy(r.base.Pix, r.dst.Pix)
	r.baseKey = key
}

// settingsShell paints what every category shares, the ground and the way back, from a copy kept
// until the theme changes.
func (r *renderer) settingsShell() {
	key := baseKey{g: walnut, a: amber, t: cream, d: dim, rl: ember, w: r.w, h: r.h}
	if r.shell != nil && r.shellKey == key {
		copy(r.dst.Pix, r.shell.Pix)
		return
	}
	// One flat ground, and the way out at the top of the categories: a back arrow and "Settings".
	r.fill(r.dst.Rect, walnut)
	fc := r.faces()
	cy := (r.navTop() - r.navGap()) / 2
	r.backArrow(r.s(24), cy)
	r.text(fc.navBold, "Settings", r.s(12+58), cy+r.s(9), cream)

	if r.shell == nil || r.shell.Rect != r.dst.Rect {
		r.shell = image.NewRGBA(r.dst.Rect)
	}
	copy(r.shell.Pix, r.dst.Pix)
	r.shellKey = key
}
