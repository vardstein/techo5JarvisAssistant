package firmware

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/question"
	"github.com/HuskerMinion/techo5/echod/internal/update"
)

// A version is said when it can be, never in quiet hours or with nothing to say it with, and again every
// remindEvery while it waits; with automatic installs on, only once.
func TestUpdateIsSaidAndRepeated(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	oldSay, oldAsk, oldCan, oldQuiet := say, ask, canSay, quiet
	t.Cleanup(func() { say, ask, canSay, quiet = oldSay, oldAsk, oldCan, oldQuiet })
	var said []string
	say = func(words string) { said = append(said, words) }
	ask = func(string) bool { return false } // no Home Assistant: said, not asked
	able, hush := false, true
	canSay = func() bool { return able }
	quiet = func() bool { return hush }

	u := &Firmware{}
	at := time.Date(2026, 10, 9, 18, 0, 0, 0, time.Local)
	u.tellOf("", at) // nothing offered
	u.tellOf("v1.0.1-dev.6", at)
	if len(said) != 0 {
		t.Fatalf("said %q in quiet hours with nothing to say it with", said)
	}
	hush = false
	u.tellOf("v1.0.1-dev.6", at)
	if len(said) != 0 {
		t.Fatalf("said %q with nothing to say it with", said)
	}
	able = true
	u.tellOf("v1.0.1-dev.6", at)
	u.tellOf("v1.0.1-dev.6", at.Add(time.Hour))
	if len(said) != 1 {
		t.Fatalf("said %d times in the first hour, want once: %q", len(said), said)
	}
	if want := "An update for this Show is ready, version 1.0.1 dev 6. You can install it in Settings, under Updates."; said[0] != want {
		t.Fatalf("said %q, want %q", said[0], want)
	}
	u.tellOf("v1.0.1-dev.6", at.Add(remindEvery))
	if len(said) != 2 {
		t.Fatalf("not said again after %s: %q", remindEvery, said)
	}
	u.tellOf("v1.0.1-dev.7", at.Add(remindEvery+time.Minute))
	if len(said) != 3 {
		t.Fatalf("a newer version was not said: %q", said)
	}

	if err := config.Set().Update().AutoInstall(true); err != nil {
		t.Fatal(err)
	}
	u.tellOf("v1.0.1-dev.7", at.Add(3*remindEvery))
	u.tellOf("v1.0.1-dev.8", at.Add(3*remindEvery))
	u.tellOf("v1.0.1-dev.8", at.Add(5*remindEvery))
	if len(said) != 4 || !strings.Contains(said[3], "tonight") {
		t.Fatalf("with automatic installs, want dev.8 said once and dev.7 not repeated: %q", said)
	}
}

// With Home Assistant to ask through, the notice is a question: yes installs, no waits for the next
// reminder, and either is answered out loud.
func TestUpdateIsAsked(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	oldSay, oldAsk, oldCan, oldQuiet, oldInstall, oldHouse := say, ask, canSay, quiet, install, updateHouse
	t.Cleanup(func() {
		say, ask, canSay, quiet, install, updateHouse = oldSay, oldAsk, oldCan, oldQuiet, oldInstall, oldHouse
	})
	said := make(chan string, 4)
	installed := make(chan bool, 1)
	housed := make(chan bool, 1)
	var asked []string
	say = func(words string) { said <- words }
	ask = func(words string) bool { asked = append(asked, words); return true }
	canSay = func() bool { return true }
	quiet = func() bool { return false }
	install = func(*Firmware) { installed <- true }
	updateHouse = func() { housed <- true }

	u := &Firmware{}
	at := time.Date(2026, 10, 9, 18, 0, 0, 0, time.Local)
	u.tellOf("v1.0.1-dev.6", at)
	if want := "An update for this Show is ready, version 1.0.1 dev 6. Want me to install it now?"; len(asked) != 1 || asked[0] != want {
		t.Fatalf("asked %q, want %q", asked, want)
	}
	if !question.Answer("not now") || <-said != "Okay, I'll remind you later." {
		t.Fatal("no was not answered")
	}

	u.tellOf("v1.0.1-dev.6", at.Add(remindEvery))
	if len(asked) != 2 {
		t.Fatalf("not asked again after %s: %q", remindEvery, asked)
	}
	if !question.Answer("yes, sure") || <-said != "Okay, installing it now." || !<-installed {
		t.Fatal("yes did not install")
	}
	if !<-housed {
		t.Fatal("yes did not ask for the other Shows too")
	}
	select {
	case w := <-said:
		t.Fatalf("also said %q", w)
	default:
	}
}

func TestSpokenVersion(t *testing.T) {
	for v, want := range map[string]string{"v1.0.1": "1.0.1", "v1.0.1-dev.6": "1.0.1 dev 6", "v2.0.0-staging.31": "2.0.0 staging 31", "v1.0.1-stable.40": "1.0.1 stable 40"} {
		if got := spoken(v); got != want {
			t.Errorf("spoken(%q) = %q, want %q", v, got, want)
		}
	}
}

// A check with an unset clock is tried again in a minute, not in an hour.
func TestNextCheck(t *testing.T) {
	for _, c := range []struct {
		err  error
		want string
	}{
		{nil, checkEvery.String()},
		{fmt.Errorf("fetch: %w", update.ErrClock), clockRetry.String()},
		{errors.New("no route to host"), failRetry.String()},
	} {
		if got := nextCheck(c.err).String(); got != c.want {
			t.Errorf("nextCheck(%v) = %s, want %s", c.err, got, c.want)
		}
	}
}
