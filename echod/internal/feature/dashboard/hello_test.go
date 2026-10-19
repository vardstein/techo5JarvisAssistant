//go:build !dot

package dashboard

import (
	"strings"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// The device's own token goes in the hello only when the dashboard is to be its own user's, and
// only when it has one; otherwise the hello is what it always was.
func TestHelloSendsTheTokenOnlyWhenAsked(t *testing.T) {
	cfg := config.Config{}
	cfg.Device.Name = "kitchen"
	cfg.Dashboard = config.Dashboard{Path: "jarvis-display", Kiosk: true}

	h := helloFor(cfg, 960, 480, false, "", "a-token")
	if _, ok := h["token"]; ok {
		t.Error("the token was sent without own user")
	}
	if h["path"] != "/jarvis-display" || h["kiosk"] != true || h["name"] != "kitchen" {
		t.Errorf("hello changed: %v", h)
	}

	cfg.Dashboard.OwnUser = true
	if h := helloFor(cfg, 960, 480, false, "", "a-token"); h["token"] != "a-token" {
		t.Errorf("own user: token %v", h["token"])
	}
	if h := helloFor(cfg, 960, 480, false, "", ""); h["token"] != nil {
		t.Errorf("no token to send, yet %v was", h["token"])
	}
}

// A Show set to its own user hears from dashcast whose page it got, and warns when that is not its
// own: a dashcast too old to say, or one that would not take the token (GENBU-251).
func TestWhoseNoteWarnsWhenTheOwnUserWasNotUsed(t *testing.T) {
	for _, c := range []struct {
		sent  bool
		whose string
		warn  bool
		says  string
	}{
		{true, "own", false, "own Home Assistant user"},
		{true, "", true, "older than own users"},
		{true, "dashcast", true, "would not use"},
		{false, "dashcast", false, "dashcast's user"},
		{false, "", false, ""},
	} {
		note, warn := whoseNote(c.sent, c.whose)
		if warn != c.warn || !strings.Contains(note, c.says) || (c.says == "") != (note == "") {
			t.Errorf("sent %v, whose %q: %q, warn %v", c.sent, c.whose, note, warn)
		}
	}
}
