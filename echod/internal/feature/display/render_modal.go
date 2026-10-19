//go:build !dot && !spot

package display

import (
	"image/color"
	"strings"

	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"golang.org/x/image/font"
)

// Announcements, the microphone open for one, reminders and an event's pop-up are the Car Thing's
// modal: the screen goes nearly black over what was on it, with a line in the accent saying what this
// is, the words large in the middle, and a softer line under them. One is up at a time, the one that
// matters most, and a tap anywhere is for it: with the page under it at a tenth of its light, there is
// nothing else a finger could have meant.

// What is up, as last drawn.
type modalKind int

const (
	modalNone modalKind = iota
	modalRecording
	modalReminder
	modalAnnouncement
	modalPopup
)

// overlays draws the one modal the scene has up, if any, and the voice turn's strip over it.
func (r *renderer) overlays(s scene) {
	kind := modalNone
	switch {
	case s.announceRecording:
		r.recordingModal(s)
		kind = modalRecording
	case s.showReminder:
		r.reminderModal(s)
		kind = modalReminder
	case s.showAnnouncement:
		r.announcementModal(s)
		kind = modalAnnouncement
	case s.popup != nil:
		r.popupModal(s, *s.popup)
		kind = modalPopup
	}
	r.setModal(kind)
	// A turn is somebody talking to the device now, so it goes over whatever is up.
	if !s.announceRecording && turnCovered(s) {
		r.turnStrip(s)
	}
}

func (r *renderer) setModal(k modalKind) {
	r.weatherMu.Lock()
	r.modal = k
	r.weatherMu.Unlock()
}

// modalUp is the modal on the screen as last drawn: a tap goes to it, wherever it lands.
func (r *renderer) modalUp() modalKind {
	r.weatherMu.Lock()
	defer r.weatherMu.Unlock()
	return r.modal
}

// modalCard draws the modal: eyebrow in the accent, the message in at most most lines, then body and
// hint, all centred on the screen.
func (r *renderer) modalCard(eyebrow, message string, most int, body, hint string) {
	cf := r.car()
	r.dimAll(0.9)
	white := color.RGBA{0xff, 0xff, 0xff, 0xff}
	room := r.w - 2*r.cz(96)

	eyebrow = r.fit(cf.eyebrow, eyebrow, room)
	msg := r.lines(cf.modal, message, room, most)
	var bodyLines []string
	if body != "" {
		bodyLines = r.lines(cf.body, body, room, 2)
	}
	eyeH, msgH, bodyH, hintH := r.lineH(cf.eyebrow), r.cz(64), r.cz(42), r.lineH(cf.small)
	h := eyeH + r.cz(20) + len(msg)*msgH
	if len(bodyLines) > 0 {
		h += r.cz(20) + len(bodyLines)*bodyH
	}
	if hint != "" {
		h += r.cz(36) + hintH
	}
	y := (r.h - h) / 2

	centre := func(face font.Face, s string, top, lineH int, c color.RGBA) {
		r.text(face, s, (r.w-r.width(face, s))/2, mid(face, top+lineH/2), c)
	}
	centre(cf.eyebrow, eyebrow, y, eyeH, amber)
	y += eyeH + r.cz(20)
	for _, line := range msg {
		centre(cf.modal, line, y, msgH, white)
		y += msgH
	}
	if len(bodyLines) > 0 {
		y += r.cz(20)
		for _, line := range bodyLines {
			centre(cf.body, line, y, bodyH, lerp(color.RGBA{A: 0xff}, white, 0.7))
			y += bodyH
		}
	}
	if hint != "" {
		y += r.cz(36)
		centre(cf.small, hint, y, hintH, lerp(color.RGBA{A: 0xff}, white, 0.5))
	}
}

// tapToClose is the way out of a modal that only needs putting away.
const tapToClose = "Tap to close"

// eyebrow joins what a modal is and whose it is, in capitals: "ANNOUNCEMENT · KITCHEN". whose is a
// name (who sent it, a calendar's), so only what is translated.
func eyebrow(what, whose string) string {
	e := strings.ToUpper(i18n.T(what))
	if whose != "" {
		e += " · " + strings.ToUpper(i18n.T(i18n.Verbatim(whose)))
	}
	return i18n.Verbatim(e)
}

// announcementModal is an announcement arriving: who it came from, and the words when it had any. The
// voice is the announcement and this is the footnote, so a voice from another room has a name on it,
// and somebody who missed the first second can read the rest.
func (r *renderer) announcementModal(s scene) {
	m := s.announcement
	who := i18n.Verbatim(m.From) // a room's name
	if who == "" {
		who = "Another room"
	}
	if m.Text == "" {
		r.modalCard(eyebrow("ANNOUNCEMENT", ""), who, 2, "", tapToClose)
		return
	}
	r.modalCard(eyebrow("ANNOUNCEMENT", who), i18n.Verbatim(firstUp(m.Text)), 3, "", tapToClose)
}

// firstUp is s starting with a capital: what Home Assistant heard comes in lower case, and as the one
// sentence on the screen it reads better as one.
func firstUp(s string) string {
	for i, c := range s {
		return strings.ToUpper(string(c)) + s[i+len(string(c)):]
	}
	return s
}

// recordingModal says this device's microphone is open and where what it hears is going.
func (r *renderer) recordingModal(s scene) {
	to := "It sends when you stop talking"
	switch {
	case s.announcePeers == 1:
		to = "Goes to 1 other device"
	case s.announcePeers > 1:
		to = i18n.Sprintf("Goes to %d other devices", s.announcePeers)
	}
	r.modalCard(eyebrow("SPEAKING TO THE HOUSE", ""), "Go ahead", 1, to, "Tap to send, hold to cancel")
}

// reminderLines is as many lines as a reminder gets; a longer one ends in an ellipsis.
const reminderLines = 3

// reminderModal is a reminder going off. It stays until somebody taps it, since a reminder nobody saw
// has not reminded anybody.
func (r *renderer) reminderModal(s scene) {
	r.modalCard(eyebrow("REMINDER", s.reminderFrom), i18n.Verbatim(s.reminder.Label), reminderLines, "", tapToClose)
}

// popupModal is an event coming up: how soon and its calendar, its title, and its time.
func (r *renderer) popupModal(s scene, e hass.Event) {
	r.modalCard(eyebrow(popupSoon(e, s.now), popupCalendarName(e.Calendar)), i18n.Verbatim(e.Summary), 2, eventWhen(e, s.now), tapToClose)
}
