package display

import (
	"strconv"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/i18n"

	"github.com/HuskerMinion/techo5/echod/internal/feature/firmware"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// keyNote is the line that says Home Assistant turned this device's token away, or nothing. It takes
// the update's place, ahead of it, as nothing that needs Home Assistant works until the installer is
// run again, and the device stops asking (lib/hass), so nobody would find out any other way.
func keyNote(short bool) string {
	switch {
	case !hass.Get().Refused():
		return ""
	case short:
		return "Key refused"
	}
	return "Home Assistant refused this device · rerun the installer"
}

// updateNote is the line that says an update is ready to install, or nothing. It shares the place a
// missed ring is said in, and gives way to one. short is the round face's, where only a few words fit.
func updateNote(short bool) string {
	v := firmware.Get().Offered()
	switch {
	case v == "":
		return ""
	case short:
		return "Update ready"
	}
	return i18n.F("Update ready: {version} · Settings › Updates", "version", v)
}

// installNote says how far an install has got, or ok is false when none is running. at is the
// download's fraction, and -1 once the download is done: checking it and writing the other slot report
// nothing of their own, so that part gets a spinner and no percentage.
//
// Before this the screen said nothing at all from the press of Install to the reboot, which on a slow
// link is minutes of a device that looks like it ignored the tap.
func installNote() (line string, at float32, ok bool) {
	running, at := firmware.Get().Installing()
	if !running {
		return "", 0, false
	}
	v := firmware.Get().Offered()
	if at < 1 {
		pct := strconv.Itoa(int(max(at, 0) * 100))
		return i18n.F("Downloading {version} · {percent}%", "version", v, "percent", pct), at, true
	}
	return i18n.F("Installing {version}…", "version", v), -1, true
}

// missedNote is the line that says a ring fell due and never sounded, or nothing. The latest one by
// name and time, and how many more, so a night away is one line rather than a list.
//
// short is the round face, where a line wider than the circle is cut off by its edge: there it is the
// ring and the time and nothing else.
func missedNote(now time.Time, short bool) string {
	list := ring.Missing()
	if len(list) == 0 {
		return ""
	}
	m := list[len(list)-1]
	if r := []rune(m.Label); short && len(r) > 12 {
		m.Label = string(r[:11]) + "…"
	}
	line := i18n.Sprintf("Missed: %s at %s", m.Says(), clockText(m.Due))
	if short {
		return line
	}
	line += daysAgo(m.Due, now)
	if n := len(list) - 1; n > 0 {
		line += i18n.Sprintf(" · and %d more", n)
	}
	return line
}

// daysAgo says which day a past time was on, when it was not today.
func daysAgo(t, now time.Time) string {
	y1, m1, d1 := now.Date()
	y2, m2, d2 := t.In(now.Location()).Date()
	days := int(time.Date(y1, m1, d1, 0, 0, 0, 0, time.UTC).Sub(time.Date(y2, m2, d2, 0, 0, 0, 0, time.UTC)).Hours() / 24)
	switch {
	case days <= 0:
		return ""
	case days == 1:
		return " yesterday"
	case days < 7:
		return " on " + t.Weekday().String()
	}
	return " on " + i18n.Date(t, "Jan 2")
}

// onMissed calls f when what was missed changes. Here rather than in display.go, whose frame loop
// has a local called ring.
func onMissed(f func()) { ring.MissedChanged.Listen(func(struct{}) { f() }) }
