//go:build !dot && !spot

package display

import (
	"image"
	"slices"

	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// The Bluetooth page, opened from Connections in the settings: the header with its back arrow, a
// line saying what to do, and the devices as large rows, each saying what a tap on it does. The one
// connected and the ones paired come first, then what the scan has found.

// btRowHigh is a device's row: its name and the line under it.
const btRowHigh = 112

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

// btSub is the line under a device: what it is and what a tap does.
func btSub(d btaudio.Device) string {
	switch {
	case d.Busy:
		return "Working…"
	case d.Connected:
		return "Connected · tap to disconnect"
	case d.Paired:
		return "Paired · tap to connect"
	}
	return "Tap to pair"
}

func (r *renderer) pairingPage(s scene) {
	cf := r.car()
	r.pending = r.pending[:0]
	r.fill(r.dst.Rect, walnut)
	head, rowH := r.cz(carHeader), r.cz(btRowHigh)
	list := image.Rect(0, head, r.w, r.h)
	x0, x1 := r.cz(carIn), r.w-r.cz(carIn)

	hint := s.bt.Status
	if hint == "" {
		hint = "Put your earbuds in pairing mode"
	}
	intro := r.lines(cf.small, hint, x1-x0, 2)
	devices := btList(s.bt.Devices)
	total := len(intro)*r.cz(40) + max(len(devices), 1)*rowH
	maxScroll := max(total+r.cz(24)-list.Dy(), 0)
	scroll := min(max(s.btScroll, 0), maxScroll)

	y := head - scroll
	for _, l := range intro {
		r.text(cf.small, l, x0, y+r.cz(28), subInk())
		y += r.cz(40)
	}
	if len(devices) == 0 {
		// A dot that walks while the scan runs, so a still list reads as searching rather than stuck.
		n := int(s.now.UnixMilli() / 400 % 4)
		r.text(cf.value, i18n.Sprintf("Searching%s", "..."[:n]), x0, mid(cf.value, y+rowH/2), softInk())
	}
	from := len(r.pending)
	icon := r.cz(48)
	tx := x0 + icon + r.cz(32)
	for _, d := range devices {
		if y < r.h && y+rowH > head {
			cy := y + rowH/2
			ink, subC := cream, subInk()
			switch {
			case d.Busy:
				subC = amber
			case d.Connected:
				ink, subC = amber, amber
			}
			glyph := "bluetooth"
			if d.Connected {
				glyph = "bluetooth-audio"
			}
			r.mdiIcon(glyph, x0, cy-icon/2, zb(48), ink)
			r.text(cf.label, r.fit(cf.label, i18n.Verbatim(d.Name), x1-tx), tx, mid(cf.label, cy-r.cz(17)), ink)
			r.text(cf.small, r.fit(cf.small, btSub(d), x1-tx), tx, mid(cf.small, cy+r.cz(22)), subC)
			r.addZone(zone{r: image.Rect(0, y, r.w, y+rowH), kind: zoneRow, id: d.Address})
		}
		y += rowH
	}
	r.clipZones(from, list)
	r.listFades(list, scroll, maxScroll)
	r.carHeader("Bluetooth audio", nil)
	r.showZones(maxScroll)
}
