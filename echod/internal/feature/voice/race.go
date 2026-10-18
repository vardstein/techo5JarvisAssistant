package voice

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"math"
	"strconv"
	"sync/atomic"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/led"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/mic"
)

// Which Show answers when several hear the same wake word (GENBU-224, wiki 734).
//
// Home Assistant's own rule is first come: whichever device opens its run first keeps it, and that is
// whichever one's model happened to fire first, not the one being spoken to. So a Show that hears its
// wake word first says how well it heard it, on Home Assistant's bus, and waits. The Genbu integration
// collects what every Show in the house heard, picks the one the voice was loudest at, and tells each
// one whether it won. Only the winner opens a run.
//
// Every part of that can fail without the house going deaf. The integration renews a lease while it
// runs (wake_race), and a Show with no lease does not wait at all, which is a house without the
// integration and every Show before this. A Show with a lease waits verdictWait at most and then opens
// its run anyway, leaving Home Assistant's first come to decide; the tone and the ring then wait for
// Home Assistant to say the run is this Show's (confirmWait), so the Shows that lost stay quiet.
const (
	// WakeEvent is what a Show fires on Home Assistant's bus when it hears its wake word in a race.
	WakeEvent = "esphome.genbu_wake"

	// raceLease is how long a wake_race call holds. The integration renews it every refresh, so this
	// only has to outlast a few missed ones; after it, an integration that went away is not waited for.
	raceLease = 3 * time.Minute

	// verdictWait is the longest a Show waits for its verdict. The integration answers within its
	// window, 80 ms from the first wake it heard, plus Wi-Fi both ways; this is for an integration that
	// does not answer at all.
	verdictWait = 500 * time.Millisecond

	// confirmWait is how long a turn opened without a verdict holds its tone, its ring and its duck,
	// waiting for Home Assistant's STT_START. That arrives within tens of milliseconds when the run is
	// this Show's; a pipeline that never sends it still gets a turn that looks like one.
	confirmWait = time.Second
)

// Wake is what the detector knew when the wake word fired.
type Wake struct {
	Heard mic.Heard
	// Score is what the model scored at the crossing, Cutoff what it crossed. How far over it went is
	// how sure the model was.
	Score, Cutoff float64
}

// race is a wake waiting for its verdict.
type race struct {
	id    string
	slot  int
	at    time.Time
	timer *time.Timer
	// over is the turn a win takes over, for a Show that was busy when it heard the word; "" when idle.
	over string
}

// lease is until when this Show races its wake words, in Unix nanoseconds; zero is never.
var lease atomic.Int64

// racing is whether a wake word should wait for a verdict now.
func racing() bool { return time.Now().UnixNano() < lease.Load() }

// Woke is a wake word heard by the microphones. Without a race to run it is Start; with one, the turn
// waits for its verdict.
func (v *Voice) Woke(slot int, w Wake) {
	if !racing() {
		v.Start(slot)
		return
	}
	// Whichever Show answers, whoever said it at a ringing alarm wants this one quiet too.
	if ring.IsSounding() && ring.Silence() {
		slog.Info("wake word over a ring, silencing it")
	}
	v.turn.post(event{kind: evWoke, slot: slot, wake: w})
}

// Actions are wake_race and wake_verdict, over the encrypted API only: a verdict decides whether this
// Show listens, so nobody else on the network gets to send one, and without a lease nothing waits for one.
func (v *Voice) Actions() []*esphome.Action {
	encrypted := func(name string) error {
		if !security.APIEncrypted() {
			return errors.New(name + ": set an API encryption key first")
		}
		return nil
	}
	return []*esphome.Action{{
		Name: "wake_race",
		Run: func(esphome.Call) (any, error) {
			if err := encrypted("wake_race"); err != nil {
				return nil, err
			}
			if !racing() {
				slog.Info("wake words race the other Shows", "lease", raceLease)
			}
			lease.Store(time.Now().Add(raceLease).UnixNano())
			return nil, nil
		},
	}, {
		Name: "wake_verdict",
		Args: []esphome.Arg{{Name: "wake", Type: esphome.ArgString}, {Name: "won", Type: esphome.ArgBool}},
		Run: func(call esphome.Call) (any, error) {
			if err := encrypted("wake_verdict"); err != nil {
				return nil, err
			}
			verdict := verdictLost
			if call.Bool("won") {
				verdict = verdictWon
			}
			v.turn.post(event{kind: evVerdict, text: call.String("wake"), code: verdict})
			return nil, nil
		},
	}}
}

// How a race ended, as the code of its evVerdict.
const (
	verdictWon  = "won"
	verdictLost = "lost"
	// verdictNone is no answer in verdictWait: the turn opens and Home Assistant decides.
	verdictNone = "none"
)

// race starts waiting for a verdict on a wake word, and tells Home Assistant what was heard.
func (c *conversation) race(slot int, w Wake) {
	phrase, _ := c.phraseFor(slot)
	r := &race{id: newWakeID(), slot: slot, at: time.Now()}
	if c.phase != phaseIdle {
		r.over = c.turn.ID()
	}
	r.timer = time.AfterFunc(verdictWait, func() {
		c.post(event{kind: evVerdict, text: r.id, code: verdictNone})
	})
	c.racing = r

	data := map[string]string{
		"wake":     r.id,
		"phrase":   phrase,
		"slot":     strconv.Itoa(slot + 1),
		"level_db": dB(w.Heard.LevelDB),
		"floor_db": dB(w.Heard.FloorDB),
		"score":    strconv.FormatFloat(w.Score, 'f', 3, 64),
		"cutoff":   strconv.FormatFloat(w.Cutoff, 'f', 3, 64),
		"clipped":  strconv.FormatBool(w.Heard.Clipped),
	}
	slog.Info("wake word heard, asking which Show answers", "slot", slot+1, "wake", r.id,
		"level_db", data["level_db"], "floor_db", data["floor_db"], "score", data["score"],
		"clipped", w.Heard.Clipped)
	// Off the loop: the send is a socket write, and the verdict timer is already running.
	go component.Fire.Emit(component.Event{Name: WakeEvent, Data: data})
}

// verdict ends the race it names. A verdict for any other wake is late and changes nothing.
func (c *conversation) verdict(id, verdict string) {
	r := c.racing
	if r == nil || r.id != id {
		slog.Debug("a verdict for no race", "wake", id, "verdict", verdict)
		return
	}
	c.endRace()
	waited := time.Since(r.at).Round(time.Millisecond)

	switch verdict {
	case verdictLost:
		if r.over != "" {
			// The word was for another room; the turn this Show is in goes on, ring and all.
			slog.Info("another Show answers this one, keeping its own turn", "slot", r.slot+1, "waited", waited)
			return
		}
		slog.Info("another Show answers this one", "slot", r.slot+1, "waited", waited)
		c.leds.Busy().Flash(led.WorkElsewhere, yieldFlash)
	case verdictWon:
		slog.Info("this Show answers", "slot", r.slot+1, "waited", waited)
		c.start(nextTurn{slot: r.slot, wokeAt: r.at, won: true})
	default:
		slog.Warn("no verdict, leaving it to Home Assistant", "slot", r.slot+1, "waited", waited)
		c.start(nextTurn{slot: r.slot, wokeAt: r.at})
	}
}

// endRace forgets the race being run, if there is one.
func (c *conversation) endRace() {
	if c.racing == nil {
		return
	}
	c.racing.timer.Stop()
	c.racing = nil
}

// dB is a level for the event, to a tenth.
func dB(v float64) string {
	return strconv.FormatFloat(math.Round(v*10)/10, 'f', 1, 64)
}

// newWakeID names one wake for its race. Only the Show that sent it and the integration ever compare it.
func newWakeID() string {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return hex.EncodeToString(b)
}
