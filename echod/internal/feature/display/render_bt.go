//go:build !dot && !spot

package display

import (
	"image"
	"image/draw"
	"slices"

	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// The Bluetooth page, opened from Connections in the settings: a title, a line saying what to do,
// the devices as rows a finger can hit, and a bar at the bottom that ends it. The one connected and
// the ones paired come first, then what the scan has found. Geometry is shared with the gesture
// handler, which maps a tap back to a row.
// The panel is 960 by 480 in landscape: title, hint, four rows, and the bar fit in 480.
const (
	btRowTop    = 112 // first row's top edge
	btRowHeight = 58
	btRows      = 4
	btDoneBar   = 64 // the bottom bar's height
)

// btRowAt maps a tap to the row it landed on, or -1; the bottom bar is btRows.
func (r *renderer) btRowAt(y int) int {
	if y >= r.h-btDoneBar {
		return btRows
	}
	if y < btRowTop {
		return -1
	}
	row := (y - btRowTop) / btRowHeight
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
	r.text(r.body, "Bluetooth audio", r.margin, 52, amber)
	t := clockHM(s.now)
	r.text(r.small, t, r.w-r.margin-r.width(r.small, t), 52, dim)
	hint := s.bt.Status
	if hint == "" {
		hint = "Put your earbuds in pairing mode"
	}
	r.text(r.tiny, hint, r.margin, 92, dim)

	if len(s.bt.Devices) == 0 {
		// A dot that walks while the scan runs, so a still list reads as searching rather than stuck.
		n := int(s.now.UnixMilli() / 400 % 4)
		r.text(r.small, i18n.Sprintf("Searching%s", "..."[:n]), r.margin, btRowTop+38, dim)
	}
	for i, d := range btList(s.bt.Devices) {
		if i >= btRows {
			break
		}
		top := btRowTop + i*btRowHeight
		draw.Draw(r.dst, image.Rect(r.margin, top+btRowHeight-2, r.w-r.margin, top+btRowHeight), image.NewUniform(ember), image.Point{}, draw.Src)
		c := cream
		if d.Busy {
			c = amber
		}
		r.text(r.small, d.Name, r.margin, top+40, c)
		var right string
		switch {
		case d.Busy:
			right = "working…"
		case d.Connected:
			right = "connected · tap to disconnect"
		case d.Paired:
			right = "paired · tap to connect"
		default:
			right = "tap to pair"
		}
		r.text(r.tiny, right, r.w-r.margin-r.width(r.tiny, right), top+38, dim)
	}

	// The bar that ends it.
	top := r.h - btDoneBar
	draw.Draw(r.dst, image.Rect(0, top, r.w, r.h), image.NewUniform(ember), image.Point{}, draw.Src)
	label := "Done"
	r.text(r.body, label, (r.w-r.width(r.body, label))/2, top+45, cream)
}
