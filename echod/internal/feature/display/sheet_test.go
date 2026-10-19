//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

func testRenderer() *renderer { return newRenderer(image.NewRGBA(image.Rect(0, 0, 960, 480))) }

// A tap takes the topmost thing under it; a near miss takes what it nearly hit; and with a list open,
// nothing under the list can be reached, only its choices or the tap that puts it away.
func TestZoneAt(t *testing.T) {
	r := testRenderer()
	r.zones = []zone{
		{r: image.Rect(0, 0, 100, 100), kind: zoneRow, id: "under"},
		{r: image.Rect(40, 40, 60, 60), kind: zoneRow, id: "over"},
		{r: image.Rect(300, 300, 340, 340), kind: zoneRow, id: "small"},
	}
	for _, c := range []struct {
		x, y int
		want string
	}{{10, 10, "under"}, {50, 50, "over"}, {320, 320, "small"}, {345, 320, "small"}, {400, 400, ""}} {
		z, ok := r.zoneAt(c.x, c.y)
		if got := map[bool]string{true: z.id, false: ""}[ok]; got != c.want {
			t.Errorf("tap at %d,%d = %q, want %q", c.x, c.y, got, c.want)
		}
	}

	r.zones = append(r.zones,
		zone{r: r.dst.Rect, kind: zoneDismiss},
		zone{r: image.Rect(500, 100, 700, 150), kind: zoneOption, opt: 3})
	if z, _ := r.zoneAt(50, 50); z.kind != zoneDismiss {
		t.Errorf("with a list open, a tap on the row under it reached %v", z.kind)
	}
	if z, _ := r.zoneAt(600, 155); z.kind != zoneOption || z.opt != 3 {
		t.Errorf("a near miss on a choice = %+v, want choice 3", z)
	}
}

// sheetScene is a settings screen on a category, with enough in it to draw every kind of row.
func sheetScene(cat category) scene {
	now := time.Date(2026, 9, 18, 15, 0, 0, 0, time.Local)
	s := scene{now: now, snooze: 9}
	s.sheet = settings{cat: cat, now: now, brightness: 75, volume: 5, name: "Kitchen", version: "v0.0.0",
		slot: "a", address: "10.0.0.50", wifiName: "HomeWiFi", wifiOK: true, weather: "Home"}
	s.bt.Available, s.bt.Remembered = true, "Speaker"
	s.security.SSHAvailable, s.security.Encrypted = true, true
	s.alarms.Local = []config.Alarm{{ID: "a", Hour: 6, Minute: 30, Days: config.DaysWeekdays, On: true}}
	return s
}

// Every level draws with somewhere to tap for everything in view that does something, and all of it
// on the screen: the categories, each category, each page, and the editors.
func TestEveryCardDraws(t *testing.T) {
	cases := map[string]scene{}
	for c := category(0); c < categories; c++ {
		cases[categoryNames[c]] = sheetScene(c)
		for pg := range pageTitles {
			s := sheetScene(c)
			s.sheet.page = pg
			if v := categoryCard(s.view()); v.title == pageTitles[pg] {
				cases[categoryNames[c]+"/"+pg] = s
			}
		}
	}
	root := sheetScene(catDisplay)
	root.sheet.root = true
	cases["settings"] = root
	editor := sheetScene(catAlarms)
	editor.draft = &alarmDraft{alarm: editor.alarms.Local[0]}
	cases["alarm editor"] = editor
	colors := sheetScene(catDisplay)
	colors.sheet.colors = true
	cases["custom colors"] = colors

	for name, s := range cases {
		r := testRenderer()
		r.settingsScreen(s)
		ids, cats, back := map[string]bool{}, 0, 0
		for _, z := range r.zones {
			if !z.r.In(r.dst.Rect) || z.r.Empty() {
				t.Errorf("%s: zone %+v is off the screen or empty", name, z)
			}
			switch z.kind {
			case zoneRow:
				ids[z.id] = true
			case zoneCat:
				cats++
			case zoneBack:
				back++
			}
		}
		if back != 1 {
			t.Errorf("%s: %d ways back, want 1", name, back)
		}
		if s.sheet.root {
			// As many as fit, and the rest once it has scrolled to them.
			s.sheet.cardScroll, _ = r.scrollLimits()
			r.settingsScreen(s)
			for _, z := range r.zones {
				if z.kind == zoneCat && z.cat == categories-1 {
					cats++
				}
			}
			if cats < 4 {
				t.Errorf("%s: %d categories to tap, and the last not once scrolled", name, cats-1)
			}
			continue
		}
		v := categoryCard(s.view())
		if len(v.rows) == 0 {
			t.Errorf("%s: no rows", name)
			continue
		}
		// The rows in view: every one with an id that does something can be tapped.
		y := r.cz(carHeader)
		for _, row := range v.rows {
			h := r.carLayout(row, r.w-2*r.cz(carIn)).h
			if y+h > r.h {
				break
			}
			if row.id != "" && row.kind != ctlValue && !ids[row.id] {
				t.Errorf("%s: row %q has nowhere to tap", name, row.label)
			}
			y += h
		}
	}
}

// A list longer than the screen scrolls, as far as its last row and no further, and nothing it
// draws while scrolled can be tapped under the header.
func TestCardScrolls(t *testing.T) {
	s := sheetScene(catAlarms)
	s.alarms.Local = nil
	for i := range 10 {
		s.alarms.Local = append(s.alarms.Local, config.Alarm{ID: fmt.Sprint(i), Hour: i + 5, On: true})
	}
	r := testRenderer()
	r.settingsScreen(s)
	cardMax, _ := r.scrollLimits()
	v := categoryCard(s.view())
	tall := 0
	for _, row := range v.rows {
		tall += r.carLayout(row, r.w-2*r.cz(carIn)).h
	}
	if want := tall + r.cz(24) - (r.h - r.cz(carHeader)); cardMax != want {
		t.Fatalf("%d rows scroll %d, want %d", len(v.rows), cardMax, want)
	}

	s.sheet.cardScroll = cardMax + 500 // held to the end
	r.settingsScreen(s)
	list := image.Rect(0, r.cz(carHeader), r.w, r.h)
	lastID := v.rows[len(v.rows)-1].id
	var last bool
	for _, z := range r.zones {
		if z.kind == zoneRow && !z.r.In(list) {
			t.Errorf("row zone %+v outside the list %v", z, list)
		}
		last = last || z.id == lastID
	}
	if !last {
		t.Errorf("scrolled to the end, the last row (%q) cannot be tapped", lastID)
	}
}

// Short lists sit in columns with every choice in view; a long one is a single column that scrolls.
func TestPickerLayout(t *testing.T) {
	for _, n := range []int{4, 13, 30} {
		p := pickerView{title: "List", cur: 0}
		for i := range n {
			p.opts = append(p.opts, fmt.Sprintf("Choice %d", i+1))
		}
		r := testRenderer()
		r.pending = r.pending[:0]
		pickMax := r.picker(p, 0)
		var opts []image.Rectangle
		for _, z := range r.pending {
			if z.kind == zoneOption {
				opts = append(opts, z.r)
			}
		}
		switch {
		case n <= pickScrollOver && (pickMax != 0 || len(opts) != n):
			t.Errorf("%d choices: scroll %d, %d in view; want 0 and all", n, pickMax, len(opts))
		case n > pickScrollOver && (pickMax <= 0 || len(opts) > pickRows+1):
			t.Errorf("%d choices: scroll %d, %d in view; want a scroll and at most %d", n, pickMax, len(opts), pickRows+1)
		}
		for i := range opts {
			for j := i + 1; j < len(opts); j++ {
				if opts[i].Inset(8).Overlaps(opts[j].Inset(8)) {
					t.Errorf("%d choices: %d and %d overlap", n, i, j)
				}
			}
		}
	}
}

// Screenshots for publishing hide station names: call letters say where the owner lives.
func TestDemoHidesStations(t *testing.T) {
	s := scene{drawerTab: drawerRadio, demo: true}
	s.radio = home.Radio{Configured: true, Stations: []string{"101.1 WXYZ", "88.5 KABC"}}
	rows, _ := drawerRows(s)
	for _, row := range rows {
		if strings.Contains(row.label, "WXYZ") || strings.Contains(row.label, "KABC") {
			t.Errorf("demo shows %q", row.label)
		}
	}
	s.demo = false
	rows, _ = drawerRows(s)
	if got := i18n.T(rows[1].label); got != "101.1 WXYZ" { // as drawn
		t.Errorf("without demo, first station = %q", got)
	}
}

// A paused track still has a Stop row: Music Assistant ends its stream when it pauses, so a remote's track
// paused from here is held for the screen with play offered. The page said "Paused" and this list said
// nothing, because playing was the only thing its gate knew about.
func TestAPausedTrackStillHasAStopRow(t *testing.T) {
	s := scene{drawerTab: drawerRadio}
	s.radio = home.Radio{Configured: true, Stations: []string{"101.1 WXYZ"}, Paused: true}

	rows, _ := drawerRows(s)
	if len(rows) < 2 || rows[1].label != "Stop the radio" {
		t.Errorf("rows = %+v with a paused track, want a Stop row first", rows)
	}
}

// A stop in a grouped room stops the group, and the row says so rather than doing something bigger than its
// words. What changes is who else it reaches, so the label stays the row's own.
func TestTheStopRowSaysWhenItStopsTheGroup(t *testing.T) {
	s := scene{drawerTab: drawerRadio}
	s.radio = home.Radio{Configured: true, Stations: []string{"■ Stop", "101.1 WXYZ"}, Playing: true}

	rows, _ := drawerRows(s)
	if len(rows) < 2 || rows[1].label != "Stop the radio" || rows[1].sub != "" {
		t.Fatalf("stop row = %+v, want the radio and nothing more", rows)
	}

	s.radio.Grouped = true
	rows, _ = drawerRows(s)
	if len(rows) < 2 || rows[1].label != "Stop the radio" || rows[1].sub != "all rooms in the group" {
		t.Errorf("stop row = %+v in a grouped room, want the group said", rows)
	}
}

// The swipe that opened the settings keeps reporting notches until its finger lifts; those do not
// scroll. Another swipe does, a notch at a time, and stops at the ends.
func TestSheetSwipe(t *testing.T) {
	d := &Display{r: testRenderer()}
	d.r.cardMax = 100
	d.openedBy = image.Pt(480, 30)
	d.sheetSwipe(touch.Gesture{Kind: touch.SwipeUp, X: 480, Y: 30})
	if d.cardScroll != 0 {
		t.Fatalf("the opening swipe scrolled to %d", d.cardScroll)
	}
	for range 5 {
		d.sheetSwipe(touch.Gesture{Kind: touch.SwipeUp, X: 600, Y: 300})
	}
	if d.cardScroll != 100 {
		t.Errorf("five notches up = %d, want held at 100", d.cardScroll)
	}
	d.sheetSwipe(touch.Gesture{Kind: touch.SwipeDown, X: 600, Y: 300})
	if d.cardScroll != 100-notchPx {
		t.Errorf("a notch back = %d, want %d", d.cardScroll, 100-notchPx)
	}
}

func TestSheetWords(t *testing.T) {
	defer clock24.Store(clock24.Load())
	clock24.Store(false)
	if got := nightText("22-6"); got != "10 PM – 6 AM" {
		t.Errorf("nightText = %q", got)
	}
	if got := nightText(""); got != "Never" {
		t.Errorf("nightText(off) = %q", got)
	}
	clock24.Store(true)
	if got := nightText("22-6"); got != "22:00 – 06:00" {
		t.Errorf("24-hour nightText = %q", got)
	}

	if repeatName(config.DaysWeekdays) != "Weekdays" || repeatName(0x05) != "Custom" {
		t.Error("repeatName")
	}
	now := time.Date(2026, 9, 18, 15, 0, 0, 0, time.Local)
	for at, want := range map[time.Time]string{
		now.Add(time.Hour): "Today", now.Add(20 * time.Hour): "Tomorrow", now.Add(72 * time.Hour): "Monday",
	} {
		if got := dayWord(at, now); got != want {
			t.Errorf("dayWord(%v) = %q, want %q", at, got, want)
		}
	}
}

// Changing category repaints the parts of the screen that depend on it; the rest is kept. On the
// Show this is what a tap on the rail waits for.
func BenchmarkCategorySwitch(b *testing.B) {
	r := testRenderer()
	scenes := make([]scene, categories)
	for c := range scenes {
		scenes[c] = sheetScene(category(c))
	}
	r.settingsScreen(scenes[0])
	b.ResetTimer()
	for i := range b.N {
		r.settingsScreen(scenes[(i+1)%len(scenes)])
	}
}

// Opening a list over the card: the long Theme list, in columns.
func BenchmarkPickerOpen(b *testing.B) {
	r := testRenderer()
	s := sheetScene(catDisplay)
	s.sheet.picker = "theme"
	b.ResetTimer()
	for range b.N {
		r.settingsScreen(s)
	}
}

// The camera's own sound is what the device plays, so it is a Sound setting and belongs on that card. It
// went under Display first, where the rows are about the screen — brightness, night, the clock, whether a
// camera is up — and a sound setting among them is one nobody looks for.
func TestTheCameraSoundSettingIsUnderSound(t *testing.T) {
	inCard := func(cat category, id string) bool {
		rows, _ := categoryRows(sheetView{st: settings{cat: cat}})
		for _, r := range rows {
			if r.id == id {
				return true
			}
		}
		return false
	}

	if !inCard(catSound, "camerasound") {
		t.Error("the camera's sound is not on the Sound card")
	}
	if inCard(catDisplay, "camerasound") {
		t.Error("the camera's sound is still on the Display card")
	}
	// And what stayed where it was: how long a view stays up is about the screen, not the sound.
	if !inCard(catDisplay, "camtime") {
		t.Error("the camera time row went with it: how long a view stays up is a screen setting")
	}
}

// A label too long for its row gives up its end, not the value beside it: the value is fitted first,
// to at most half the row, and the label takes what is left.
func TestACutLineLeavesTheValueWhole(t *testing.T) {
	row := settingRow{id: "screenlang", label: "The language of the words on this screen and the ones it listens for, in every room of the house, from the kitchen to the hall and on up the stairs",
		kind: ctlChoice, value: "Match Assistant"}
	t.Cleanup(func() { useMenuSize(menuSizeIndexOf(0)) })
	for _, m := range menuSizes {
		useMenuSize(menuSizeIndexOf(m.percent))
		for _, w := range []int{showWide, show8Wide} {
			h := map[int]int{showWide: showHigh, show8Wide: show8High}[w]
			r := newRenderer(image.NewRGBA(image.Rect(0, 0, w, h)))
			l := r.carLayout(row, r.w-2*r.cz(carIn))
			if i18n.T(l.value) != row.value {
				t.Errorf("%d wide at %d%%: the value is cut to %q", w, menuZoom.Load(), i18n.T(l.value))
			}
			if i18n.T(strings.Join(l.label, " ")) == row.label {
				t.Fatalf("%d wide at %d%%: the label was not cut, so this does not test anything", w, menuZoom.Load())
			}
		}
	}
}

// No setting is cut: a label or the line under it that is too long for one line wraps to a second,
// in English and in German, which runs the longest of the screen's languages. That goes for the rows
// that open a page too, and at every menu size.
func TestEveryRowReadsWhole(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	t.Cleanup(i18n.Changed)
	for _, lang := range []string{"", "de"} {
		if err := config.Set().Screen().Language(lang); err != nil {
			t.Fatal(err)
		}
		i18n.Changed()
		for c := category(0); c < categories; c++ {
			r := testRenderer()
			v := categoryCard(sheetScene(c).view())
			for _, row := range append(v.rows, v.all...) {
				l := r.carLayout(row, r.w-2*r.cz(carIn))
				for _, line := range append(l.label, l.sub...) {
					if strings.HasSuffix(line, "…") {
						t.Errorf("%s %q: %q is cut: %q", categoryNames[c], lang, row.label, line)
					}
				}
				if l.h < r.cz(carRow) {
					t.Errorf("%s %q: %q is shorter than a row", categoryNames[c], lang, row.label)
				}
			}
		}
	}
}

// A stepper opens a page of its own, where each half of the screen under the value is − and +.
func TestAStepperOpensItsOwnPage(t *testing.T) {
	s := sheetScene(catSound)
	r := testRenderer()
	r.settingsScreen(s)
	var opens bool
	for _, z := range r.zones {
		opens = opens || (z.id == "volume" && z.part == partAdjust)
	}
	if !opens {
		t.Fatal("the volume row does not open its page")
	}

	s.sheet.adjust = "volume"
	r.settingsScreen(s)
	for _, c := range []struct {
		x, y int
		want part
	}{
		{r.w / 8, r.h - 10, partMinus},
		{r.w/2 - 10, r.h * 3 / 4, partMinus},
		{r.w/2 + 10, r.h * 3 / 4, partPlus},
		{r.w - 10, r.h - 10, partPlus},
	} {
		if z, _ := r.zoneAt(c.x, c.y); z.kind != zoneRow || z.id != "volume" || z.part != c.want {
			t.Errorf("a tap at %d,%d = %+v, want %v", c.x, c.y, z, c.want)
		}
	}
	if z, _ := r.zoneAt(20, 20); z.kind != zoneBack {
		t.Errorf("the back arrow = %+v, want back", z)
	}
}

// Factory reset asks first, over everything, and Erase is no yes until resetPause has gone by: a
// double tap or a finger resting where the row was does not erase the Show.
func TestFactoryResetAsksFirst(t *testing.T) {
	resets := 0
	was := factoryReset
	factoryReset = func(string) { resets++ }
	t.Cleanup(func() { factoryReset = was })

	s := sheetScene(catGeneral)
	s.sheet.confirm = "factoryreset"
	r := testRenderer()
	r.settingsScreen(s)
	found := map[string]bool{}
	for _, z := range r.zones {
		if z.kind == zoneRow {
			t.Errorf("a row can be tapped under the question: %+v", z)
		}
		found[z.id] = true
	}
	if !found["confirm.yes"] || !found["confirm.no"] || !found["confirm"] {
		t.Fatalf("the question has %v, want Erase, Cancel and a tap off it", found)
	}

	d := &Display{r: testRenderer()}
	d.confirm, d.confirmAt = "factoryreset", time.Now()
	d.confirmYes()
	if resets != 0 || d.confirm == "" {
		t.Fatal("an Erase right after the question erased the Show, or put the question away")
	}
	d.confirmAt = time.Now().Add(-2 * resetPause)
	d.confirmYes()
	if resets != 1 || d.confirm != "" {
		t.Fatalf("a deliberate Erase: %d resets, question %q; want 1 and none", resets, d.confirm)
	}

	d.confirm = "factoryreset"
	d.sheetBack()
	if d.confirm != "" || resets != 1 {
		t.Error("back did not put the question away without erasing")
	}
}

// The drawer is a shelf: a swipe to the left slides it a tile on and stops at its end, one to the right
// slides it back, and at its start the same swipe puts the drawer away. Over a list of choices, the back
// arrow is the shelf again; on the shelf, it closes the drawer.
func TestTheShelfSlidesAndBackCloses(t *testing.T) {
	d := &Display{r: testRenderer(), drawer: true}
	step := d.r.shelfStep()
	d.r.cardMax = step + step/2
	for range 3 {
		d.drawerGesture(touch.Gesture{Kind: touch.SwipeLeft, X: 600, Y: 300})
	}
	if d.drawerScroll != d.r.cardMax {
		t.Fatalf("three swipes left = %d, want held at the end, %d", d.drawerScroll, d.r.cardMax)
	}
	d.drawerGesture(touch.Gesture{Kind: touch.SwipeRight, X: 600, Y: 300})
	d.drawerGesture(touch.Gesture{Kind: touch.SwipeRight, X: 600, Y: 300})
	if d.drawerScroll != 0 || !d.drawer {
		t.Fatalf("two swipes back = %d, drawer %v, want the start with the drawer in", d.drawerScroll, d.drawer)
	}
	d.drawerGesture(touch.Gesture{Kind: touch.SwipeRight, X: 600, Y: 300})
	if d.drawer {
		t.Fatal("a swipe right at the start left the drawer in")
	}

	d.drawer, d.drawerPick = true, "radiosource"
	d.r.zones = []zone{{r: image.Rect(0, 0, 100, 100), kind: zoneBack}}
	d.drawerGesture(touch.Gesture{Kind: touch.Tap, X: 40, Y: 40})
	if d.drawerPick != "" || !d.drawer {
		t.Fatalf("back over a list: pick %q drawer %v, want the shelf", d.drawerPick, d.drawer)
	}
	d.drawerGesture(touch.Gesture{Kind: touch.Tap, X: 40, Y: 40})
	if d.drawer {
		t.Fatal("back on the shelf left the drawer in")
	}
}

// The drawer comes in from the right edge over a settings page as it does over the clock; a swipe left
// across the middle of the page is not it (Jade, 2026-10-12: no way to the radio from a sub-page).
func TestTheDrawerSwipesInOverSettings(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Screen().Welcomed(true); err != nil {
		t.Fatal(err)
	}
	d := &Display{r: testRenderer(), poke: make(chan struct{}, 1), on: true, view: voice.State{Phase: "idle"}, drawerTab: drawerAnnounce}
	d.showSheet(true)
	d.mu.Lock()
	d.root, d.page = false, "display"
	d.mu.Unlock()
	d.gesture(touch.Gesture{Kind: touch.SwipeLeft, X: d.r.w / 2, Y: 300})
	if d.drawer || !d.sheet {
		t.Fatalf("a swipe left mid-page: drawer %v sheet %v, want the page left as it was", d.drawer, d.sheet)
	}
	d.gesture(touch.Gesture{Kind: touch.SwipeLeft, X: d.r.w - 20, Y: 300})
	if !d.drawer || d.sheet || d.drawerTab != drawerAnnounce {
		t.Fatalf("a swipe in from the edge: drawer %v sheet %v tab %d, want the drawer on its last tab", d.drawer, d.sheet, d.drawerTab)
	}
}
