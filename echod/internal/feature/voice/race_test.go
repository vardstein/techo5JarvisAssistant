package voice

import (
	"testing"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/feature/activity"
)

// A verdict decides whether a Show listens, so both race actions want the encrypted API. The test machine
// has no API key: neither may start a race or end one.
func TestTheRaceActionsWantTheEncryptedAPI(t *testing.T) {
	lease.Store(0)
	c := &conversation{events: make(chan event, 4)}
	v := &Voice{turn: c}
	for _, a := range v.Actions() {
		if _, err := a.Run(esphome.Call{}); err == nil {
			t.Errorf("%s ran without an API key", a.Name)
		}
	}
	if racing() {
		t.Error("a refused wake_race left a lease")
	}
	if len(c.events) != 0 {
		t.Error("a refused wake_verdict was posted")
	}
}

// Without a lease a wake word does not wait, which is a house without the integration.
func TestTheLeaseRunsOut(t *testing.T) {
	lease.Store(time.Now().Add(time.Minute).UnixNano())
	if !racing() {
		t.Fatal("a lease a minute ahead does not race")
	}
	lease.Store(time.Now().Add(-time.Second).UnixNano())
	if racing() {
		t.Fatal("a lease that ran out still races")
	}
}

// A race that hears nothing back opens its turn anyway, and a verdict for another wake changes nothing.
func TestARaceWithoutAVerdict(t *testing.T) {
	c := &conversation{events: make(chan event, 4), vs: &esphome.VoiceSatellite{}}
	c.race(0, Wake{Score: 0.9, Cutoff: 0.5})
	r := c.racing
	if r == nil || len(r.id) != 12 {
		t.Fatalf("race %+v", r)
	}

	c.verdict("another", verdictWon)
	if c.racing != r {
		t.Fatal("a verdict for another wake ended the race")
	}

	select {
	case e := <-c.events:
		if e.kind != evVerdict || e.text != r.id || e.code != verdictNone {
			t.Fatalf("event %+v", e)
		}
	case <-time.After(verdictWait + time.Second):
		t.Fatal("no verdict after verdictWait")
	}

	c.endRace()
	if c.racing != nil {
		t.Fatal("endRace left the race")
	}
}

// Ended before its timer, a race posts nothing.
func TestAnEndedRaceIsQuiet(t *testing.T) {
	c := &conversation{events: make(chan event, 4), vs: &esphome.VoiceSatellite{}}
	c.race(0, Wake{})
	c.endRace()
	select {
	case e := <-c.events:
		t.Fatalf("an ended race posted %+v", e)
	case <-time.After(verdictWait + 200*time.Millisecond):
	}
}

// STT_START for a turn that is not waiting, or a timer from a turn that is gone, changes nothing.
func TestAConfirmationForNoTurn(t *testing.T) {
	c := &conversation{events: make(chan event, 4)}
	c.handle(event{kind: evConfirmed})
	c.handle(event{kind: evConfirmed, text: "gone"})
	if c.confirmed {
		t.Fatal("an idle conversation took a confirmation")
	}
}

// A Show busy with a turn races the wake word too, and losing leaves that turn alone: the word was for
// another room. A turn that opened while the race ran is not taken over by its verdict either.
func TestABusyShowRacesAndKeepsItsTurnWhenItLoses(t *testing.T) {
	var log activity.Log
	c := &conversation{events: make(chan event, 4), vs: &esphome.VoiceSatellite{},
		phase: phaseThinking, turn: log.Begin(0, "Okay Nabu")}
	c.race(0, Wake{Score: 0.9, Cutoff: 0.5})
	if c.racing == nil || c.racing.over != c.turn.ID() {
		t.Fatalf("race %+v over turn %s", c.racing, c.turn.ID())
	}
	c.handle(event{kind: evVerdict, text: c.racing.id, code: verdictLost})
	if c.racing != nil || c.phase != phaseThinking {
		t.Fatalf("after losing: race %+v, phase %v", c.racing, c.phase)
	}

	c.race(0, Wake{})
	id := c.racing.id
	c.turn = log.Begin(0, "Okay Nabu") // a question opened a turn of its own meanwhile
	c.handle(event{kind: evVerdict, text: id, code: verdictWon})
	if c.racing != nil || c.phase != phaseThinking || c.pending != nil {
		t.Fatalf("a verdict took over a turn it was not run over: race %+v, pending %+v", c.racing, c.pending)
	}
}

func TestLevelsAreTenths(t *testing.T) {
	for v, want := range map[float64]string{-31.04: "-31.0", -31.06: "-31.1", 0: "0.0", -96.33: "-96.3"} {
		if got := dB(v); got != want {
			t.Errorf("dB(%v) = %s, want %s", v, got, want)
		}
	}
}
