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
	// fetchRetry is how long a house version that could not be fetched waits before the next try: half a
	// minute after the first miss, twice that after each further one, up to fetchRetryMost. A router rule
	// that lets GitHub in shows within the minute, and a Show with no way out does not keep knocking.
	fetchRetry     = 30 * time.Second
	fetchRetryMost = 10 * time.Minute
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
	// retrying is a move whose fetch failed: the next try moves too. The version counts as seen by then,
	// and bathroomShow5, shut out of GitHub on its first move, asked instead.
	retrying bool
	// failed is a house version that would not install here.
	failed string
	// askedThisBoot, asked and askedAt are the last "should I move?" question.
	askedThisBoot bool
	asked         string
	askedAt       time.Time
	// busy is what keeps somebody's use of the Show from being cut by a restart, if anything does.
	busy            Hold
	quiet           bool
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
	case f.house == f.failed, !f.committed, f.held, f.busy.Holding():
		return stay
	case f.seen == "" || f.movedWhileUp || f.retrying:
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
	fetchMisses   int
	status        string
	// wait is what the move waits for, while that is all it waits for: the screen says it.
	wait Hold
}

// HoldKind is what a following Show waits for before it moves to the house version.
type HoldKind int

const (
	HoldNone HoldKind = iota
	HoldMusic
	HoldRinging
	HoldCall
	HoldInstall
	HoldTimer
	HoldAlarm
)

// Hold is what keeps a following Show where it is for now: the update waits for it, and update_status
// and the screen say so (GENBU-247).
type Hold struct {
	Kind HoldKind
	// Name is the timer's, empty for everything else and for a timer nobody named.
	Name string
	// At is when the timer ends or the alarm goes off.
	At time.Time
}

func (h Hold) Holding() bool { return h.Kind != HoldNone }

// line finishes update_status's "Moving to v once ...".
func (h Hold) line() string {
	at := h.At.Local().Format("15:04")
	switch h.Kind {
	case HoldMusic:
		return "the music stops"
	case HoldRinging:
		return "the ringing stops"
	case HoldCall:
		return "the call ends"
	case HoldInstall:
		return "its own install ends"
	case HoldTimer:
		if h.Name == "" {
			return "the timer ends at " + at
		}
		return "timer " + h.Name + " ends at " + at
	case HoldAlarm:
		return "the alarm at " + at + " has rung"
	}
	return "nobody is using this Show"
}

// inUse is somebody using the Show in a way a restart would cut: music playing or paused a short while
// ago, a call, something ringing, or a timer or an alarm going off before the Show would be back.
var inUse = func(now time.Time) Hold {
	if playing, paused := media.Get().ScreenState(); musicHolds(playing, paused, now) {
		return Hold{Kind: HoldMusic}
	}
	switch {
	case ring.IsSounding():
		return Hold{Kind: HoldRinging}
	case phone.Get().Busy():
		return Hold{Kind: HoldCall}
	case update.Installing():
		return Hold{Kind: HoldInstall}
	}
	if h := timerHolds(timer.Get().List(now), now); h.Holding() {
		return h
	}
	if next := alarm.Get().View(now).Next; next != nil && next.At.Sub(now) < dueSoon {
		return Hold{Kind: HoldAlarm, At: next.At}
	}
	return Hold{}
}

// timerHolds is the soonest timer that ends before the Show would be back from a restart. Every running
// timer used to hold, so a timer set for the evening kept the Show off the house version all afternoon.
// A longer one outlives the restart: the Show's own are saved as their finish time and come back, and
// Home Assistant keeps the ones set by voice and sends the finish to the Show once it is connected again,
// which rings it even though the countdown itself is gone. That last part is read in Home Assistant's
// code (intent/timers.py), not yet tried on a Show.
func timerHolds(list []timer.Countdown, now time.Time) Hold {
	for _, c := range list {
		if c.Active && c.Left < dueSoon {
			return Hold{Kind: HoldTimer, Name: c.Name, At: now.Add(c.Left)}
		}
	}
	return Hold{}
}

// pausedHolds is how long paused music keeps a following Show from moving: as long as the Show keeps a
// paused track of its own before ending it (media's stoppedFor). Music Assistant leaves a queue paused
// for as long as nobody touches it, so without an end a paused song would hold the update for good.
const pausedHolds = 30 * time.Minute

// paused is when the Show's music was first seen paused, zero while it is not. It is only looked at
// while the Show has somewhere to move, so a pause from before that counts from then: later, never
// sooner.
var paused struct {
	mu    sync.Mutex
	since time.Time
}

// musicHolds is the music keeping the Show in use. What the screen shows is what counts: the Show's
// own stream, and Music Assistant's through Sendspin, which stops the Show's own stream when it takes
// the speaker, so the Show's own stream alone reads as idle in the middle of a song (GENBU-134).
func musicHolds(playing, isPaused bool, now time.Time) bool {
	paused.mu.Lock()
	defer paused.mu.Unlock()
	if !isPaused {
		paused.since = time.Time{}
		return playing
	}
	if paused.since.IsZero() {
		paused.since = now
	}
	return now.Sub(paused.since) < pausedHolds
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
		running: layout.Version, house: v, seen: c.HouseSeen, movedWhileUp: h.movedWhileUp, retrying: h.fetchMisses > 0,
		failed: c.HouseFailed, askedThisBoot: h.askedThisBoot, asked: c.HouseAsked, askedAt: c.HouseAskedAt,
		quiet: quiet(), committed: committed(), held: update.Installing() || now.Before(h.retryAt), now: now,
	}
	h.mu.Unlock()
	if f.house != f.running {
		f.busy = inUse(now)
	}

	switch decide(f) {
	case align:
		slog.Info("moving to the house version", "house", v, "running", layout.Version)
		u.houseStatus("Moving to " + v)
		u.alignTo(ctx, v, false)
		return
	case askFirst:
		u.askToAlign(v, now)
	default:
		if asksAboutWaiting(s, f, nodeName()) {
			u.tellHouse(s.waiting, now)
		}
	}
	u.houseStatus(houseLine(f, u.mainName(), u.mainMissing(), now))
	h.mu.Lock()
	h.wait = waitShown(f)
	h.mu.Unlock()
}

// waitShown is the hold the move waits for, when houseLine says the Show waits for it.
func waitShown(f following) Hold {
	if f.house == f.running || f.house == f.failed || !f.committed {
		return Hold{}
	}
	return f.busy
}

// Waiting is what this Show's move to the house version waits for, for the screen.
func (u *Firmware) Waiting() Hold {
	if u.role(time.Now()) != roleFollower {
		return Hold{}
	}
	u.house.mu.Lock()
	defer u.house.mu.Unlock()
	return u.house.wait
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
	case f.busy.Holding():
		return "Moving to " + f.house + " once " + f.busy.line()
	}
	if update.Newer(f.running, f.house) {
		// its own card offers nothing older, so it has no Install to point at
		return "Your other Shows are on the older " + f.house + "; this one steps back when you say yes to its " +
			"question, or they all move up with an update on the Genbu Show card"
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
		safe.Go("house version install", func() { u.alignTo(context.Background(), v, true) })
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

// alignTo installs the house version's own release, going back if it has to. asked is somebody
// wanting it now, whose failure to even fetch it the screen says; the house's own tries only say so in
// the update status.
func (u *Firmware) alignTo(ctx context.Context, v string, asked bool) {
	u.busy.Add(1)
	defer u.busy.Add(-1)
	m, err := fetchTag(ctx, v)
	if err == nil && !m.Serves() {
		err = errors.New("the release has nothing for this Show")
	}
	if err != nil {
		slog.Error("fetching the house version failed", "house", v, "err", err)
		u.house.mu.Lock()
		wait := min(fetchRetry<<u.house.fetchMisses, fetchRetryMost)
		if wait < fetchRetryMost {
			u.house.fetchMisses++
		}
		u.house.retryAt = time.Now().Add(wait)
		u.house.mu.Unlock()
		u.houseStatus("Version " + v + " could not be fetched: " + err.Error())
		if asked {
			u.fail(v, err)
		}
		return
	}
	u.house.mu.Lock()
	u.house.fetchMisses = 0
	u.house.mu.Unlock()
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
