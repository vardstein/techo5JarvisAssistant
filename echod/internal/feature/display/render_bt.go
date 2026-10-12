//go:build !dot && !spot

package display

import (
	"slices"

	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// The Bluetooth page, opened from Connections in the settings: a back arrow and a title, a line
// saying what to do, and the devices as rows a finger can hit. The one connected and
// the ones paired come first, then what the scan has found. Geometry is shared with the gesture
// handler, which maps a tap back to a row.
// The sizes are the Show 5's (960 by 480): the heading and four rows fit in 480. The Show 8
// scales them like everything else.
const (
	btRowTopBase    = 108 // first row's top edge
	btRowHeightBase = 70
	btRows          = 4
)

func (p *paint) btRowTop() int    { return p.s(btRowTopBase) }
func (p *paint) btRowHeight() int { return p.s(btRowHeightBase) }

// btRowAt maps a tap to the row it landed on, or -1; the way back is btRows.
func (r *renderer) btRowAt(x, y int) int {
	if r.pageBackAt(x, y) {
		return btRows
	}
	if y < r.btRowTop() {
		return -1
	}
	row := (y - r.btRowTop()) / r.btRowHeight()
	if row >= btRows {
		return -1
	}
	return row
}

// btList is the page's devices in the order it lists them: connected, paired, then the rest, each
// group strongest first as the scan sorted them.
func btList(devices []btaudio.Device) []btaudio.Device {
	rank := func(d btaudio.Device) int {
		switch {
		case d.Connected:
			return 0
		case d.Paired:
			return 1
		}
		return 2
	}
	list := slices.Clone(devices)
	slices.SortStableFunc(list, func(a, b btaudio.Device) int { return rank(a) - rank(b) })
	return list
}

func (r *renderer) pairingPage(s scene) {
	hint := s.bt.Status
	if hint == "" {
		hint = "Put your earbuds in pairing mode"
	}
	r.pageHead("Bluetooth audio", hint, "")
	fc := r.faces()
	rowTop, rowH := r.btRowTop(), r.btRowHeight()

	if len(s.bt.Devices) == 0 {
		// A dot that walks while the scan runs, so a still list reads as searching rather than stuck.
		n := int(s.now.UnixMilli() / 400 % 4)
		r.text(fc.value, i18n.Sprintf("Searching%s", "..."[:n]), r.margin, rowTop+r.s(36), dim)
	}
	for i, d := range btList(s.bt.Devices) {
		if i >= btRows {
			break
		}
		tile := r.pageTile(rowTop+i*rowH, rowH, d.Connected)
		cy := (tile.Min.Y + tile.Max.Y) / 2
		right, rc := "tap to pair", dim
		switch {
		case d.Busy:
			right, rc = "working…", amber
		case d.Connected:
			right, rc = "connected · tap to disconnect", amber
		case d.Paired:
			right = "paired · tap to connect"
		}
		rw := r.width(fc.sub, right)
		r.text(fc.sub, right, r.w-r.margin-rw, cy+r.s(7), rc)
		face := fc.label
		if d.Connected {
			face = fc.labelBold
		}
		r.text(face, r.fit(face, d.Name, r.w-2*r.margin-rw-r.s(24)), r.margin, cy+r.s(10), cream)
	}
}
