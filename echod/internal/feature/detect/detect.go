package detect

import (
	"context"
	"log/slog"
	"sync"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/diag"
	"github.com/HuskerMinion/techo5/echod/internal/feature/mute"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/feature/wakeword"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/led"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/mic"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wake"
	"github.com/HuskerMinion/techo5/echod/internal/service"
)

func init() {
	// Before the API, so Home Assistant cannot read the wake words while they are still loading and be
	// told about one that then fails.
	component.Register(component.Device, Get(), component.Order(40),
		component.Supervise(service.Restart(time.Second, 30*time.Second)))
}

// Detect is the engine as the device runs it: the wake words the user chose, loaded into it, and the
// ring saying so until they can hear.
type Detect struct {
	engine *Engine
	busy   *wakeBusy
	stop   *esphome.Number

	// ducker gets the music out of the way after an utterance that nearly fired; see nearmiss.go.
	ducker *ducker

	// nearMiss tells Home Assistant about the same moment, for music this device does not play and so
	// cannot duck: a speaker elsewhere in the room, which only Home Assistant can turn down.
	nearMiss *esphome.Event
}

var (
	once   sync.Once
	shared *Detect
)

func Get() *Detect {
	once.Do(func() { shared = newDetect() })
	return shared
}

// playingSlack is how much lower the stop word's threshold sits while the speaker is loud enough to bury
// a word (mic.Source.Masking). Not merely while the canceller runs: it starts on a loopback far too faint
// to hide anything, and the slack there let a TV in the room wake the device.
const playingSlack = 0.10

// slackFloor is as low as the slack is allowed to drag a threshold, whatever it started at.
const slackFloor = 0.5

// thresholdFor is the cutoff a slot's score is judged against. base supplies the per-slot wake word
// threshold; stop is the stop word's own, which is configured separately.
//
// While the speaker plays loud and the canceller runs, what reaches the detector is the residual of the
// music plus the voice, and the word scores lower than it does in a quiet room. The stop word gets a
// little slack for that: it is the one word said while the speaker is certainly playing, because
// playing is what it is asked to stop, and a false stop costs a pause.
//
// The wake words get none here. With it, the residual of the device's own songs and announcements woke
// Hey Jarvis, every such wake crossing between 0.85 and 0.91 against a lowered cutoff, and a false wake
// costs the music and a turn nobody asked for. The few that music leaves alone go further instead; see
// playbackWords.
func thresholdFor(slot int, base func(int) float64, stop float64, masking bool) float64 {
	if slot != StopSlot {
		return base(slot)
	}
	if masking {
		return max(stop-playingSlack, slackFloor)
	}
	return stop
}

// playbackWords are the wake words that are judged against NearMiss while the device's own playback is
// loud enough to bury them (mic.Source.Masking), so somebody who says one over the music is heard the
// first time instead of having the music turned down and being asked, in effect, to say it again.
//
// A word gets in here by being measured, not by seeming likely. Okay Nabu: 64 songs from 64 artists,
// four hours of music with sung words, never scored above 0.08 (TestWakeScore). On the two Shows, of
// the ten near misses it had over their own playback, eight were followed by the same word firing two to
// seven seconds later, and they peaked anywhere from 0.55 to 0.87, so any smaller allowance misses most
// of them. Hey Jarvis is the counterexample: the residual of the same music crossed 0.85 on its own.
//
// A word not listed keeps its threshold over the music, and the near miss ducks it instead (nearmiss.go).
var playbackWords = map[string]bool{"okay_nabu": true}

// heardThrough is whether the word in a slot is judged against NearMiss: a wake word measured safe over
// music, while the device's own playback masks it. The stop word has its own allowance (thresholdFor).
func heardThrough(slot int, id string, masking bool) bool {
	return masking && slot != StopSlot && playbackWords[id]
}

func newDetect() *Detect {
	// Sized to reach the stop word's reserved index. The slots between it and Home Assistant's are never
	// loaded, and an unloaded slot is one comparison a frame.
	e := New(StopSlot+1, mic.Get())

	e.Threshold = func(slot int) float64 {
		return thresholdFor(slot, wakeword.Threshold, config.Get().Wake.Stop.Threshold, mic.Get().Masking())
	}
	// The wake words only, and not over the device's own playback, for the same reason they get no
	// slack there: the residual of its own songs comes close often enough to be heard twice.
	e.SecondTry = func(slot int) bool { return slot != StopSlot && !mic.Get().Masking() }
	e.Playback = func(slot int, id string) bool { return heardThrough(slot, id, mic.Get().Masking()) }

	e.OnDetect = func(slot int) {
		// A call has the microphones and the speaker. The far end talking through the speaker is not
		// someone in the room, and a turn would take the call's audio away mid-sentence.
		if phone.Get().Busy() {
			// A ring during a call is the one thing that still has to hear "stop". The call page
			// takes every tap and Home Assistant cannot stop a timer, so without this a timer that
			// finishes mid-call sounds for its full fifteen minutes with no way out at all.
			//
			// It ends the ring and nothing else: the call is not interrupted, and the word is not
			// passed to the turn, because the far end talking through the speaker is not somebody in
			// the room.
			if slot == StopSlot && ring.IsSounding() {
				slog.Info("stop word during a call, ending the ring")
				ring.End()
				return
			}
			slog.Debug("wake word ignored during a call", "slot", slot+1)
			return
		}
		if slot == StopSlot {
			voice.Get().Interrupt()
			return
		}
		// Heard by the microphones, so it may have been heard by another Show too: the voice feature
		// asks Home Assistant which of them answers, with what this one heard (GENBU-224).
		score, cutoff := e.Crossing(slot)
		voice.Get().Woke(slot, voice.Wake{Heard: mic.Get().Voice(), Score: score, Cutoff: cutoff})
	}

	d := &Detect{engine: e, busy: newWakeBusy(led.Get().Busy(), e.Ready)}
	d.ducker = newDucker()
	d.ducker.watch(e)
	d.nearMiss = &esphome.Event{
		Base: esphome.Base{
			ObjectID: "wake_near_miss",
			Name:     "Wake word near miss",
			Icon:     "mdi:ear-hearing",
		},
		Types: []string{NearMissEvent},
	}
	duck := e.OnNearMiss
	e.OnNearMiss = func(slot int, peak float64) {
		duck(slot, peak)
		if slot != StopSlot {
			d.nearMiss.Trigger(NearMissEvent)
		}
	}
	d.stop = newStopEntity(d)

	// A cut microphone hands on silence, and running the models over it is work that cannot find
	// anything. The engines go down with the microphones and come back with them.
	mute.Get().Changed.Listen(e.Quiet)
	e.OnReady = d.busy.scored

	// The engine loads on every start, including a restart. Home Assistant only pushes a selection when
	// the user changes one, so an engine that came back empty would leave the device deaf while it went
	// on advertising wake words it was not listening for.
	e.Load = func() error {
		turn := voice.Get()
		turn.SetActiveWakeWords(d.load(turn.ActiveWakeWords()))
		d.loadStop()
		// A device that was muted when it was switched off comes back muted, and the hook below only
		// fires on a change, so the state has to be read once here as well.
		if muted, err := mute.Get().Muted(); err == nil {
			e.Quiet(muted)
		}
		return nil
	}

	// A selection both downloads models and lets go of the ones it replaced, so it is the one thing that
	// moves either number the disk reports.
	voice.Get().OnWakeWord(d.load, diag.Get().Measure)

	ours := wake.Lib().Ours()
	slog.Info("wake words installed", "count", len(ours),
		"openwakeword", len(wake.OfKind(ours, wake.KindOpenWakeWord)),
		"microwakeword", len(wake.OfKind(ours, wake.KindMicroWakeWord)))

	return d
}

func (d *Detect) Name() string { return "wake" }

func (d *Detect) Start(ctx context.Context) error { return d.engine.Start(ctx) }

func (d *Detect) Run(ctx context.Context) error { return d.engine.Run(ctx) }

func (d *Detect) Close() error { return d.engine.Close() }

// load puts one wake word in each slot and reports the ids that came up. Whatever the engine refuses
// is left out, so Home Assistant reverts that slot rather than showing a wake word the device is not
// listening for.
func (d *Detect) load(ids []string) []string {
	d.busy.begin()

	// A selection may name a model Home Assistant is offering but this device has never had, so the
	// library is asked rather than a list captured at boot: this is where a new word arrives.
	models := wake.Lib().Ensure(ids)

	var accepted []string
	var loaded []int
	for slot := range wakeword.Slots {
		if slot >= len(ids) || ids[slot] == "" {
			d.engine.Clear(slot)
			continue
		}

		m, ok := wake.Find(models, ids[slot])
		if !ok {
			slog.Warn("unknown wake word", "slot", slot+1, "id", ids[slot])
			d.engine.Clear(slot)
			continue
		}
		if err := d.engine.Use(slot, m); err != nil {
			slog.Error("loading the selected wake word failed", "slot", slot+1, "id", m.ID, "err", err)
			d.engine.Clear(slot)
			continue
		}
		accepted = append(accepted, m.ID)
		loaded = append(loaded, slot)
	}

	// Only the slots that took a wake word are waited on. A selection that loaded nothing has nothing to
	// warm up, so the ring goes back to what it was showing rather than animating for a device that is
	// not going to hear anything.
	d.busy.waitFor(loaded)
	return accepted
}
