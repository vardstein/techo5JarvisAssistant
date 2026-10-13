// Package mute owns both microphone privacy layers: a reversible software cut for Home Assistant
// and the device's physical privacy latch. Either one makes the microphone effectively muted; only
// the button on the device can release a latched Crown/Checkers hardware mute.
package mute

import (
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/buttons"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/led"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/mic"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/privacy"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/speaker"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hook"
)

func init() {
	component.Register(component.Device, Get(), component.Order(30))
}

// The two levels the mute LED has. Neither way of reaching it offers anything between them.
const (
	dim    = "Dim"
	bright = "Bright"
)

// mutedColor is what an inheriting animation runs in while the microphones are cut. Red, like the
// button's own LED and like a failure: the device cannot hear, which is closer to being broken than
// to being a color someone chose.
var mutedColor = led.Color{R: 0xC0, G: 0x00, B: 0x00}

type Mute struct {
	sw         *esphome.Switch
	status     *esphome.TextSensor
	brightness *esphome.Select
	line       privacy.Mute
	led        privacy.LED
	physical   atomic.Bool

	// ring is the animation to show while the microphones are cut, and claim is where it goes. The
	// select lives here rather than with the other settings because choosing one has to take effect
	// immediately: being muted has no next occurrence to wait for, it is already happening.
	ring  *esphome.Select
	claim *led.Claim

	// Changed fires when the microphones are actually cut or brought back, with the new state. It is
	// what lets the detector stop running models over silence.
	Changed hook.Hook[bool]
}

var (
	once   sync.Once
	shared *Mute
)

func Get() *Mute {
	once.Do(func() { shared = build() })
	return shared
}

func build() *Mute {
	m := &Mute{
		sw: &esphome.Switch{
			Base: esphome.Base{
				ObjectID: "mic_mute",
				DeviceID: component.DeviceMicrophone,
				Name:     "Microphone mute",
				Icon:     "mdi:microphone-off",
			},
		},
		status: &esphome.TextSensor{
			Base: esphome.Base{
				ObjectID: "microphone_mute_status",
				DeviceID: component.DeviceMicrophone,
				Name:     "Microphone mute status",
				Icon:     "mdi:microphone-off",
				Category: esphome.CategoryDiagnostic,
			},
		},
		claim: led.Get().Claim(led.PriorityMute),
		ring: &esphome.Select{
			Base: esphome.Base{
				ObjectID: "ring_muted",
				DeviceID: component.DeviceRing,
				Name:     "Ring while muted",
				Icon:     "mdi:microphone-off",
				Category: esphome.CategoryConfig,
			},
		},
	}
	m.sw.OnCommand = m.Set
	component.BindEffect(m.ring, led.EffectNames(), m.show, config.Set().Ring().Muted)

	// The entities exist whether or not the pins do. A device that hides controls when its hardware
	// fails is a device nobody can tell has failed, and the log says which of the two went missing.
	m.brightness = &esphome.Select{
		Base: esphome.Base{
			ObjectID: "mute_led_brightness",
			DeviceID: component.DeviceMicrophone,
			Name:     "Mute LED brightness",
			Icon:     "mdi:brightness-6",
			Category: esphome.CategoryConfig,
		},
		Options: []string{dim, bright},
	}
	m.brightness.OnCommand = m.setBrightness

	var err error
	if m.line, err = privacy.Microphone(); err != nil {
		slog.Error("mute unavailable", "err", err)
	}
	if m.led, err = privacy.Light(); err != nil {
		slog.Error("mute LED unavailable", "err", err)
	}

	buttons.Get().Events.Listen(m.pressed)
	return m
}

func (m *Mute) Name() string { return "microphone mute" }

// The switch and its status are there on every device. The ring effect needs a ring, and the LED
// brightness an LED that can dim: on the Shows the privacy circuit lights it and nothing sets how bright.
func (m *Mute) Entities() []esphome.Entity {
	out := []esphome.Entity{m.sw, m.status}
	if led.HasRing() {
		out = append(out, m.ring)
	}
	if privacy.LightDims {
		out = append(out, m.brightness)
	}
	return out
}

// Muted reports whether the line is cut, which is what a turn has to check before opening the
// microphones.
func (m *Mute) Muted() (bool, error) {
	software := mic.Get().SoftwareMuted()
	if m.line == nil {
		return software, nil
	}
	physical, err := m.line.Get()
	if err != nil {
		return m.physical.Load() || software, err
	}
	m.physical.Store(physical)
	return physical || software, nil
}

// Restore puts both mute layers back. The saved physical latch remains a local privacy choice; the
// separate software cut is the reversible Home Assistant control and is already seeded in mic.New
// so not one captured frame leaks before this Device-phase component starts.
func (m *Mute) Restore(c config.Config) {
	// What to show while cut, before cutting, so the ring is right the first time settled looks.
	component.RestoreEffect(m.ring, c.Ring.Muted, nil, config.Set().Ring().Muted)
	mic.Get().SetSoftwareMuted(c.Microphone.SoftwareMuted)
	m.sw.Set(c.Microphone.SoftwareMuted)

	if m.line != nil {
		was, err := m.line.Get()
		if err != nil {
			slog.Error("reading mute state failed", "err", err)
		} else {
			m.physical.Store(was)
			want := c.Microphone.Muted
			if want != was {
				if err := m.line.Set(want); err != nil {
					slog.Error("setting physical mute failed", "muted", want, "err", err)
				}
			}
			m.settledPhysical(false, was)
		}
	}
	m.refresh(false)
	slog.Info("restored", "what", "microphone software mute", "muted", m.sw.Get())

	m.applyBrightness(c.Microphone.LEDBright)
	slog.Info("restored", "what", m.brightness.ObjectID, "using", label(c.Microphone.LEDBright))
}

// Set is the reversible software mute in Home Assistant. Muting is always accepted and takes
// effect on the next captured frame. Unmuting is a stronger action: the owner must have opted in on
// the device/setup UI and the native API must have a real encryption key. Neither path can release
// the physical privacy latch.
func (m *Mute) Set(muted bool) {
	// An already-clear software cut is not an unmute operation. In particular, Home Assistant may
	// replay the entity's current false state after reconnecting; that must not turn into a refusal.
	if !muted && !mic.Get().SoftwareMuted() {
		m.sw.Set(false)
		m.refresh(false)
		return
	}
	allowed := config.Get().Microphone.AllowRemoteUnmute
	encrypted := security.APIEncrypted()
	if !muted && !canRemoteUnmute(allowed, encrypted) {
		m.sw.Set(mic.Get().SoftwareMuted())
		m.refresh(true)
		slog.Warn("remote microphone unmute refused", "allowed", allowed, "encrypted", encrypted)
		return
	}
	m.setSoftware(muted)
}

func canRemoteUnmute(allowed, encrypted bool) bool { return allowed && encrypted }

func (m *Mute) setSoftware(muted bool) {
	before := m.effective()
	mic.Get().SetSoftwareMuted(muted)
	m.sw.Set(muted)
	if err := config.Set().Microphone().SoftwareMuted(muted); err != nil {
		slog.Error("saving software mute state failed", "err", err)
	}
	m.refresh(false)
	m.changed(before)
	slog.Info("microphone software mute", "muted", muted)
}

// LocalToggle is the touchscreen control. It owns only the reversible software cut: the physical
// button remains the hardware privacy control, and a latched hardware mute cannot be cleared by a
// screen tap either. Local software unmute never needs the Home Assistant permission.
func (m *Mute) LocalToggle() {
	if m.physical.Load() {
		m.refresh(false)
		return
	}
	m.setSoftware(!mic.Get().SoftwareMuted())
}

// Toggle is the button on top of the device. Where the hardware has already acted on the press, the
// press is only news: what follows is the same either way. Whether it has can depend on which way
// the press goes, and the switch still holds the state from before it.
func (m *Mute) Toggle() {
	if m.line == nil {
		return
	}
	was := m.physical.Load()
	if !m.line.HardwareActs(was) {
		// On Crown/Checkers engaging the latch takes about a second. Stop handing frames on before
		// the driver pulse so a local mute press is immediate from software's point of view too.
		if !was {
			mic.Get().SetTransitionMuted(true)
		}
		if _, err := m.line.Toggle(); err != nil {
			mic.Get().SetTransitionMuted(false)
			slog.Error("toggling mute failed", "err", err)
			return
		}
	}
	m.settledPhysical(true, was)
}

// pressed is the mute button. A hold only sounds: nothing is bound to it, and the tone says the
// press was heard.
func (m *Mute) pressed(e buttons.Event) {
	if e.Name != buttons.Mute {
		return
	}
	switch e.Kind {
	case buttons.Tap:
		// A ring takes the press, and the microphone is left where it was (keep, which undoes it
		// where the hardware has already moved). Cutting the microphone is the last thing somebody
		// wants at a ringing alarm, since it would take the stop word with it — and on a device with
		// no action button this is one of the three that can stop a ring.
		if ring.Offered() {
			ring.Accept()
			m.keep()
			return
		}
		if ring.Silence() {
			m.keep()
			return
		}
		m.Toggle()
	case buttons.Hold:
		speaker.Sound().Chime(speaker.ToneMuteHold)
	}
}

// keep leaves the microphones where they were after a press that went to a ring. Where the hardware
// acts on the button itself - the Dot's keypad driver, the 2nd gen Show 5's - it has already moved
// the mute by the time the press arrives, and nothing here followed: a Dot muted before an alarm was
// live after the press that stopped it, with its wake words still stopped and the stored state
// still muted, so it answered nothing and came back muted after a restart (techo5-dot#4). The same
// press on a live Dot cut its microphones behind Home Assistant's back.
//
// So the line is put back. If it will not go back, what the hardware did is taken as the press it
// was, and published like any other, so that the device at least says what it is doing.
func (m *Mute) keep() {
	if m.line == nil {
		return
	}
	was := m.physical.Load()
	if !m.line.HardwareActs(was) {
		return
	}
	m.await(was)
	is, err := m.line.Get()
	if err != nil {
		slog.Error("reading mute state failed", "err", err)
		return
	}
	if is == was {
		return
	}
	if err := m.line.Set(was); err != nil {
		slog.Error("putting the mute back after a ring failed", "muted", was, "err", err)
	}
	if now, err := m.line.Get(); err == nil && now != was {
		m.settledPhysical(true, was)
		return
	}
	// A latch released under the microphones may take the chip down with it; see settled.
	if !was {
		mic.Rewire()
	}
	m.physical.Store(was)
	m.refresh(false)
	slog.Info("microphone mute left as it was after a press that went to a ring", "muted", was)
}

// settledPhysical publishes what the privacy latch now reads — not what was asked for. from is the
// last observed latch state, kept separately from the Home Assistant software-mute switch.
func (m *Mute) settledPhysical(asked bool, from bool) {
	if asked {
		m.await(from)
	}

	muted, err := m.line.Get()
	if err != nil {
		mic.Get().SetTransitionMuted(false)
		slog.Error("reading mute state failed", "err", err)
		return
	}
	before := m.effective()
	m.physical.Store(muted)
	mic.Get().SetTransitionMuted(false)
	m.refresh(false)

	// A latch that has just been released may have taken the microphone chip down with it, which
	// brings it back muted and its stream dead (hardware/mic.Rewire). Only on a real change: at
	// start-up the capture device has just been opened.
	if asked && !muted {
		mic.Rewire()
	}

	if !asked {
		return
	}

	if err := config.Set().Microphone().Muted(muted); err != nil {
		slog.Error("saving physical mute state failed", "err", err)
	}
	slog.Info("microphone physical mute", "muted", muted)
	m.changed(before)
}

func (m *Mute) effective() bool { return m.physical.Load() || mic.Get().SoftwareMuted() }

func (m *Mute) changed(before bool) {
	after := m.effective()
	if before == after {
		return
	}
	m.Changed.Emit(after)
	speaker.Sound().Chime(speaker.MuteSound(after))
}

func (m *Mute) refresh(remoteRefused bool) {
	if m.ring != nil && m.claim != nil {
		m.show(component.ChosenEffect(m.ring))
	}
	if m.status != nil {
		m.status.Set(statusLabel(m.physical.Load(), mic.Get().SoftwareMuted(), remoteRefused))
	}
}

func statusLabel(physical, software, remoteRefused bool) string {
	switch {
	case physical:
		return "physical mute active"
	case remoteRefused:
		return "remote unmute not permitted"
	case software:
		return "muted"
	default:
		return "unmuted"
	}
}

// pollInterval is how often await looks while it waits.
const pollInterval = 25 * time.Millisecond

// await gives the hardware the time it says it needs to leave from, so what gets published is where
// the microphones ended up rather than where they were. It returns as soon as they have moved, and
// gives up quietly: a request that changed nothing is not an error.
func (m *Mute) await(from bool) {
	for waited := time.Duration(0); waited < m.line.Lag(); waited += pollInterval {
		if is, err := m.line.Get(); err != nil || is != from {
			return
		}
		time.Sleep(pollInterval)
	}
}

// show puts an animation on the ring for as long as the microphones are cut, or takes it off. Unlike
// a failure this has no duration of its own, so the claim is held and cleared rather than timed.
//
// It takes the name rather than reading the setting, because it is called both when the mute state
// changes and when the choice does, and on that second path the setting has not been written yet.
func (m *Mute) show(name string) {
	if name == "" || !m.effective() {
		m.claim.Clear()
		return
	}
	m.claim.Play(name, mutedColor)
}

func (m *Mute) setBrightness(v string) {
	on := v == bright
	m.applyBrightness(on)
	if err := config.Set().Microphone().LEDBright(on); err != nil {
		slog.Error("saving mute LED brightness failed", "err", err)
	}
}

func (m *Mute) applyBrightness(on bool) {
	if m.led == nil {
		return
	}
	if err := m.led.SetBright(on); err != nil {
		slog.Error("setting mute LED brightness failed", "bright", on, "err", err)
		return
	}
	if now, err := m.led.Bright(); err == nil {
		m.brightness.Set(label(now))
	}
}

func label(on bool) string {
	if on {
		return bright
	}
	return dim
}
