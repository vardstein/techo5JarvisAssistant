//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"image/color"
	"strings"

	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wifi"
)

// The Wi-Fi pages: a list of the networks the radio hears, a row each, and a keyboard for the
// passphrase. They open from the Connections card, and on their own when a device has
// no address a while after boot — a fresh unit, or one carried to another house.

// wifiState is what the pages show.
type wifiState struct {
	status   wifi.Status
	nets     []wifi.Network
	scanning bool
	scroll   int
	pick     *wifi.Network // the network being joined; nil is the list
	text     string        // the passphrase so far
	shift    bool
	symbols  bool
	busy     string // "Connecting to X…" while a join runs
	err      string // why the last join failed
}

var (
	keyRowsLetters = []string{"qwertyuiop", "asdfghjkl", "zxcvbnm"}
	keyRowsSymbols = []string{"1234567890", "-_.,!?@#/", "$%&*()'~+"}
)

// The keyboard's sizes. Keys are a finger's size whatever the menu size, so they scale with the
// panel only; the band over them follows the menu size like the rest of the settings.
const (
	keySide  = 12 // from the panel's edge to the outer keys
	keyGap   = 8
	keyTall  = 72 // at most
	keyField = 72 // the passphrase field's height
)

// wifiPage is the Wi-Fi pages: the networks the radio hears, or the keyboard for one's passphrase.
func (r *renderer) wifiPage(s scene) {
	r.pending = r.pending[:0]
	r.fill(r.dst.Rect, walnut)
	maxScroll := 0
	if s.wifi.pick != nil {
		r.keyboardPage(s.wifi)
	} else {
		maxScroll = r.wifiList(s)
	}
	r.showZones(maxScroll)
}

// wifiLine is how the connection stands, said over the list, and its ink: a join under way in the
// accent, one that failed in red.
func wifiLine(w wifiState) (string, color.RGBA) {
	switch {
	case w.busy != "":
		return w.busy, amber
	case w.err != "":
		return w.err, danger
	case w.status.Connected && w.status.Address != "":
		return i18n.Sprintf("Connected to %s", w.status.SSID) + "  ·  " + w.status.Address, subInk()
	case w.status.Connected:
		return i18n.Sprintf("Connected to %s", w.status.SSID), subInk()
	case w.status.SSID != "":
		return w.status.SSID + ": " + w.status.State, subInk()
	}
	return "Not connected", subInk()
}

// wifiList is the networks as large rows under the header: the strength and a lock at the left, the
// name, and the one joined in the accent. A tap on a locked one opens the keyboard; an open one is
// joined. It returns how far the list can scroll.
func (r *renderer) wifiList(s scene) int {
	w := s.wifi
	cf := r.car()
	head, rowH := r.cz(carHeader), r.cz(carRow)
	list := image.Rect(0, head, r.w, r.h)
	x0, x1 := r.cz(carIn), r.w-r.cz(carIn)

	line, lineInk := wifiLine(w)
	intro := r.lines(cf.small, line, x1-x0, 2)
	total := len(intro)*r.cz(40) + max(len(w.nets), 1)*rowH
	maxScroll := max(total+r.cz(24)-list.Dy(), 0)
	scroll := min(max(w.scroll, 0), maxScroll)

	y := head - scroll
	for _, l := range intro {
		r.text(cf.small, l, x0, y+r.cz(28), lineInk)
		y += r.cz(40)
	}
	if len(w.nets) == 0 {
		msg := "No networks heard yet"
		if w.scanning {
			msg = i18n.T("Scanning") + "..."[:int(s.now.UnixMilli()/400%4)]
		}
		r.text(cf.value, msg, x0, mid(cf.value, y+rowH/2), softInk())
	}
	from := len(r.pending)
	icon := r.cz(48)
	tx := x0 + icon + r.cz(32)
	for _, n := range w.nets {
		if y < r.h && y+rowH > head {
			cy := y + rowH/2
			joined := w.status.Connected && n.SSID == w.status.SSID
			ink, right := cream, x1
			switch {
			case joined:
				ink = amber
				right -= r.width(cf.value, "Connected")
				r.text(cf.value, "Connected", right, mid(cf.value, cy), amber)
				right -= r.cz(24)
			case n.Secured:
				right = r.carChevron(x1, cy) - r.cz(16)
			}
			r.mdiIcon(wifiIcon(n), x0, cy-icon/2, zb(48), ink)
			r.text(cf.label, r.fit(cf.label, n.SSID, right-tx), tx, mid(cf.label, cy), ink)
			r.addZone(zone{r: image.Rect(0, y, r.w, y+rowH), kind: zoneRow, id: n.SSID})
		}
		y += rowH
	}
	r.clipZones(from, list)
	r.listFades(list, scroll, maxScroll)
	r.carHeader("Wi-Fi", []headerAction{{id: "rescan", label: "Rescan"}})
	return maxScroll
}

// wifiIcon is a network's strength as the Wi-Fi icon with that many arcs, locked when it wants a
// password.
func wifiIcon(n wifi.Network) string {
	name := fmt.Sprintf("wifi-strength-%d", signalLevel(n.Signal))
	if n.Secured {
		name += "-lock"
	}
	return name
}

// signalLevel is a signal strength as bars out of four.
func signalLevel(dbm int) int {
	switch {
	case dbm >= -55:
		return 4
	case dbm >= -70:
		return 3
	case dbm >= -80:
		return 2
	}
	return 1
}

// keyboardPage is the passphrase keyboard: the way back and the network's name, the field, and the
// keys under it, filling what is left of the panel. Each key is a zone named "key:" and what it
// types or does (wifiKey).
func (r *renderer) keyboardPage(w wifiState) {
	cf := r.car()

	// The band: back (which is Cancel) and whose password it is.
	band := r.cz(88)
	in, arrow := r.cz(40), r.cz(48)
	r.mdiIcon("arrow-left", in, band/2-arrow/2, zb(48), cream)
	tx := in + arrow + r.cz(24)
	title := r.fit(cf.label, i18n.Sprintf("Password for %s", w.pick.SSID), r.w-r.cz(carIn)-tx)
	r.text(cf.label, title, tx, mid(cf.label, band/2), cream)
	r.addZone(zone{r: image.Rect(0, 0, min(tx+r.width(cf.label, title)+r.cz(32), r.w), band), kind: zoneBack})

	// The field: what has been typed and the caret, or how the join is going.
	fh := r.cz(keyField)
	field := image.Rect(r.cz(carIn), band, r.w-r.cz(carIn), band+fh)
	r.roundFill(field, float64(fh)/2, lerp(walnut, cream, 0.1), lerp(walnut, cream, 0.1))
	pad := r.cz(32)
	room := field.Dx() - 2*pad
	shown, ink := w.text, cream
	switch {
	case w.busy != "":
		shown, ink = r.fit(cf.value, w.busy, room), amber
	case w.err != "":
		shown, ink = r.fit(cf.value, w.err, room), danger
	default:
		// The end of a long passphrase, where the typing is.
		for len(shown) > 0 && r.width(cf.value, shown) > room-r.cz(8) {
			shown = shown[1:]
		}
	}
	cy := band + fh/2
	r.text(cf.value, shown, field.Min.X+pad, mid(cf.value, cy), ink)
	if w.busy == "" && w.err == "" {
		x := field.Min.X + pad + r.width(cf.value, shown) + r.cz(4)
		h := r.cz(40)
		r.fill(image.Rect(x, cy-h/2, x+max(r.cz(3), 2), cy+h/2), amber)
	}

	// The keys, as tall as fit under the field.
	side, gap := r.s(keySide), r.s(keyGap)
	top := band + fh + r.cz(16)
	keyH := min(r.s(keyTall), (r.h-r.s(keySide)-top-3*gap)/4)
	keyW := (r.w - 2*side - 9*gap) / 10
	pitch := keyW + gap
	span := func(x, y, units int) image.Rectangle {
		return image.Rect(x, y, x+units*pitch-gap, y+keyH)
	}
	for row := range 3 {
		letters := keyRowsLetters[row]
		if w.symbols {
			letters = keyRowsSymbols[row]
		}
		x0 := side + (10-len(letters))*pitch/2
		if row == 2 && w.symbols {
			x0 = side // no Shift here, so Del can have a whole key
		}
		y := top + row*(keyH+gap)
		for i, ch := range letters {
			label := string(ch)
			if w.shift && !w.symbols {
				label = strings.ToUpper(label)
			}
			r.keyCap(image.Rect(x0+i*pitch, y, x0+i*pitch+keyW, y+keyH), "key:"+string(ch), label, "", keyPlain)
		}
		if row == 2 {
			if !w.symbols {
				icon, look := "arrow-up-bold-outline", keyPlain
				if w.shift {
					icon, look = "arrow-up-bold", keyLit
				}
				r.keyCap(image.Rect(side, y, x0-gap, y+keyH), "key:shift", "", icon, look)
			}
			r.keyCap(image.Rect(x0+len(letters)*pitch, y, r.w-side, y+keyH), "key:backspace", "", "backspace-outline", keyPlain)
		}
	}
	y := top + 3*(keyH+gap)
	sym, look := "123", keyPlain
	if w.symbols {
		sym, look = "abc", keyLit
	}
	r.keyCap(span(side, y, 2), "key:symbols", sym, "", look)
	r.keyCap(span(side+2*pitch, y, 5), "key:space", "space", "", keyQuiet)
	join := keySolid
	if w.busy != "" {
		join = keyPlain
	}
	r.keyCap(image.Rect(side+7*pitch, y, r.w-side, y+keyH), "key:join", "Connect", "", join)
}

// How a key looks: a faint cap, one switched on in the accent, the one that finishes in the text
// color, and the space bar, whose word is quieter than a letter.
type keyLook int

const (
	keyPlain keyLook = iota
	keyLit
	keySolid
	keyQuiet
)

// keyCap draws a key in b, its label or icon centred, and makes it the zone id.
func (r *renderer) keyCap(b image.Rectangle, id, label, icon string, look keyLook) {
	cf := r.car()
	fill, ink, face := lerp(walnut, cream, 0.14), cream, cf.pill
	switch look {
	case keyLit:
		fill, ink = amber, onAccent()
	case keySolid:
		fill, ink = cream, walnut
	case keyQuiet:
		ink, face = softInk(), cf.small
	}
	r.roundFill(b, float64(r.s(12)), fill, fill)
	cx, cy := (b.Min.X+b.Max.X)/2, (b.Min.Y+b.Max.Y)/2
	if icon != "" {
		size := r.s(36)
		r.mdiIcon(icon, cx-size/2, cy-size/2, 36, ink)
	} else {
		label = r.fit(face, label, b.Dx()-r.s(8))
		r.text(face, label, cx-r.width(face, label)/2, mid(face, cy), ink)
	}
	// The zone reaches halfway into the gaps, so a tap between two keys lands on the nearer.
	r.addZone(zone{r: b.Inset(-r.s(keyGap) / 2), kind: zoneAction, id: id})
}

// wifiSummary is a line for the connection.
func wifiSummary(st wifi.Status) string {
	switch {
	case st.Connected && st.Address != "":
		return fmt.Sprintf("%s  ·  %s", st.SSID, st.Address)
	case st.Connected:
		return st.SSID + "  ·  no address yet"
	case st.SSID != "":
		return st.SSID + "  ·  " + st.State
	}
	return "not connected"
}
