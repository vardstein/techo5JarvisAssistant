//go:build !dot && !spot

package display

import (
	"image"
	"image/color"
	"math"
	"sync"

	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/lib/asp"
	"github.com/HuskerMinion/techo5/echod/internal/lib/figtree"
)

// The Show's settings screen, the way Spotify's Car Thing drew its own: one column the width of the
// screen, a level at a time. The categories, then a category's rows, then a page for a group of
// them, a list of choices, or a value on a page of its own; anything that can't be taken back asks
// over all of it. Each level has a back arrow and its title at the top, and a swipe to the right
// goes back too. Large, plain type in Figtree on the theme's ground, no boxes and no rules: the
// accent only where something is on or chosen.
//
// Every size here is the Car Thing's own, in the Show 5's pixels, at the menu size Large; Normal
// and Small draw it all a step smaller (menu_size.go).

// The Car Thing's sizes.
const (
	carHeader  = 144 // the band with the back arrow and the title
	carRootRow = 128 // a category
	carRow     = 96  // a setting, at least
	carIn      = 48  // the column's side margins
	carFade    = 32  // how far the list fades out under the header and at the foot
)

// carFaces are the settings screen's type: Figtree in the Car Thing's sizes.
type carFaces struct {
	title, label, value, big, modal, body, small, pill font.Face
}

var carFonts = sync.OnceValues(func() (bold, regular *opentype.Font) {
	bold, _ = opentype.Parse(figtree.Bold)
	regular, _ = opentype.Parse(figtree.Regular)
	return bold, regular
})

// cz is a Car Thing size on this panel at the menu size in force.
func (r *renderer) cz(n int) int { return (r.s(n)*int(menuZoom.Load()) + 50) / 100 }

// zb is a size given to a part that scales it to the panel itself (mdiIcon), at the menu size.
func zb(n int) int { return (n*int(menuZoom.Load()) + 50) / 100 }

// car is the type at this panel and menu size, made once for each.
func (r *renderer) car() *carFaces {
	key := r.cz(100)
	if f := r.carFc[key]; f != nil {
		return f
	}
	bold, regular := carFonts()
	f := func(fn *opentype.Font, size int) font.Face {
		fc, _ := opentype.NewFace(fn, &opentype.FaceOptions{Size: float64(r.cz(size)), DPI: 72, Hinting: font.HintingFull})
		return fc
	}
	fc := &carFaces{
		title: f(bold, 44), label: f(bold, 36), value: f(regular, 36), big: f(bold, 120),
		modal: f(bold, 52), body: f(regular, 32), small: f(regular, 28), pill: f(bold, 32),
	}
	if r.carFc == nil {
		r.carFc = map[int]*carFaces{}
	}
	r.carFc[key] = fc
	return fc
}

// The column's inks: values at 70% of the text, the lines under labels a little under that.
func softInk() color.RGBA { return lerp(walnut, cream, 0.7) }
func subInk() color.RGBA  { return lerp(walnut, cream, 0.58) }

// mid is the baseline that centres face's capitals on cy.
func mid(face font.Face, cy int) int {
	m := face.Metrics()
	ch := m.CapHeight.Ceil()
	if ch == 0 {
		ch = m.Ascent.Ceil() * 7 / 10
	}
	return cy + ch/2
}

// settingsScreen draws the settings screen at the level open.
func (r *renderer) settingsScreen(s scene) {
	sv := s.view()
	st := sv.st
	r.pending = r.pending[:0]
	r.fill(r.dst.Rect, walnut)
	maxScroll, pickMax := 0, 0

	var v cardView
	if !st.root {
		v = categoryCard(sv)
	}
	pick, picking := pickerFor(st.picker, sv)
	adjust, adjusting := settingRow{}, false
	if st.adjust != "" {
		for _, row := range v.all {
			if row.id == st.adjust {
				adjust, adjusting = row, true
			}
		}
	}
	switch {
	case st.picker != "" && picking:
		pickMax = r.carPicker(pick, st.pickScroll)
	case adjusting:
		r.carAdjust(adjust, st)
	case st.root:
		maxScroll = r.carRoot(st.cardScroll)
	default:
		maxScroll = r.carList(v)
	}
	if st.confirm != "" {
		r.carConfirm(st.confirm)
	}

	r.zmu.Lock()
	r.zones, r.pending = r.pending, r.zones
	r.cardMax, r.pickMax = maxScroll, pickMax
	r.zmu.Unlock()
}

// carHeader is a level's top: the back arrow and title, and the level's buttons at the right. It is
// painted over the list, which scrolls under it.
func (r *renderer) carHeader(title string, actions []headerAction) {
	cf := r.car()
	h := r.cz(carHeader)
	r.fill(image.Rect(0, 0, r.w, h), walnut)
	in, arrow := r.cz(40), r.cz(48)
	r.mdiIcon("arrow-left", in, h/2-arrow/2, zb(48), cream)
	tx := in + arrow + r.cz(24)

	right := r.w - r.cz(carIn)
	for _, a := range actions {
		left := r.carPill(right, h/2, a.label, a.style == btnPrimary)
		r.addZone(zone{r: image.Rect(left-r.cz(8), r.cz(16), right+r.cz(8), h-r.cz(16)), kind: zoneAction, id: a.id})
		right = left - r.cz(16)
	}
	title = r.fit(cf.title, title, right-r.cz(24)-tx)
	r.text(cf.title, title, tx, mid(cf.title, h/2), cream)
	r.addZone(zone{r: image.Rect(0, 0, min(tx+r.width(cf.title, title)+r.cz(32), right), h), kind: zoneBack})
}

// carPill is a button as the Car Thing drew them, its right edge at right: solid in the text color,
// or a faint one for the other choice. It returns where it starts.
func (r *renderer) carPill(right, cy int, label string, solid bool) int {
	cf := r.car()
	h, pad := r.cz(64), r.cz(32)
	left := right - r.width(cf.pill, label) - 2*pad
	b := image.Rect(left, cy-h/2, right, cy+h/2)
	fill, ink := lerp(walnut, cream, 0.14), cream
	if solid {
		fill, ink = cream, walnut
	}
	r.roundFill(b, float64(h)/2, fill, fill)
	r.text(cf.pill, label, left+pad, mid(cf.pill, cy), ink)
	return left
}

// carChevron is the sign a row opens something, its right edge at right.
func (r *renderer) carChevron(right, cy int) int {
	size := r.cz(40)
	r.mdiIcon("chevron-right", right-size, cy-size/2, zb(40), softInk())
	return right - size
}

// fade lets the list run out into the ground over the band from y0 to y1, solid at y0 when down
// is false and at y1 when it is true.
func (r *renderer) fade(y0, y1 int, down bool) {
	y0, y1 = max(y0, 0), min(y1, r.h)
	g := walnut
	for y := y0; y < y1; y++ {
		t := float64(y-y0) / float64(max(y1-y0, 1))
		if !down {
			t = 1 - t
		}
		k := uint32(t * t * 256)
		row := r.dst.Pix[y*r.dst.Stride : y*r.dst.Stride+r.w*4]
		for i := 0; i+3 < len(row); i += 4 {
			row[i] = uint8((uint32(row[i])*(256-k) + uint32(g.R)*k) >> 8)
			row[i+1] = uint8((uint32(row[i+1])*(256-k) + uint32(g.G)*k) >> 8)
			row[i+2] = uint8((uint32(row[i+2])*(256-k) + uint32(g.B)*k) >> 8)
		}
	}
}

// clipZones holds the zones added since from to the list they were drawn in, and drops those
// scrolled out of it.
func (r *renderer) clipZones(from int, list image.Rectangle) {
	kept := r.pending[:from]
	for _, z := range r.pending[from:] {
		if z.r = z.r.Intersect(list); !z.r.Empty() {
			kept = append(kept, z)
		}
	}
	r.pending = kept
}

// The categories' icons on the first level.
var carIcons = [categories]string{"monitor", "volume-high", "alarm", "wifi", "shield-lock-outline", "cog-outline"}

// carRoot is the first level: the categories, a large row each.
func (r *renderer) carRoot(scroll int) int {
	cf := r.car()
	head, rowH := r.cz(carHeader), r.cz(carRootRow)
	list := image.Rect(0, head, r.w, r.h)
	maxScroll := max(int(categories)*rowH+r.cz(16)-list.Dy(), 0)
	scroll = min(max(scroll, 0), maxScroll)
	from := len(r.pending)
	icon := r.cz(64)
	for c := category(0); c < categories; c++ {
		top := head + int(c)*rowH - scroll
		cy := top + rowH/2
		r.mdiIcon(carIcons[c], r.cz(carIn), cy-icon/2, zb(64), cream)
		x := r.cz(160)
		right := r.carChevron(r.w-r.cz(carIn), cy)
		r.text(cf.label, r.fit(cf.label, categoryTitles[c], right-r.cz(24)-x), x, mid(cf.label, cy), cream)
		r.addZone(zone{r: image.Rect(0, top, r.w, top+rowH), kind: zoneCat, cat: c})
	}
	r.clipZones(from, list)
	r.listFades(list, scroll, maxScroll)
	r.carHeader("Settings", nil)
	return maxScroll
}

// listFades fades the list where there is more of it above or below.
func (r *renderer) listFades(list image.Rectangle, scroll, maxScroll int) {
	if scroll > 0 {
		r.fade(list.Min.Y, list.Min.Y+r.cz(carFade), false)
	}
	if scroll < maxScroll {
		r.fade(list.Max.Y-r.cz(carFade), list.Max.Y, true)
	}
}

// carLayout is a row as it will be drawn: its label and the lines under it, what stands at its
// right and how wide that is, and how tall it is.
type carLayout struct {
	label, sub []string
	value      string
	tail       int
	h          int
}

// showsSub is whether a row keeps its line under it: when it says how things stand, or is the
// row's whole point. What a setting is for is left to the setting's name.
func showsSub(row settingRow) bool {
	switch {
	case row.kind == ctlDanger:
		return false
	case row.live, row.bold, row.kind == ctlValue, row.kind == ctlButton:
		return true
	}
	return false
}

func (r *renderer) carLayout(row settingRow, width int) carLayout {
	cf := r.car()
	var l carLayout
	gap := r.cz(24)
	chev := r.cz(40) + r.cz(16)
	half := width / 2
	switch row.kind {
	case ctlToggle:
		l.value = "Off"
		if row.on {
			l.value = "On"
		}
		l.tail = r.width(cf.value, l.value) + chev
	case ctlChoice, ctlPage, ctlStepper:
		l.value = r.fit(cf.value, row.value, half-chev)
		l.tail = chev
		if l.value != "" {
			l.tail += r.width(cf.value, l.value)
		}
		if row.button != "" {
			l.tail += r.width(cf.pill, row.button) + 2*r.cz(24) + r.cz(16)
		}
	case ctlButton:
		l.tail = chev
		if row.button != "" {
			l.tail += r.width(cf.label, row.button)
		}
		if row.value != "" {
			l.value = r.fit(cf.value, row.value, half-l.tail)
			l.tail += r.width(cf.value, l.value) + r.cz(16)
		}
	case ctlValue:
		l.value = r.fit(cf.value, row.value, half-chev)
		l.tail = r.width(cf.value, l.value) + chev
	case ctlDays:
		l.tail = 7*r.cz(56) + 6*r.cz(8)
	case ctlSwatches:
		l.tail = swatchCount*34 - 6 + r.cz(8)
	}
	room := width - l.tail
	if l.tail > 0 {
		room -= gap
	}
	l.label = r.lines(cf.label, row.label, room, 2)
	if row.sub != "" && row.sub != l.value && showsSub(row) { // not when the value says it already
		l.sub = r.lines(cf.small, row.sub, room, 3)
	}
	block := len(l.label)*r.cz(44) + len(l.sub)*r.cz(34)
	l.h = max(r.cz(carRow), block+2*r.cz(22))
	return l
}

// carList is a category or a page: its rows down the whole width under its title.
func (r *renderer) carList(v cardView) int {
	cf := r.car()
	head := r.cz(carHeader)
	list := image.Rect(0, head, r.w, r.h)
	x0, x1 := r.cz(carIn), r.w-r.cz(carIn)

	// An alarm being set says when it will ring, over its rows.
	var intro []string
	if v.actions != nil && v.blurb != "" {
		intro = r.lines(cf.small, v.blurb, x1-x0, 2)
	}
	if len(v.rows) == 0 && v.note != "" {
		intro = r.lines(cf.value, v.note, x1-x0, 4)
	}
	lays := make([]carLayout, len(v.rows))
	total := len(intro) * r.cz(40)
	for i, row := range v.rows {
		lays[i] = r.carLayout(row, x1-x0)
		total += lays[i].h
	}
	maxScroll := max(total+r.cz(24)-list.Dy(), 0)
	scroll := min(max(v.scroll, 0), maxScroll)

	from := len(r.pending)
	y := head - scroll
	for _, line := range intro {
		r.text(cf.small, line, x0, y+r.cz(28), subInk())
		y += r.cz(40)
	}
	for i, row := range v.rows {
		if y < r.h && y+lays[i].h > head {
			r.carRow(row, lays[i], x0, x1, y)
		}
		y += lays[i].h
	}
	r.clipZones(from, list)
	r.listFades(list, scroll, maxScroll)
	r.carHeader(v.title, v.actions)
	return maxScroll
}

// carRow draws one row from top, between x0 and x1, with what a tap on it does.
func (r *renderer) carRow(row settingRow, l carLayout, x0, x1, top int) {
	cf := r.car()
	cy := top + l.h/2
	whole := image.Rect(0, top, r.w, top+l.h)
	add := func(b image.Rectangle, p part) {
		if row.id != "" {
			r.addZone(zone{r: b, kind: zoneRow, id: row.id, part: p})
		}
	}

	ink := cream
	switch {
	case row.kind == ctlDanger:
		ink = danger
	case row.kind == ctlButton && row.button == "":
		ink = amber
	}
	block := len(l.label)*r.cz(44) + len(l.sub)*r.cz(34)
	y := top + (l.h-block)/2
	for _, line := range l.label {
		r.text(cf.label, line, x0, mid(cf.label, y+r.cz(22)), ink)
		y += r.cz(44)
	}
	for _, line := range l.sub {
		r.text(cf.small, line, x0, mid(cf.small, y+r.cz(17)), subInk())
		y += r.cz(34)
	}

	// Values stand in one column, the chevrons beside it, whether or not the row has one.
	right := x1
	switch row.kind {
	case ctlToggle, ctlButton, ctlValue:
		right = x1 - r.cz(40) - r.cz(16)
	}
	value := func(c color.RGBA) {
		if l.value != "" {
			r.text(cf.value, l.value, right-r.width(cf.value, l.value), mid(cf.value, cy), c)
			right -= r.width(cf.value, l.value) + r.cz(16)
		}
	}
	switch row.kind {
	case ctlToggle:
		c := softInk()
		if row.on {
			c = amber
		}
		value(c)
		if row.rowTap {
			add(whole, partRow)
			add(image.Rect(x1-l.tail-r.cz(40), top, r.w, top+l.h), partMain)
			break
		}
		add(whole, partMain)
	case ctlChoice, ctlPage, ctlStepper:
		right = r.carChevron(right, cy) - r.cz(16)
		value(softInk())
		p := partMain
		if row.kind == ctlStepper {
			p = partAdjust
		}
		add(whole, p)
		if row.button != "" {
			left := r.carPill(right, cy, row.button, false)
			add(image.Rect(left-r.cz(8), top, right+r.cz(8), top+l.h), partExtra)
		}
	case ctlButton:
		if row.button != "" {
			w := r.width(cf.label, row.button)
			r.text(cf.label, row.button, right-w, mid(cf.label, cy), amber)
			right -= w + r.cz(16)
		}
		value(softInk())
		add(whole, partMain)
	case ctlDanger:
		add(whole, partConfirm)
	case ctlValue:
		value(softInk())
	case ctlDays:
		w, gap, half := r.cz(56), r.cz(8), r.cz(26)
		left := x1 - 7*w - 6*gap
		for i, name := range []string{"S", "M", "T", "W", "T", "F", "S"} {
			b := image.Rect(left+i*(w+gap), cy-half, left+i*(w+gap)+w, cy+half)
			fill, c := lerp(walnut, cream, 0.14), cream
			if row.days&(1<<i) != 0 {
				fill, c = amber, onAccent()
			}
			r.roundFill(b, float64(half), fill, fill)
			r.text(cf.pill, name, b.Min.X+(w-r.width(cf.pill, name))/2, mid(cf.pill, cy), c)
			if row.id != "" {
				r.addZone(zone{r: image.Rect(b.Min.X-gap/2, top, b.Max.X+gap/2, top+l.h), kind: zoneRow, id: row.id, part: partDay, opt: i})
			}
		}
	case ctlSwatches:
		r.swatchStrip(row, x1, cy, top, top+l.h)
	}
}

// carPicker is a list of choices as a level of its own: the one in force in the accent with a tick.
// A tap off the list, or the back arrow, puts it away.
func (r *renderer) carPicker(p pickerView, scroll int) int {
	cf := r.car()
	r.pending = r.pending[:0]
	r.addZone(zone{r: r.dst.Rect, kind: zoneDismiss})
	head, rowH := r.cz(carHeader), r.cz(carRow)
	list := image.Rect(0, head, r.w, r.h)
	maxScroll := max(len(p.opts)*rowH+r.cz(16)-list.Dy(), 0)
	scroll = min(max(scroll, 0), maxScroll)
	x0, x1 := r.cz(carIn), r.w-r.cz(carIn)
	from := len(r.pending)
	for i, o := range p.opts {
		top := head + i*rowH - scroll
		if top >= r.h || top+rowH <= head {
			continue
		}
		cy := top + rowH/2
		tx, ink := x0, cream
		if i < len(p.swatches) {
			sw, rad := p.swatches[i], float64(r.cz(18))
			cx := float64(x0) + rad
			r.aaDisc(cx, float64(cy), rad, sw[0])
			r.aaRing(cx, float64(cy), rad, float64(r.cz(2)), 0, 2*math.Pi, lerp(sw[0], cream, 0.35))
			r.aaDisc(cx, float64(cy), rad/2, sw[1])
			tx = x0 + int(2*rad) + r.cz(24)
		}
		room := x1 - tx - r.cz(56)
		if i == p.cur {
			ink = amber
			r.tick(x1-r.cz(20), cy)
		}
		r.text(cf.label, r.fit(cf.label, o, room), tx, mid(cf.label, cy), ink)
		r.addZone(zone{r: image.Rect(0, top, r.w, top+rowH), kind: zoneOption, opt: i})
	}
	r.clipZones(from, list)
	r.listFades(list, scroll, maxScroll)
	r.carHeader(p.title, nil)
	return maxScroll
}

// tick is the mark beside the choice in force, centred on cx, cy. Figtree has no ✓.
func (r *renderer) tick(cx, cy int) {
	k, x, y := r.sf(100)/100*float64(menuZoom.Load())/100, float64(cx), float64(cy)
	r.aaLine(x-14*k, y, x-5*k, y+10*k, 4*k, amber)
	r.aaLine(x-5*k, y+10*k, x+14*k, y-11*k, 4*k, amber)
}

// carAdjust is a stepper's value on a level of its own: large, over a bar showing where it stands
// between its ends when it has them, with − and + either side.
func (r *renderer) carAdjust(row settingRow, st settings) {
	cf := r.car()
	head := r.cz(carHeader)
	body := r.h - head
	vy := head + body*36/100
	r.text(cf.big, r.fit(cf.big, row.value, r.w-2*r.cz(carIn)), (r.w-r.width(cf.big, r.fit(cf.big, row.value, r.w-2*r.cz(carIn))))/2,
		mid(cf.big, vy), cream)

	btn := r.cz(96)
	by := head + body*72/100
	minus := image.Rect(r.cz(carIn), by-btn/2, r.cz(carIn)+btn, by+btn/2)
	plus := image.Rect(r.w-r.cz(carIn)-btn, by-btn/2, r.w-r.cz(carIn), by+btn/2)
	for i, b := range []image.Rectangle{minus, plus} {
		fill := lerp(walnut, cream, 0.14)
		r.aaDisc(float64(b.Min.X)+float64(btn)/2, float64(by), float64(btn)/2, fill)
		name := "minus"
		if i == 1 {
			name = "plus"
		}
		icon := r.cz(48)
		r.mdiIcon(name, b.Min.X+(btn-icon)/2, by-icon/2, zb(48), cream)
	}
	if frac := adjustFrac(row.id, st); frac >= 0 {
		frac = min(frac, 1)
		bar := image.Rect(minus.Max.X+r.cz(40), by-r.cz(4), plus.Min.X-r.cz(40), by+r.cz(4))
		track := lerp(walnut, cream, 0.2)
		r.roundFill(bar, float64(r.cz(4)), track, track)
		end := bar.Min.X + int(float64(bar.Dx())*frac)
		if end > bar.Min.X {
			r.roundFill(image.Rect(bar.Min.X, bar.Min.Y, end, bar.Max.Y), float64(r.cz(4)), amber, amber)
		}
		r.aaDisc(float64(end), float64(by), float64(r.cz(14)), cream)
	}
	// Each half of the screen under the value is its button: the round ones are only where to look.
	r.addZone(zone{r: image.Rect(0, vy+r.cz(60), r.w/2, r.h), kind: zoneRow, id: row.id, part: partMinus})
	r.addZone(zone{r: image.Rect(r.w/2, vy+r.cz(60), r.w, r.h), kind: zoneRow, id: row.id, part: partPlus})
	r.carHeader(row.label, nil)
}

// adjustFrac is where a stepper's value stands between its ends, or -1 when it has none to show.
func adjustFrac(id string, st settings) float64 {
	switch id {
	case "brightness":
		return float64(st.brightness-25) / 75
	case "volume":
		return float64(st.volume) / sheetVolumeSteps
	case "wakesens":
		return (config.Get().Wake.Slot(0).Threshold - 0.5) / 0.49
	case "bass", "treble":
		v := config.Get().Speaker.Bass
		if id == "treble" {
			v = config.Get().Speaker.Treble
		}
		return float64(v+asp.ToneRange) / float64(2*asp.ToneRange)
	case "ringvol":
		return float64(ring.Level()) / sheetVolumeSteps
	}
	return -1
}

// confirmTexts are the questions asked before a row does what can't be taken back: the title, what
// will happen, and the word that does it.
var confirmTexts = map[string][3]string{
	"restart":      {"Restart?", "The Show is back in about a minute.", "Restart"},
	"factoryreset": {"Erase this Show?", "This erases the settings, Wi-Fi, the Home Assistant key and the root password, then restarts.", "Erase"},
	"e.delete":     {"Delete this alarm?", "It won't ring again.", "Delete"},
}

// carConfirm is the question over everything: a black screen, the question, and Cancel beside the
// answer that does it. A tap anywhere else is Cancel.
func (r *renderer) carConfirm(id string) {
	t, ok := confirmTexts[id]
	if !ok {
		return
	}
	cf := r.car()
	r.fill(r.dst.Rect, color.RGBA{A: 0xff})
	r.pending = r.pending[:0]
	r.addZone(zone{r: r.dst.Rect, kind: zoneDismiss, id: "confirm"})
	white := color.RGBA{0xff, 0xff, 0xff, 0xff}
	lines := r.lines(cf.body, t[1], r.cz(540), 4)
	pillH := r.cz(72)
	block := r.cz(64) + len(lines)*r.cz(40) + r.cz(40) + pillH
	y := (r.h - block) / 2
	r.text(cf.modal, t[0], (r.w-r.width(cf.modal, t[0]))/2, mid(cf.modal, y+r.cz(26)), white)
	y += r.cz(64) + r.cz(8)
	for _, line := range lines {
		r.text(cf.body, line, (r.w-r.width(cf.body, line))/2, mid(cf.body, y+r.cz(16)), lerp(color.RGBA{A: 0xff}, white, 0.75))
		y += r.cz(40)
	}
	y += r.cz(32)
	pad, gap := r.cz(40), r.cz(24)
	no, yes := "Cancel", t[2]
	wNo, wYes := r.width(cf.pill, no)+2*pad, r.width(cf.pill, yes)+2*pad
	left := (r.w - wNo - gap - wYes) / 2
	for i, p := range []struct {
		label, id string
		w         int
		fill, ink color.RGBA
	}{
		{no, "confirm.no", wNo, color.RGBA{0x1f, 0x1f, 0x1f, 0xff}, white},
		{yes, "confirm.yes", wYes, white, color.RGBA{A: 0xff}},
	} {
		b := image.Rect(left, y, left+p.w, y+pillH)
		r.roundFill(b, float64(pillH)/2, p.fill, p.fill)
		r.text(cf.pill, p.label, b.Min.X+pad, mid(cf.pill, y+pillH/2), p.ink)
		r.addZone(zone{r: b.Inset(-r.cz(8)), kind: zoneAction, id: p.id})
		left += p.w + gap
		_ = i
	}
}
