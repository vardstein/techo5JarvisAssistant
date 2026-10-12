//go:build !dot && !spot

package display

import (
	"image"
)

// The full-screen pages that open from the settings, Bluetooth audio and Wi-Fi, are drawn from the
// settings' own parts: the same flat ground, a back arrow beside a big title, plain rows, and the one
// in use in a soft box. Walking from the settings into one of them should not look like opening
// another program.
//
// Each page keeps its own geometry (rows at fixed heights), because its gesture handler maps a tap
// back through the same numbers. What is drawn here sits inside those rows, so a tap still lands
// where it did.

// pageHeadBase is how tall a page's heading is at the Show 5's size: the title and the line under it.
const pageHeadBase = 100

// pageBackAt is whether a tap at (x, y) hit a page's way back: the arrow and the title beside it.
func (p *paint) pageBackAt(x, y int) bool {
	return y < p.s(pageHeadBase)-p.s(24) && x < p.w/2
}

// pageHead paints the ground and a page's heading: a back arrow, its title, and a dim line under it.
// action, when set, is a button at the heading's right, and the title keeps clear of it.
func (r *renderer) pageHead(title, hint, action string) {
	fc := r.faces()
	r.fill(r.dst.Rect, walnut)
	left, cy := r.margin-r.s(12), r.s(38)
	right := r.w - r.margin
	if action != "" {
		right = r.pillButton(r.w-r.margin+r.s(12), cy, action, btnSecondary) - r.s(16)
	}
	r.backArrow(left, cy)
	x := left + r.s(32)
	r.text(fc.header, r.fit(fc.header, title, right-x), x, cy+r.s(13), cream)
	r.text(fc.sub, r.fit(fc.sub, hint, r.w-r.margin-x), x, r.s(78), dim)
}

// pageTile is one row of a page's list, top to top+h. Rows are plain on the ground; lit is the one in
// use, set in a soft box. Text inside it starts at r.margin, as on the rest of the page.
func (r *renderer) pageTile(top, h int, lit bool) image.Rectangle {
	b := image.Rect(r.margin-r.s(14), top+r.s(3), r.w-r.margin+r.s(14), top+h-r.s(3))
	if lit {
		r.roundFill(b, r.sf(14), surface(3), surface(3))
	}
	return b
}

// chevron is the arrow at a row's end that says it leads somewhere, its point at x.
func (r *renderer) chevron(x, cy int) {
	px, y, k := float64(x), float64(cy), r.sf(100)/100
	r.aaLine(px-7*k, y-7*k, px, y, 2.4*k, dim)
	r.aaLine(px, y, px-7*k, y+7*k, 2.4*k, dim)
}
