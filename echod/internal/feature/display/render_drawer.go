//go:build !dot && !spot

package display

import (
	"image"
	"strconv"
	"strings"

	"golang.org/x/image/font"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// The drawer: a swipe in from the right edge brings Cameras, Radio, Announce and Call. They are things
// to use rather than settings, so they are a shelf, the way the Car Thing showed what it could play: the
// tabs along the top, and a row of large tiles under them that slides sideways.

const (
	// drawerEdgeBase is how far from the right edge a leftward swipe has to start to open the drawer.
	drawerEdgeBase = 240

	// The shelf's sizes, in the Car Thing's units (cz).
	shelfBand = 112 // the band with the back arrow and the tabs
	shelfTile = 240 // a tile's picture, square, when the panel is tall enough for it
	shelfGap  = 32  // between tiles

	drawerCameras  = 0
	drawerRadio    = 1
	drawerAnnounce = 2
	drawerCall     = 3
)

func (p *paint) drawerEdge() int { return p.s(drawerEdgeBase) }

var drawerTabs = []string{"Cameras", "Radio", "Announce", "Call"}

// drawer draws the drawer: the shelf, or a list of choices opened from it.
func (r *renderer) drawer(s scene) {
	r.pending = r.pending[:0]
	r.fill(r.dst.Rect, walnut)
	maxScroll, pickMax := 0, 0
	p, picking := pickerFor(s.drawerPick, s.view())
	if s.drawerPick != "" && picking {
		pickMax = r.carPicker(p, s.pickScroll)
	} else {
		maxScroll = r.shelf(s)
	}
	r.zmu.Lock()
	r.zones, r.pending = r.pending, r.zones
	r.cardMax, r.pickMax = maxScroll, pickMax
	r.zmu.Unlock()
}

// shelfStep is how far one swipe slides the shelf: a tile and the gap after it.
func (r *renderer) shelfStep() int { return r.shelfArt() + r.cz(shelfGap) }

// shelfArt is a tile's picture at this panel and menu size: as large as the Car Thing's, or as large
// as leaves room under it for two lines of name and one of what it is doing.
func (r *renderer) shelfArt() int {
	cf := r.car()
	text := r.cz(16) + 2*r.lineH(cf.pill) + r.cz(4) + r.lineH(cf.small)
	return min(r.cz(shelfTile), r.h-r.cz(shelfBand)-r.cz(24)-text-r.cz(16))
}

// lineH is the distance from one line of face to the next.
func (r *renderer) lineH(face font.Face) int { return face.Metrics().Height.Ceil() }

// shelf is the tabs and the open tab's tiles, scrolled by s.drawerScroll. It returns how far they scroll.
func (r *renderer) shelf(s scene) int {
	cf := r.car()
	band := r.cz(shelfBand)

	// The back arrow puts the drawer away; the tabs are words, the open one lit and underlined.
	in, arrow := r.cz(40), r.cz(48)
	r.mdiIcon("arrow-left", in, band/2-arrow/2, zb(48), cream)
	r.addZone(zone{r: image.Rect(0, 0, in+arrow+r.cz(16), band), kind: zoneBack})
	x, gap := in+arrow+r.cz(32), r.cz(40)
	base := mid(cf.pill, band/2)
	for i, name := range drawerTabs {
		w := r.width(cf.pill, name)
		ink := lerp(walnut, cream, 0.5)
		if i == s.drawerTab {
			ink = cream
			r.fill(image.Rect(x, base+r.cz(10), x+w, base+r.cz(14)), amber)
		}
		r.text(cf.pill, name, x, base, ink)
		// A tap anywhere up to halfway into the gaps either side picks it.
		r.addZone(zone{r: image.Rect(max(x-gap/2, 0), 0, min(x+w+gap/2, r.w), band), kind: zoneTab, opt: i})
		x += w + gap
	}

	rows, note := drawerRows(s)
	x0 := r.cz(carIn)
	if len(rows) == 0 {
		y := band + r.cz(48)
		for _, line := range r.lines(cf.body, note, r.w-2*x0, 6) {
			r.text(cf.body, line, x0, mid(cf.body, y), softInk())
			y += r.cz(44)
		}
		return 0
	}

	// Rows with nothing to tap that are not the list's name are what the tab has to say instead of
	// tiles: still asking, or nothing found. They go after the tiles there are.
	var tiles []settingRow
	var notes []settingRow
	for _, row := range rows {
		if row.id == "" && row.label != "Stations" {
			notes = append(notes, row)
			continue
		}
		tiles = append(tiles, row)
	}

	art, step := r.shelfArt(), r.shelfStep()
	text := r.cz(16) + 2*r.lineH(cf.pill) + r.cz(4) + r.lineH(cf.small)
	top := band + max((r.h-band-art-text)/2-r.cz(8), r.cz(8))
	width := len(tiles)*step - r.cz(shelfGap) + 2*x0
	noteW := 0
	if len(notes) > 0 {
		noteW = r.cz(400)
		width += noteW
	}
	maxScroll := max(width-r.w, 0)
	scroll := min(max(s.drawerScroll, 0), maxScroll)

	shelf := image.Rect(0, band, r.w, r.h)
	from := len(r.pending)
	for i, row := range tiles {
		left := x0 + i*step - scroll
		if left >= r.w || left+art <= 0 {
			continue
		}
		r.shelfItem(row, left, top, art)
		if row.id != "" && row.kind != ctlValue || row.id == "radiosource" {
			r.addZone(zone{r: image.Rect(left-r.cz(8), top-r.cz(8), left+art+r.cz(8), top+art+text), kind: zoneRow, id: row.id})
		}
	}
	if len(notes) > 0 {
		nx := x0 + len(tiles)*step - scroll
		ny := top + r.cz(24)
		for _, n := range notes {
			for _, line := range r.lines(cf.label, n.label, noteW, 3) {
				r.text(cf.label, line, nx, mid(cf.label, ny), cream)
				ny += r.cz(44)
			}
			for _, line := range r.lines(cf.body, n.sub, noteW, 3) {
				r.text(cf.body, line, nx, mid(cf.body, ny), softInk())
				ny += r.cz(40)
			}
		}
	}
	r.clipZones(from, shelf)

	// The shelf runs out into the ground at the side there is more of it.
	if scroll > 0 {
		r.sideFade(0, x0, band, r.h, false)
	}
	if scroll < maxScroll {
		r.sideFade(r.w-x0, r.w, band, r.h, true)
	}
	return maxScroll
}

// shelfItem is one thing on the shelf: its picture, square at left, top, its name under it, and under that
// what it is doing, in the accent when it is going on now.
func (r *renderer) shelfItem(row settingRow, left, top, art int) {
	cf := r.car()
	live := row.bold || row.value == "Listening"
	b := image.Rect(left, top, left+art, top+art)
	hi, lo := lerp(walnut, cream, 0.12), lerp(walnut, cream, 0.06)
	if live {
		hi, lo = lerp(walnut, amber, 0.42), lerp(walnut, amber, 0.24)
	}
	r.roundFill(b, float64(r.cz(8)), hi, lo)
	// The icon is the Car Thing's 96 on a full-sized tile, and shrinks with a smaller one.
	full := r.cz(shelfTile)
	icon := r.cz(96) * art / full
	ink := softInk()
	if live {
		ink = cream
	}
	r.mdiIcon(tileIcon(row), left+(art-icon)/2, top+(art-icon)/2, zb(96)*art/full, ink)

	y := top + art + r.cz(16)
	name := r.lines(cf.pill, row.label, art, 2)
	for _, line := range name {
		r.text(cf.pill, line, left, y+cf.pill.Metrics().Ascent.Ceil(), cream)
		y += r.lineH(cf.pill)
	}
	sub := row.sub
	switch {
	case row.label == "Stations":
		sub = row.value
	case row.value == "Listening":
		sub = row.value
	case strings.HasPrefix(row.id, "cam:"):
		// The voice hint under a camera's name is longer than a tile is wide.
		sub = ""
	}
	if sub == "" {
		return
	}
	subInk := softInk()
	if live {
		subInk = amber
	}
	// What it is doing has the second line of the name when the name did not need it.
	y += r.cz(4)
	for _, line := range r.lines(cf.small, sub, art, 3-len(name)) {
		r.text(cf.small, line, left, y+cf.small.Metrics().Ascent.Ceil(), subInk)
		y += r.lineH(cf.small)
	}
}

// tileIcon is the picture on a tile: what kind of thing it is.
func tileIcon(row settingRow) string {
	kind, _, _ := strings.Cut(row.id, ":")
	switch {
	case row.label == "Stations":
		return "radio-tower"
	case kind == "cam":
		return "cctv"
	case kind == "st" && row.button == "Stop":
		return "stop-circle-outline"
	case kind == "st":
		return "radio"
	case kind == "call" && row.sub == "Phone":
		return "account"
	case kind == "call":
		return "speaker-wireless"
	case kind == "announce":
		return "microphone"
	}
	return "radio"
}

// sideFade lets the shelf run out into the ground between x0 and x1, solid at x0 when right is false
// and at x1 when it is true.
func (r *renderer) sideFade(x0, x1, y0, y1 int, right bool) {
	x0, x1, y0, y1 = max(x0, 0), min(x1, r.w), max(y0, 0), min(y1, r.h)
	g := walnut
	for x := x0; x < x1; x++ {
		t := float64(x-x0) / float64(max(x1-x0, 1))
		if !right {
			t = 1 - t
		}
		k := uint32(t * t * 256)
		for y := y0; y < y1; y++ {
			i := y*r.dst.Stride + x*4
			px := r.dst.Pix[i : i+3 : i+3]
			px[0] = uint8((uint32(px[0])*(256-k) + uint32(g.R)*k) >> 8)
			px[1] = uint8((uint32(px[1])*(256-k) + uint32(g.G)*k) >> 8)
			px[2] = uint8((uint32(px[2])*(256-k) + uint32(g.B)*k) >> 8)
		}
	}
}

// drawerRows are the open tab's rows, and a note to show when it has none.
func drawerRows(s scene) ([]settingRow, string) {
	if s.drawerTab == drawerAnnounce {
		return announceRows(s)
	}
	if s.drawerTab == drawerCall {
		return callRows(s)
	}
	if s.drawerTab == drawerCameras {
		if len(s.cameras) == 0 {
			return nil, "No cameras yet. Set a Home Assistant token to list them all, or pick some with the home_cameras action."
		}
		var rows []settingRow
		for i, c := range s.cameras {
			name := c.Name
			if s.demo {
				// A camera's name can say whose room it looks into.
				name = demoCameras[i%len(demoCameras)]
			}
			rows = append(rows, settingRow{id: "cam:" + strconv.Itoa(i), label: name,
				sub: i18n.F("Or say \"show {name}\"", "name", strings.ToLower(name)), kind: ctlButton, rowTap: true})
		}
		return rows, ""
	}

	rd := s.radio
	if !rd.Configured {
		return nil, "No stations yet. Give the device a Home Assistant token (the home_assistant action) " +
			"for local and popular stations, or add your own with home_radio."
	}
	source := settingRow{label: "Stations", kind: ctlValue, value: home.SourceLabel(rd.Source)}
	if rd.Sources > 1 {
		source.id, source.kind = "radiosource", ctlChoice
	}
	rows := []settingRow{source}
	stations := radioList(rd)
	switch {
	case rd.Loading:
		return append(rows, settingRow{label: "Asking Home Assistant for stations…", kind: ctlValue}), ""
	case len(stations) == 0 && rd.Problem != "":
		return append(rows, settingRow{label: "Could not list the stations", sub: rd.Problem, kind: ctlValue}), ""
	case len(stations) == 0 && rd.Source == config.RadioLocal:
		return append(rows, settingRow{label: "No stations within 100 km", sub: "Try Popular worldwide", kind: ctlValue}), ""
	case len(stations) == 0:
		return append(rows, settingRow{label: "No stations in this list yet", kind: ctlValue}), ""
	}
	for i, name := range stations {
		id := "st:" + strconv.Itoa(i)
		if name == "■ Stop" {
			// A stop in a grouped room stops the group, so the row says so rather than doing something
			// bigger than its words. The label stays what it is: what changes is who else it reaches.
			row := settingRow{id: id, label: "Stop the radio", kind: ctlButton, button: "Stop", rowTap: true}
			if rd.Grouped {
				row.sub = "all rooms in the group"
			}
			rows = append(rows, row)
			continue
		}
		label := stationLabel(name)
		if s.demo {
			// Call letters and local station names say where the owner lives.
			label = "Station " + strconv.Itoa(i+1)
		}
		row := settingRow{id: id, label: label, kind: ctlButton, rowTap: true}
		switch {
		case name == rd.Now && rd.Playing:
			row.bold, row.sub = true, "Playing now"
		case name == rd.Chosen && rd.Chosen != rd.Now:
			row.sub = "Starting…"
		}
		rows = append(rows, row)
	}
	return rows, ""
}

// callRows is the Call tab: the other devices in the house, then the phone's contacts.
func callRows(s scene) ([]settingRow, string) {
	if len(s.callees) == 0 {
		return nil, "Nobody to call yet. Give every device the same house word on its setup page to call " +
			"between rooms, or sign the phone in (the phone_account action) for your contacts."
	}
	var rows []settingRow
	for i, c := range s.callees {
		name, sub := c.Name, "Phone"
		if c.Device {
			sub = "Intercom"
		}
		if s.demo {
			// A device's name can say whose room it is, and a contact's is somebody's name.
			if c.Device {
				name = demoRooms[i%len(demoRooms)]
			} else {
				name = "Contact " + strconv.Itoa(i+1)
			}
		}
		rows = append(rows, settingRow{id: "call:" + strconv.Itoa(i), label: name, sub: sub,
			kind: ctlButton, rowTap: true})
	}
	return rows, ""
}

// demoRooms stand in for the devices' names in screenshots that will be published.
var demoRooms = []string{"Kitchen", "Office", "Living room", "Bedroom", "Garage"}

// demoCameras stand in for the owner's camera names in screenshots that will be published.
var demoCameras = []string{"Front door", "Driveway", "Backyard", "Porch", "Garage"}
