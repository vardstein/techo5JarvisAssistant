//go:build !dot && !spot

package display

import (
	"image"
	"time"
)

// The full-screen pages that open from the settings, Bluetooth audio and Wi-Fi, are drawn from the
// settings' own parts: the same ground, heading, raised rows and pill buttons. Walking from the card
// into one of them should not look like opening another program.
//
// Each page keeps its own geometry (rows at fixed heights, a bar along the bottom), because its
// gesture handler maps a tap back through the same numbers. What is drawn here sits inside those
// rows and that bar, so a tap still lands where it did.

// pageBarH is the strip along the bottom that a page's buttons sit in, at the Show 5's size.
const pageBarH = 64

// pageHead paints the ground and a page's heading: its title, a line under it, and the time.
func (r *renderer) pageHead(title, hint string, now time.Time) {
	fc := r.faces()
	r.vgradient(r.dst.Bounds(), shift(walnut, 7), shift(walnut, -3))
	t := clockHM(now)
	tw := r.width(fc.value, t)
	r.text(fc.value, t, r.w-r.margin-tw, r.s(46), dim)
	r.text(fc.header, r.fit(fc.header, title, r.w-2*r.margin-tw-r.s(24)), r.margin, r.s(50), cream)
	r.text(fc.sub, r.fit(fc.sub, hint, r.w-2*r.margin), r.margin, r.s(82), dim)
}

// pageTile is one row of a page's list, top to top+h, raised off the ground; lit is the one in use,
// ringed in the accent. Text inside it starts at r.margin, as on the rest of the page.
func (r *renderer) pageTile(top, h int, lit bool) image.Rectangle {
	b := image.Rect(r.margin-r.s(14), top+r.s(3), r.w-r.margin+r.s(14), top+h-r.s(3))
	rad := r.sf(14)
	r.roundFill(b, rad, surface(3), surface(2))
	r.roundHighlight(b, rad)
	if lit {
		r.roundStroke(b, rad, r.sf(2), amber)
	}
	return b
}

// pageButton is a pill in the bottom bar, centred on cx and w wide. The bar's part around it is
// still what a tap is matched to; the pill is what a finger aims at.
func (r *renderer) pageButton(cx, w int, label string, style buttonStyle) {
	fc := r.faces()
	cy, half := r.h-r.s(pageBarH/2)-r.s(2), r.s(24)
	b := image.Rect(cx-w/2, cy-half, cx+w/2, cy+half)
	fg := r.buttonFace(b, float64(half), style)
	r.text(fc.button, label, cx-r.width(fc.button, label)/2, cy+r.s(8), fg)
}

// chevron is the arrow at a row's end that says it leads somewhere, its point at x.
func (r *renderer) chevron(x, cy int) {
	px, y, k := float64(x), float64(cy), r.sf(100)/100
	r.aaLine(px-7*k, y-7*k, px, y, 2.4*k, dim)
	r.aaLine(px, y, px-7*k, y+7*k, 2.4*k, dim)
}
