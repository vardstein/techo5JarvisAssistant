//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"math"
	"strings"

	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wifi"
)

// The Wi-Fi pages: a list of the networks the radio hears with a Connect button each, and a
// keyboard for the passphrase. They open from the Connections card, and on their own when a device has
// no address a while after boot — a fresh unit, or one carried to another house.

// wifiState is what the pages show.
type wifiState struct {
	status   wifi.Status
	nets     []wifi.Network
	scanning bool
	page     int
	pick     *wifi.Network // the network being joined; nil is the list
	text     string        // the passphrase so far
	shift    bool
	symbols  bool
	busy     string // "Connecting to X…" while a join runs
	err      string // why the last join failed
}

// The sizes below are the ones this page was drawn at on a Show 5. Everything that is a length goes
// through paint.s, so the same layout comes out right on the Show 8's wider panel; wifiRows and
// keyRows are counts of things rather than lengths, so they stay as they are.
const (
	wifiRowTopBase    = 106
	wifiRowHeightBase = 58
	wifiRows          = 6

	keyTopBase    = 150
	keyHBase      = 62
	keyGapBase    = 6
	keyWBase      = 88
	keyRows       = 4
	keyMarginBase = 13
)

func (p *paint) wifiRowTop() int    { return p.s(wifiRowTopBase) }
func (p *paint) wifiRowHeight() int { return p.s(wifiRowHeightBase) }
func (p *paint) keyTop() int        { return p.s(keyTopBase) }
func (p *paint) keyH() int          { return p.s(keyHBase) }
func (p *paint) keyGap() int        { return p.s(keyGapBase) }
func (p *paint) keyW() int          { return p.s(keyWBase) }
func (p *paint) keyMargin() int     { return p.s(keyMarginBase) }

var (
	keyRowsLetters = []string{"qwertyuiop", "asdfghjkl", "zxcvbnm"}
	keyRowsSymbols = []string{"1234567890", "-_.,!?@#/", "$%&*()'~+"}
)

// wifiHit is what a tap on the network list landed on.
type wifiHit struct {
	row    int // a network row, or -1
	rescan bool
	done   bool
}

func (r *renderer) wifiListHit(x, y int) wifiHit {
	h := wifiHit{row: -1}
	top, rowH := r.wifiRowTop(), r.wifiRowHeight()
	switch {
	case r.pageBackAt(x, y):
		h.done = true
	case y < top-r.s(24) && x >= r.w/2:
		h.rescan = true
	case y >= top && (y-top)/rowH < wifiRows:
		h.row = (y - top) / rowH
	}
	return h
}

// keyAt maps a tap on the keyboard page to a key: a character, or "shift", "symbols", "space",
// "backspace", "cancel", "join"; empty for nothing.
func (r *renderer) keyAt(x, y int, symbols bool) string {
	keyTop, keyH, keyGap, keyW, keyMargin := r.keyTop(), r.keyH(), r.keyGap(), r.keyW(), r.keyMargin()
	if y < keyTop {
		return ""
	}
	row := (y - keyTop) / (keyH + keyGap)
	if row >= keyRows {
		return ""
	}
	pitch := keyW + keyGap
	switch row {
	case 0, 1, 2:
		letters := keyRowsLetters[row]
		if symbols {
			letters = keyRowsSymbols[row]
		}
		x0 := keyMargin + (10-len(letters))*pitch/2
		if row == 2 && !symbols {
			// Shift on the left, backspace on the right, the letters between.
			if x >= keyMargin && x < keyMargin+pitch+keyW/2 {
				return "shift"
			}
			if x >= r.w-keyMargin-pitch-keyW/2 {
				return "backspace"
			}
		} else if row == 2 && symbols && x >= r.w-keyMargin-pitch-keyW/2 {
			return "backspace"
		}
		i := (x - x0) / pitch
		if i < 0 || i >= len(letters) || x < x0 {
			return ""
		}
		return string(letters[i])
	default:
		// [symbols] [    space    ] [cancel] [join]
		switch {
		case x < keyMargin+pitch*2:
			return "symbols"
		case x < keyMargin+pitch*6:
			return "space"
		case x < keyMargin+pitch*8:
			return "cancel"
		default:
			return "join"
		}
	}
}

func (r *renderer) wifiPage(s scene) {
	w := s.wifi
	if w.pick != nil {
		r.keyboardPage(s)
		return
	}
	line := "Not connected"
	if w.status.Connected {
		line = i18n.Sprintf("Connected to %s", w.status.SSID)
		if w.status.Address != "" {
			line += "  ·  " + w.status.Address
		}
	} else if w.status.SSID != "" {
		line = w.status.SSID + ": " + w.status.State
	}
	if w.busy != "" {
		line = w.busy
	} else if w.err != "" {
		line = w.err
	}
	r.pageHead("Wi-Fi", line, "Rescan")
	fc := r.faces()

	rowTop, rowH := r.wifiRowTop(), r.wifiRowHeight()
	if len(w.nets) == 0 {
		msg := "No networks heard yet"
		if w.scanning {
			msg = "Scanning" + "..."[:int(s.now.UnixMilli()/400%4)]
		}
		r.text(fc.value, msg, r.margin, rowTop+r.s(30), dim)
	}
	// A whole row is the button: a tap on a locked network opens the keyboard, an open one is joined.
	start, end, more := pageWith(len(w.nets), w.page, wifiRows)
	for i, n := range w.nets[start:end] {
		joined := n.SSID == w.status.SSID && w.status.Connected
		tile := r.pageTile(rowTop+i*rowH, rowH, joined)
		cy := (tile.Min.Y + tile.Max.Y) / 2
		x := r.w - r.margin
		if joined {
			x -= r.width(fc.sub, "Joined")
			r.text(fc.sub, "Joined", x, cy+r.s(7), amber)
			x -= r.s(18)
		}
		x = r.signal(x, cy, signalLevel(n.Signal))
		if n.Secured {
			x = r.lock(x-r.s(12), cy)
		}
		face := fc.value
		if joined {
			face = fc.labelBold
		}
		r.text(face, r.fit(face, n.SSID, x-r.margin-r.s(20)), r.margin, cy+r.s(9), cream)
	}
	if more {
		tile := r.pageTile(rowTop+(wifiRows-1)*rowH, rowH, false)
		cy := (tile.Min.Y + tile.Max.Y) / 2
		r.text(fc.value, "More", r.margin, cy+r.s(9), dim)
		r.chevron(r.w-r.margin-r.s(4), cy)
	}
}

// signal draws a network's strength as four rising bars, level of them lit, ending at right, and
// returns their left edge.
func (r *renderer) signal(right, cy, level int) int {
	bw, gap := r.s(5), r.s(3)
	left := right - 4*bw - 3*gap
	bottom := cy + r.s(9)
	for i := range 4 {
		c := lerp(surface(3), dim, 0.45)
		if i < level {
			c = cream
		}
		x := left + i*(bw+gap)
		r.roundFill(image.Rect(x, bottom-r.s(6+4*i), x+bw, bottom), r.sf(2), c, c)
	}
	return left
}

// lock draws a small padlock ending at right, for a network that wants a password, and returns its
// left edge.
func (r *renderer) lock(right, cy int) int {
	k := r.sf(100) / 100
	body := image.Rect(right-r.s(14), cy-r.s(1), right, cy+r.s(10))
	r.roundFill(body, 2.5*k, dim, dim)
	r.aaRing(float64(body.Min.X+body.Max.X)/2, float64(body.Min.Y), 4.5*k, 2.2*k, math.Pi, 2*math.Pi, dim)
	return body.Min.X
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

// pageWith is pageOf for a page of rows rows: the list's slice, and whether a More row is needed.
func pageWith(n, page, rows int) (start, end int, more bool) {
	if n <= rows {
		return 0, n, false
	}
	per := rows - 1
	pages := (n + per - 1) / per
	page %= pages
	start = page * per
	end = start + per
	if end > n {
		end = n
	}
	return start, end, true
}

func (r *renderer) keyboardPage(s scene) {
	w := s.wifi
	keyTop, keyH, keyGap, keyW, keyMargin := r.keyTop(), r.keyH(), r.keyGap(), r.keyW(), r.keyMargin()
	fc := r.faces()
	r.fill(r.dst.Rect, walnut)
	title := i18n.Sprintf("Password for %s", w.pick.SSID)
	r.text(fc.header, r.fit(fc.header, title, r.w-2*r.margin), r.margin, r.s(46), cream)
	// The field, a soft box ringed in the accent while it takes the keys.
	field := image.Rect(r.margin-r.s(14), r.s(64), r.w-r.margin+r.s(14), r.s(128))
	rad := r.sf(14)
	r.roundFill(field, rad, surface(2), surface(2))
	ring := amber
	shown := w.text
	if w.busy != "" {
		shown, ring = w.busy, ember
	} else if w.err != "" {
		shown, ring = w.err, danger
	}
	r.roundStroke(field, rad, r.sf(2), ring)
	for r.width(r.small, shown+"|") > field.Dx()-r.s(44) && len(shown) > 1 {
		shown = shown[1:]
	}
	c := cream
	if w.busy != "" || w.err != "" {
		c = dim
	} else {
		shown += "|"
	}
	r.text(r.small, shown, r.margin, r.s(108), c)

	pitch := keyW + keyGap
	for row := 0; row < 3; row++ {
		letters := keyRowsLetters[row]
		if w.symbols {
			letters = keyRowsSymbols[row]
		}
		x0 := keyMargin + (10-len(letters))*pitch/2
		y0 := keyTop + row*(keyH+keyGap)
		for i, ch := range letters {
			label := string(ch)
			if w.shift && !w.symbols {
				label = strings.ToUpper(label)
			}
			r.key(image.Rect(x0+i*pitch, y0, x0+i*pitch+keyW, y0+keyH), label, false)
		}
		if row == 2 {
			if !w.symbols {
				r.key(image.Rect(keyMargin, y0, x0-keyGap, y0+keyH), "Shift", w.shift)
			}
			r.key(image.Rect(x0+len(letters)*pitch, y0, r.w-keyMargin, y0+keyH), "Del", false)
		}
	}
	y0 := keyTop + 3*(keyH+keyGap)
	sym := "123"
	if w.symbols {
		sym = "abc"
	}
	r.key(image.Rect(keyMargin, y0, keyMargin+pitch*2-keyGap, y0+keyH), sym, w.symbols)
	r.key(image.Rect(keyMargin+pitch*2, y0, keyMargin+pitch*6-keyGap, y0+keyH), "space", false)
	r.key(image.Rect(keyMargin+pitch*6, y0, keyMargin+pitch*8-keyGap, y0+keyH), "Cancel", false)
	r.key(image.Rect(keyMargin+pitch*8, y0, r.w-keyMargin, y0+keyH), "Connect", true)
}

// key draws one key: a flat soft box, or filled in the accent when lit.
func (r *renderer) key(rect image.Rectangle, label string, lit bool) {
	rad := r.sf(10)
	ink := cream
	if lit {
		r.roundFill(rect, rad, amber, amber)
		ink = onAccent()
	} else {
		r.roundFill(rect, rad, surface(4), surface(4))
	}
	face := r.small
	if r.width(face, label) > rect.Dx()-r.s(10) {
		face = r.tiny
	}
	r.text(face, label, rect.Min.X+(rect.Dx()-r.width(face, label))/2, rect.Min.Y+rect.Dy()/2+r.s(12), ink)
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
