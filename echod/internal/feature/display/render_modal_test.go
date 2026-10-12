//go:build !dot && !spot

package display

import (
	"image"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/announce"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/feature/remind"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// One modal is up at a time, the one that matters most, and a call takes the screen from all of them.
func TestOneModalAtATime(t *testing.T) {
	at := time.Date(2026, 9, 26, 14, 50, 0, 0, time.Local)
	e := hass.Event{Calendar: "calendar.family", Summary: "Dentist", Start: at.Add(10 * time.Minute), End: at.Add(time.Hour)}
	said := announce.Message{From: "Kitchen", Text: "dinner is ready"}
	due := remind.Reminder{Label: "Take the trash out"}
	cases := []struct {
		what string
		s    scene
		want modalKind
	}{
		{"nothing", scene{now: at, phase: "idle"}, modalNone},
		{"a pop-up", scene{now: at, phase: "idle", popup: &e}, modalPopup},
		{"an announcement over a pop-up", scene{now: at, phase: "idle", popup: &e, showAnnouncement: true, announcement: said}, modalAnnouncement},
		{"a reminder over an announcement", scene{now: at, phase: "idle", showAnnouncement: true, announcement: said, showReminder: true, reminder: due}, modalReminder},
		{"the microphone over a reminder", scene{now: at, phase: "idle", showReminder: true, reminder: due, announceRecording: true}, modalRecording},
		{"a call over all of them", scene{now: at, phase: "idle", showReminder: true, reminder: due, popup: &e,
			call: phone.State{Phase: phone.Talking, Peer: "104", Since: at}}, modalNone},
	}
	r := newRenderer(image.NewRGBA(image.Rect(0, 0, showWide, showHigh)))
	for _, c := range cases {
		r.draw(c.s)
		if got := r.modalUp(); got != c.want {
			t.Errorf("%s: modal %d up, want %d", c.what, got, c.want)
		}
	}
}

// The header is not drawn over a modal: the screen's top line is as dark as the rest.
func TestTheHeaderStaysUnderAModal(t *testing.T) {
	at := time.Date(2026, 9, 26, 14, 50, 0, 0, time.Local)
	img := image.NewRGBA(image.Rect(0, 0, showWide, showHigh))
	r := newRenderer(img)
	r.draw(scene{now: at, phase: "idle", muted: true, install: "Installing", showReminder: true, reminder: remind.Reminder{Label: "x"}})
	for y := 0; y < r.s(70); y++ {
		for x := 0; x < img.Rect.Dx(); x++ {
			if c := img.RGBAAt(x, y); int(c.R)+int(c.G)+int(c.B) > 3*40 {
				t.Fatalf("a bright pixel %v at %d,%d over the modal", c, x, y)
			}
		}
	}
}
