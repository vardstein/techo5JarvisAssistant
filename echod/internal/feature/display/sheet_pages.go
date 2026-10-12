//go:build !dot && !spot

package display

import (
	"log/slog"
	"strconv"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/alarm"
	"github.com/HuskerMinion/techo5/echod/internal/feature/sendspin"
)

// The Show's settings as levels, the way the Car Thing had them: the categories, a category's
// rows, and under those a page for each group of rows that belong together (the clock, the
// weather, the wake word…), so no list runs much past a screen. A stepper's value is set on a page
// of its own, and anything that cannot be taken back asks first.

// keysPage is the page the SSH keys are listed on.
const keysPage = "sshkeys"

// rowPages is the page each row is on, by its id. A row not named stays on its category.
var rowPages = map[string]string{
	"night": "night", "atnight": "night", "nightstyle": "night",
	"theme": "theme", "colors": "theme",
	"clock": "clock", "clockpos": "clock", "datecolor": "clock",
	"callbutton": "home", "musicstrip": "home", "turnstyle": "home", "slideshow": "home",
	"photofolder": "home", "photoevery": "home", "shuffle": "home", "subfolders": "home",
	"weatherfx": "weather", "radarsrc": "weather", "alerts": "weather",
	"camtime": "ontime", "answertime": "ontime",
	"wakeword": "wake", "wakesens": "wake", "waketone": "wake",
	"bass": "tone", "treble": "tone",
	"hasounds": "sounds", "camerasound": "sounds",
	"calendars": "calendar", "calpop": "calendar", "calpopwhen": "calendar", "calpopsound": "calendar",
	"calpopallday": "calendar", "calpopcals": "calendar",
}

// pageTitles head each page, and name the row that opens it.
var pageTitles = map[string]string{
	"night": "Night", "theme": "Theme", "clock": "Clock", "home": "Home screen", "weather": "Weather",
	"ontime": "Time on screen", "wake": "Wake word", "tone": "Bass & treble", "sounds": "Sounds",
	"calendar": "Calendar", "updates": "Updates", "diag": "Diagnostics", keysPage: "SSH keys",
}

// alwaysPage are the pages that stay pages with a single row on them.
var alwaysPage = map[string]bool{keysPage: true, "updates": true, "diag": true}

// liveRow is whether a row's line under it says how things stand now (a state, a warning), which
// the Show keeps, rather than what the setting is for, which it leaves out.
func liveRow(row settingRow) bool {
	switch row.id {
	case "ssh", "setuppage", "updatecheck", "bluetooth", "sleep", "quiet":
		return true
	case "sendspin":
		return !sendspin.Get().Paired()
	case "ringvol":
		return row.value == "Silent"
	}
	return false
}

// pageCard is the card at the level open: a page's rows under its title, or the category's with
// each group of two or more folded into the row that opens its page.
func pageCard(v cardView, st settings) cardView {
	for i := range v.rows {
		row := &v.rows[i]
		if row.page == "" {
			row.page = rowPages[row.id]
		}
		row.live = liveRow(*row)
	}
	v.all = v.rows
	count := map[string]int{}
	for _, row := range v.rows {
		count[row.page]++
	}
	if st.page != "" && count[st.page] > 0 {
		var rows []settingRow
		for _, row := range v.rows {
			if row.page == st.page {
				rows = append(rows, row)
			}
		}
		v.title, v.blurb, v.rows = pageTitles[st.page], "", rows
		return v
	}
	var rows []settingRow
	opened := map[string]bool{}
	for _, row := range v.rows {
		pg := row.page
		if pg == "" || (count[pg] < 2 && !alwaysPage[pg]) {
			rows = append(rows, row)
			continue
		}
		if opened[pg] {
			continue
		}
		opened[pg] = true
		open := settingRow{id: "page:" + pg, label: pageTitles[pg], kind: ctlPage}
		switch {
		case pg == keysPage:
			open.value = strconv.Itoa(count[pg])
		case row.kind == ctlChoice:
			open.value = row.value
		}
		for _, m := range v.rows {
			if m.page == pg && m.live {
				open.sub, open.live = m.sub, true
				break
			}
		}
		rows = append(rows, open)
	}
	v.rows = rows
	return v
}

// sheetBack is one level up: from a question, a value being set, a list, the colors, an alarm
// being edited or a page to the level under it, from a category to the categories, and from those
// out of the settings.
func (d *Display) sheetBack() {
	d.mu.Lock()
	switch {
	case d.confirm != "":
		d.confirm = ""
	case d.adjust != "":
		d.adjust = ""
	case d.picker != "":
		d.picker, d.pickScroll = "", 0
	case d.colors:
		d.colors = false
	case d.draft != nil:
		d.draft, d.cardScroll = nil, 0
	case d.page != "":
		d.page, d.cardScroll = "", d.backScroll
	case !d.root:
		d.root, d.cardScroll = true, 0
	default:
		d.mu.Unlock()
		d.closeSheet()
		return
	}
	d.mu.Unlock()
}

// confirmYes is the answer to the question up: the row asked about does what it does.
func (d *Display) confirmYes() {
	d.mu.Lock()
	id, at, dr := d.confirm, d.confirmAt, d.draft
	if id == "factoryreset" && time.Since(at) < resetPause {
		d.mu.Unlock()
		return // the tap that asked, or one right after it: wait for a deliberate one
	}
	d.confirm = ""
	d.mu.Unlock()
	switch id {
	case "factoryreset":
		slog.Warn("factory reset asked for from the screen")
		factoryReset("screen")
	case "restart":
		slog.Warn("restart asked for from the screen")
		restartNow()
	case "e.delete":
		if dr == nil || dr.isNew {
			return
		}
		if err := alarm.Get().Delete(dr.alarm.ID); err != nil {
			slog.Warn("deleting an alarm failed", "err", err)
		}
		d.closeDraft()
	}
}
