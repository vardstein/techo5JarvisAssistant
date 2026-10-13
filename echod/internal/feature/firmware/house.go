package firmware

import (
	"context"
	"errors"
	"log/slog"
	"slices"
	"sync"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/alarm"
	"github.com/HuskerMinion/techo5/echod/internal/feature/hastate"
	"github.com/HuskerMinion/techo5/echod/internal/feature/media"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/feature/question"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/feature/timer"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
	"github.com/HuskerMinion/techo5/echod/internal/update"
)

// One update point per house (docs/update-leader.md). Home Assistant's genbu integration publishes the
// house version and the main update Shows. A main Show, or a Show in a house without the integration,
// works as before. Every other Show follows: it never reads its channel and moves to the house version,
// up or down. Since Genbu 1.4.0 there is no main Show; the house update waits on Home Assistant's update
// card, and the one Show Genbu names asks about it out loud (GENBU-119, GENBU-125).
//
// A Show only reads here. Which Show is main is decided in Home Assistant alone, so two Shows can never
// both take the job.

const (
	houseEntity = "sensor.genbu_house_version"
	// attrMains is the main update Shows' node names, attrMissing since when Home Assistant has seen the
	// main Show unavailable (empty while it is there).
	attrMains   = "main_shows"
	attrMissing = "main_missing_since"
	// attrWaiting is the house update waiting on the card, attrAsks the one Show that asks about it, so
	// it is not asked in every room at once.
	attrWaiting = "update_waiting"
	attrAsks    = "update_asks"

	// houseGrace is how long after Home Assistant connects a missing house sensor means the house has
	// none. Its states arrive within moments of the subscription; until then the Show stays quiet.
	houseGrace = 30 * time.Second
	// aloneGrace is the same for a Show that has not reached Home Assistant since it started: after this
	// long it works on its own channel, as a Show in a house without Home Assistant has to.
	aloneGrace = 10 * time.Minute
	// houseLook is how often a following Show looks again, for a moment when nobody is using it.
	houseLook = time.Minute
	// askAgain is how long after "not now" a Show that was off asks again, if it has not restarted.
	askAgain = 24 * time.Hour
	// dueSoon is how close an alarm may be before moving would get in its way.
	dueSoon = 30 * time.Minute
	// missingAfter is how long the main Show has to be gone before a following Show mentions it.
	missingAfter = time.Hour
	// fetchRetry is how long a house version that could not be fetched waits before the next try.
	fetchRetry = 10 * time.Minute
)

type role int

const (
	// roleUnknown is a Show that cannot tell yet: just connected, or the integration's sensor has no
	// version. It stays where it is and says nothing.
	roleUnknown role = iota
	roleFree
	roleMain
	roleFollower
)

func (r role) String() string {
	return [...]string{"unknown", "free", "main", "follower"}[r]
}

// roleOf is the Show's part in the house, from what Home Assistant last sent.
func roleOf(version string, haveVersion bool, mains []string, haveMains bool, node string, waited bool) role {
	switch {
	case !haveVersion && !haveMains:
		if waited {
			return roleFree
		}
		return roleUnknown
	case slices.Contains(mains, node):
		return roleMain
	case update.ValidVersion(version) == nil:
		return roleFollower
	}
	return roleUnknown
}

type move int

const (
	stay move = iota
	align
	askFirst
)

// following is what a following Show decides from.
type following struct {
	running, house string
	// seen is the house version last seen while following, empty for a Show that never followed.
	seen string
	// movedWhileUp is the house version changing while this process ran.
	movedWhileUp bool
	// failed is a house version that would not install here.
	failed string
	// askedThisBoot, asked and askedAt are the last "should I move?" question.
	askedThisBoot   bool
	asked           string
	askedAt         time.Time
	busy, quiet     bool
	committed, held bool // held: an install is running, or a fetch failed a moment ago
	now             time.Time
}

// decide is the table in docs/update-leader.md: a Show that has just joined or was running when the
// house moved follows as soon as nobody is using it; one that was off asks first, once a boot or once a
// day, never in quiet hours.
func decide(f following) move {
	switch {
	case f.house == "" || f.house == f.running:
		return stay
	case f.house == f.failed, !f.committed, f.held, f.busy:
		return stay
	case f.seen == "" || f.movedWhileUp:
		return align
	case f.quiet:
		return stay
	case f.askedThisBoot && f.asked == f.house && f.now.Sub(f.askedAt) < askAgain:
		return stay
	}
	return askFirst
}

// house is the following Show's state for this process.
type house struct {
	// step is held for one look, so a change arriving while the Show moves waits for the next.
	step          sync.Mutex
	mu            sync.Mutex
	started       time.Time
	connected     time.Time
	movedWhileUp  bool
	askedThisBoot bool
	retryAt       time.Time
	status        string
}

// inUse is somebody using the Show: something playing, a call, a timer counting or ringing, or an alarm
// about to go off.
var inUse = func(now time.Time) bool {
	if playing, _ := media.Get().Playing(); playing {
		return true
	}
	if ring.IsSounding() || update.Installing() || phone.Get().Busy() {
		return true
	}
	for _, c := range timer.Get().List(now) {
		if c.Active {
			return true
		}
	}
	next := alarm.Get().View(now).Next
	return next != nil && next.At.Sub(now) < dueSoon
}

// sighting is what Home Assistant last sent about the house; have* says whether a value arrived at all.
type sighting struct {
	version, mains, missing string
	waiting, asks           string
	haveVersion, haveMains  bool
}

var lookHouse = func() sighting {
	ha := hastate.Get()
	var s sighting
	s.version, s.haveVersion = ha.Value(houseEntity, "")
	s.mains, s.haveMains = ha.Value(houseEntity, attrMains)
	s.missing, _ = ha.Value(houseEntity, attrMissing)
	s.waiting, _ = ha.Value(houseEntity, attrWaiting)
	s.asks, _ = ha.Value(houseEntity, attrAsks)
	return s
}

var (
	fetchTag  = update.FetchTag
	committed = update.Committed
)

// nodeName is this Show's ESPHome node name, which is how the integration names it.
var nodeName = func() string { return layout.Slug(config.Get().Device.Name) }

func (u *Firmware) followHouse() {
	u.house.started = time.Now()
	hastate.Get().Follow("firmware",
		hastate.Key{Entity: houseEntity},
		hastate.Key{Entity: houseEntity, Attribute: attrMains},
		hastate.Key{Entity: houseEntity, Attribute: attrMissing},
		hastate.Key{Entity: houseEntity, Attribute: attrWaiting},
		hastate.Key{Entity: houseEntity, Attribute: attrAsks})
	hastate.Get().Changed.Listen(func(up hastate.Update) {
		if up.Entity != houseEntity {
			return
		}
		if up.Attribute == "" && !up.First {
			u.house.mu.Lock()
			u.house.movedWhileUp = true
			u.house.mu.Unlock()
		}
		safe.Go("house version", func() { u.houseStep(context.Background(), time.Now()) })
	})
	component.Subscribed.Listen(func(struct{}) {
		u.house.mu.Lock()
		u.house.connected = time.Now()
		u.house.mu.Unlock()
	})
}

// role is the Show's part in the house now.
func (u *Firmware) role(now time.Time) role {
	s := lookHouse()
	u.house.mu.Lock()
	connected := u.house.connected
	u.house.mu.Unlock()
	waited := now.Sub(connected) >= houseGrace
	if connected.IsZero() {
		waited = now.Sub(u.house.started) >= aloneGrace
	}
	return roleOf(s.version, s.haveVersion, hastate.Options(s.mains), s.haveMains, nodeName(), waited)
}

// speaksForItself is a Show that reads its own channel and tells about what it finds.
func (u *Firmware) speaksForItself() bool {
	r := u.role(time.Now())
	return r == roleFree || r == roleMain
}

// houseVersion is the version a following Show moves to, empty when it is not following.
func (u *Firmware) houseVersion() string {
	if u.role(time.Now()) != roleFollower {
		return ""
	}
	return lookHouse().version
}

// publishHouse is a following Show's update card: the house version as the latest, so Install on it
// means "go to the house version", up or down.
func (u *Firmware) publishHouse(v string) {
	if u.busy.Load() > 0 || update.Installing() {
		return // the install's progress is on the card
	}
	u.entity.Set(esphome.UpdateState{
		CurrentVersion: layout.Version,
		LatestVersion:  v,
		Title:          u.title(),
	})
}

// houseLoop looks again every houseLook, for a moment when nobody is using the Show.
func (u *Firmware) houseLoop(ctx context.Context) {
	t := time.NewTicker(houseLook)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			u.houseStep(ctx, time.Now())
		}
	}
}

// houseStep is one look at the house version by a following Show.
func (u *Firmware) houseStep(ctx context.Context, now time.Time) {
	if u.role(now) != roleFollower || !u.house.step.TryLock() {
		return
	}
	defer u.house.step.Unlock()
	s := lookHouse()
	v := s.version
	c := config.Get().Update
	if c.HouseSeen != v {
		if err := config.Set().Update().HouseSeen(v); err != nil {
			slog.Error("saving the house version failed", "err", err)
		}
	}
	u.publishHouse(v)

	h := &u.house
	h.mu.Lock()
	f := following{
		running: layout.Version, house: v, seen: c.HouseSeen, movedWhileUp: h.movedWhileUp,
		failed: c.HouseFailed, askedThisBoot: h.askedThisBoot, asked: c.HouseAsked, askedAt: c.HouseAskedAt,
		quiet: quiet(), committed: committed(), held: update.Installing() || now.Before(h.retryAt), now: now,
	}
	h.mu.Unlock()
	f.busy = f.house != f.running && inUse(now)

	switch decide(f) {
	case align:
		slog.Info("moving to the house version", "house", v, "running", layout.Version)
		u.houseStatus("Moving to " + v)
		u.alignTo(ctx, v)
		return
	case askFirst:
		u.askToAlign(v, now)
	default:
		if asksAboutWaiting(s, f, nodeName()) {
			u.tellHouse(s.waiting, now)
		}
	}
	u.houseStatus(houseLine(f, u.mainName(), u.mainMissing(), now))
}

// asksAboutWaiting is this Show being the one to ask about the house update: it is on the house version
// itself, and Genbu named it.
func asksAboutWaiting(s sighting, f following, node string) bool {
	return f.house == f.running && s.asks == node && update.ValidVersion(s.waiting) == nil &&
		update.Newer(s.waiting, f.house)
}

// houseLine is the following Show's one line in update_status.
func houseLine(f following, main, missing string, now time.Time) string {
	if since, err := time.Parse(time.RFC3339, missing); err == nil && now.Sub(since) >= missingAfter {
		at := since.Local().Format("15:04")
		if since.Local().YearDay() != now.Local().YearDay() {
			at = since.Local().Format("2 Jan 15:04")
		}
		return "Main update Show " + main + " not reachable since " + at
	}
	switch {
	case f.house == f.running:
		return "Following " + main + " · version " + f.house
	case f.house == f.failed:
		return "Version " + f.house + " did not install here; staying on " + f.running
	case !f.committed:
		return "Waiting for this version to settle before moving to " + f.house
	case f.busy:
		return "Moving to " + f.house + " once nobody is using this Show"
	}
	return "Your other Shows are on " + f.house + "; install it from the update card"
}

func (u *Firmware) mainName() string {
	mains := hastate.Options(lookHouse().mains)
	if len(mains) == 0 {
		return "the house" // Genbu 1.4.0 and later
	}
	return mains[0]
}

func (u *Firmware) mainMissing() string {
	return lookHouse().missing
}

func (u *Firmware) houseStatus(line string) {
	u.house.mu.Lock()
	same := u.house.status == line
	u.house.status = line
	u.house.mu.Unlock()
	if !same {
		u.status.Set(component.Fit(line))
	}
}

// askToAlign asks, out loud through Home Assistant, whether to move to the house version.
func (u *Firmware) askToAlign(v string, now time.Time) {
	if !canSay() {
		return // asked on a later look, when it can be
	}
	u.house.mu.Lock()
	u.house.askedThisBoot = true
	u.house.mu.Unlock()
	if err := config.Set().Update().HouseAsked(v, now); err != nil {
		slog.Error("saving the house question failed", "err", err)
		return
	}
	question.Ask("house", answerWithin, func() {
		slog.Info("moving to the house version, as asked", "house", v)
		say(i18n.T("Okay, installing it now."))
		safe.Go("house version install", func() { u.alignTo(context.Background(), v) })
	}, func() {
		say(i18n.T("Okay, I'll remind you later."))
	}, func() {
		ask(i18n.T("Sorry, I didn't catch that. Should I switch to the version of your other Shows now, yes or no?"))
	})
	if !ask(i18n.F("This Show is on version {running}, your other Shows are on {house}. Want me to switch to it now?",
		"running", spoken(layout.Version), "house", spoken(v))) {
		question.Withdraw("house")
	}
}

// alignTo installs the house version's own release, going back if it has to.
func (u *Firmware) alignTo(ctx context.Context, v string) {
	u.busy.Add(1)
	defer u.busy.Add(-1)
	m, err := fetchTag(ctx, v)
	if err == nil && !m.Serves() {
		err = errors.New("the release has nothing for this Show")
	}
	if err != nil {
		slog.Error("fetching the house version failed", "house", v, "err", err)
		u.house.mu.Lock()
		u.house.retryAt = time.Now().Add(fetchRetry)
		u.house.mu.Unlock()
		u.houseStatus("Version " + v + " could not be fetched: " + err.Error())
		return
	}
	if err := u.put(ctx, m, update.Align); err != nil && !errors.Is(err, update.ErrInstalling) {
		if serr := config.Set().Update().HouseFailed(v); serr != nil {
			slog.Error("saving the failed house version failed", "err", serr)
		}
	}
}

// rolledBackFromHouse remembers a house version the slot trial took out, so it is not tried again by
// itself. Pressing Install on the update card still tries.
func (u *Firmware) rolledBackFromHouse() {
	v := u.rolledBack
	if v == "" || v != config.Get().Update.HouseSeen {
		return
	}
	if err := config.Set().Update().HouseFailed(v); err != nil {
		slog.Error("saving the rolled back house version failed", "err", err)
	}
}
