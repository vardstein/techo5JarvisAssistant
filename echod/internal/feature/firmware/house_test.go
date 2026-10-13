package firmware

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/question"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/update"
)

func TestRoleComesFromHomeAssistant(t *testing.T) {
	const me = "kitchen_show"
	for _, c := range []struct {
		name        string
		version     string
		haveVersion bool
		mains       []string
		haveMains   bool
		waited      bool
		want        role
	}{
		{"nothing yet, just connected", "", false, nil, false, false, roleUnknown},
		{"nothing after the wait: no integration", "", false, nil, false, true, roleFree},
		{"named a main", "v1.0.3-stable.52", true, []string{"hall_show", me}, true, true, roleMain},
		{"main before the version arrives", "", false, []string{me}, true, false, roleMain},
		{"another Show is main", "v1.0.3-stable.52", true, []string{"hall_show"}, true, true, roleFollower},
		{"no main Show picked yet", "v1.0.3-stable.52", true, nil, true, true, roleFollower},
		{"sensor without a version", "unavailable", true, []string{"hall_show"}, true, true, roleUnknown},
		{"version arrived, mains not yet", "v1.0.3-stable.52", true, nil, false, false, roleFollower},
	} {
		if got := roleOf(c.version, c.haveVersion, c.mains, c.haveMains, me, c.waited); got != c.want {
			t.Errorf("%s: %s, want %s", c.name, got, c.want)
		}
	}
}

func TestFollowingShowDecides(t *testing.T) {
	now := time.Date(2026, 10, 11, 18, 0, 0, 0, time.Local)
	base := following{running: "v1.0.2-stable.48", house: "v1.0.3-stable.52", seen: "v1.0.2-stable.48", committed: true, now: now}
	for _, c := range []struct {
		name string
		edit func(*following)
		want move
	}{
		{"already on the house version", func(f *following) { f.house = f.running }, stay},
		{"was off: asks", func(f *following) {}, askFirst},
		{"fresh install, house newer: moves", func(f *following) { f.seen = "" }, align},
		{"fresh install, house older: moves back", func(f *following) { f.seen, f.house = "", "v1.0.1-stable.40" }, align},
		{"house moved while running: moves", func(f *following) { f.movedWhileUp = true }, align},
		{"moves only when nobody uses it", func(f *following) { f.movedWhileUp, f.busy = true, true }, stay},
		{"a version that failed here is not tried again", func(f *following) { f.seen, f.failed = "", f.house }, stay},
		{"not while its own update is on trial", func(f *following) { f.seen, f.committed = "", false }, stay},
		{"not while an install runs or a fetch just failed", func(f *following) { f.seen, f.held = "", true }, stay},
		{"never asks in quiet hours", func(f *following) { f.quiet = true }, stay},
		{"never asks while in use", func(f *following) { f.busy = true }, stay},
		{"asked this boot: waits", func(f *following) {
			f.askedThisBoot, f.asked, f.askedAt = true, f.house, now.Add(-time.Hour)
		}, stay},
		{"asked this boot a day ago: asks again", func(f *following) {
			f.askedThisBoot, f.asked, f.askedAt = true, f.house, now.Add(-askAgain)
		}, askFirst},
		{"asked before the restart: asks again", func(f *following) {
			f.asked, f.askedAt = f.house, now.Add(-time.Hour)
		}, askFirst},
		{"asked about another version: asks", func(f *following) {
			f.askedThisBoot, f.asked, f.askedAt = true, "v1.0.3-stable.51", now.Add(-time.Hour)
		}, askFirst},
	} {
		f := base
		c.edit(&f)
		if got := decide(f); got != c.want {
			t.Errorf("%s: %d, want %d", c.name, got, c.want)
		}
	}
}

func TestHouseLine(t *testing.T) {
	now := time.Date(2026, 10, 11, 18, 0, 0, 0, time.Local)
	f := following{running: "v1.0.3", house: "v1.0.3", committed: true}
	if got := houseLine(f, "hall_show", "", now); got != "Following hall_show · version v1.0.3" {
		t.Errorf("on the house version: %q", got)
	}
	gone := now.Add(-2 * time.Hour).Format(time.RFC3339)
	if got := houseLine(f, "hall_show", gone, now); got != "Main update Show hall_show not reachable since 16:00" {
		t.Errorf("main gone two hours: %q", got)
	}
	if got := houseLine(f, "hall_show", now.Add(-time.Minute).Format(time.RFC3339), now); !strings.HasPrefix(got, "Following") {
		t.Errorf("main gone a minute is not worth a word: %q", got)
	}
	if got := houseLine(f, "the house", "", now); got != "Following the house · version v1.0.3" {
		t.Errorf("no main Show (Genbu 1.4.0): %q", got)
	}
	f.running, f.failed = "v1.0.2", "v1.0.3"
	if got := houseLine(f, "hall_show", "", now); got != "Version v1.0.3 did not install here; staying on v1.0.2" {
		t.Errorf("failed: %q", got)
	}
}

// followingShow is a Show that Home Assistant says follows hall_show at house version v.
func followingShow(t *testing.T, v string, mains string) *Firmware {
	t.Helper()
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	oldLook, oldFetch, oldCommitted, oldUse, oldNode := lookHouse, fetchTag, committed, inUse, nodeName
	oldSay, oldAsk, oldCan, oldQuiet := say, ask, canSay, quiet
	t.Cleanup(func() {
		lookHouse, fetchTag, committed, inUse, nodeName = oldLook, oldFetch, oldCommitted, oldUse, oldNode
		say, ask, canSay, quiet = oldSay, oldAsk, oldCan, oldQuiet
	})
	lookHouse = func() sighting {
		return sighting{version: v, haveVersion: true, mains: mains, haveMains: true}
	}
	nodeName = func() string { return "kitchen_show" }
	committed = func() bool { return true }
	inUse = func(time.Time) bool { return false }
	fetchTag = func(context.Context, string) (update.Manifest, error) {
		t.Fatal("fetched a release")
		return update.Manifest{}, nil
	}
	say = func(string) {}
	canSay = func() bool { return true }
	quiet = func() bool { return false }
	ask = func(string) bool { t.Fatal("asked"); return false }
	u := build()
	u.house.connected = time.Now()
	return u
}

// A following Show offers the house version, older or newer, and never tells about its channel.
func TestFollowingShowOnlyOffersTheHouseVersion(t *testing.T) {
	u := followingShow(t, "v0.0.0-alpha.1", "['hall_show']")
	if got := u.Offered(); got != "v0.0.0-alpha.1" {
		t.Errorf("offered %q, want the older house version", got)
	}
	if u.speaksForItself() || u.channelOffered() != "" {
		t.Error("a following Show speaks about its channel")
	}
	lookHouse = func() sighting {
		return sighting{version: layout.Version, haveVersion: true, mains: "['hall_show']", haveMains: true}
	}
	if got := u.Offered(); got != "" {
		t.Errorf("offered %q on the house version", got)
	}
	lookHouse = func() sighting {
		return sighting{version: layout.Version, haveVersion: true, mains: "['kitchen_show']", haveMains: true}
	}
	if !u.speaksForItself() {
		t.Error("the main update Show does not speak for itself")
	}
}

// A fresh Show moves without asking; a fetch that fails waits before trying again.
func TestFreshFollowingShowMovesWithoutAsking(t *testing.T) {
	u := followingShow(t, "v1.0.3-stable.52", "['hall_show']")
	var fetched []string
	fetchTag = func(_ context.Context, tag string) (update.Manifest, error) {
		fetched = append(fetched, tag)
		return update.Manifest{}, errors.New("no network")
	}
	now := time.Now()
	u.houseStep(context.Background(), now)
	u.houseStep(context.Background(), now.Add(time.Minute))
	if len(fetched) != 1 || fetched[0] != "v1.0.3-stable.52" {
		t.Fatalf("fetched %q, want the house version once until the retry", fetched)
	}
	if got := config.Get().Update.HouseSeen; got != "v1.0.3-stable.52" {
		t.Errorf("house version seen %q", got)
	}
	if got := config.Get().Update.HouseFailed; got != "" {
		t.Errorf("a fetch that failed marked the version as not installing here: %q", got)
	}
}

// A Show that was off asks once a boot, and not in quiet hours.
func TestShowThatWasOffAsksOnce(t *testing.T) {
	u := followingShow(t, "v1.0.3-stable.52", "['hall_show']")
	if err := config.Set().Update().HouseSeen("v1.0.2-stable.48"); err != nil {
		t.Fatal(err)
	}
	var asked []string
	ask = func(words string) bool { asked = append(asked, words); return true }
	hush := true
	quiet = func() bool { return hush }

	now := time.Now()
	u.houseStep(context.Background(), now)
	if len(asked) != 0 {
		t.Fatalf("asked in quiet hours: %q", asked)
	}
	hush = false
	u.houseStep(context.Background(), now.Add(time.Minute))
	u.houseStep(context.Background(), now.Add(2*time.Minute))
	if len(asked) != 1 || !strings.Contains(asked[0], "1.0.3 stable 52") {
		t.Fatalf("asked %q, want once about the house version", asked)
	}
	if c := config.Get().Update; c.HouseAsked != "v1.0.3-stable.52" {
		t.Errorf("question not saved: %+v", c)
	}
}

// GENBU-119: with Genbu 1.4.0 the Show Genbu names asks about the update waiting on the card, and a yes
// moves the house without installing here first. The other Shows stay quiet.
func TestNamedShowAsksAboutTheHouseUpdate(t *testing.T) {
	u := followingShow(t, layout.Version, "[]")
	if err := config.Set().Update().HouseSeen(layout.Version); err != nil {
		t.Fatal(err)
	}
	oldInstall, oldHouse := install, updateHouse
	t.Cleanup(func() { install, updateHouse = oldInstall, oldHouse })
	install = func(*Firmware) { t.Fatal("installed here first") }
	housed := make(chan bool, 1)
	updateHouse = func() { housed <- true }
	waiting := "v99.0.0"
	asks := "hall_show"
	lookHouse = func() sighting {
		return sighting{version: layout.Version, haveVersion: true, mains: "[]", haveMains: true,
			waiting: waiting, asks: asks}
	}
	var asked []string
	ask = func(words string) bool { asked = append(asked, words); return true }

	now := time.Now()
	u.houseStep(context.Background(), now)
	if len(asked) != 0 {
		t.Fatalf("a Show Genbu did not name asked: %q", asked)
	}
	asks = "kitchen_show"
	u.houseStep(context.Background(), now.Add(time.Minute))
	u.houseStep(context.Background(), now.Add(2*time.Minute))
	if want := "An update for your Shows is ready, version 99.0.0. Want me to install it now?"; len(asked) != 1 || asked[0] != want {
		t.Fatalf("asked %q, want %q once", asked, want)
	}
	if !question.Answer("yes") || !<-housed {
		t.Fatal("yes did not move the house")
	}
	if got := u.house.status; got != "Following the house · version "+layout.Version {
		t.Errorf("status %q", got)
	}

	// Not newer than the house, or skipped (Genbu sends it empty): nothing to ask.
	f := following{running: "v1.0.3", house: "v1.0.3"}
	for _, w := range []string{"", "v1.0.3", "v1.0.2", "soon"} {
		if asksAboutWaiting(sighting{waiting: w, asks: "kitchen_show"}, f, "kitchen_show") {
			t.Errorf("asks about %q", w)
		}
	}
	// A Show still behind the house moves first; it does not ask about the next one.
	f.running = "v1.0.2"
	if asksAboutWaiting(sighting{waiting: "v1.0.4", asks: "kitchen_show"}, f, "kitchen_show") {
		t.Error("a Show behind the house asks about the house update")
	}
}
