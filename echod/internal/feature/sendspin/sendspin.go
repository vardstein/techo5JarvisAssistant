package sendspin

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"strings"
	"sync"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/android/firewall"
	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/speaker"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

func init() {
	component.Register(component.Device, Get(), component.Order(26))
}

// Player is the room's membership of a group, as Home Assistant sees it: a listening port and an
// advert saying the room is here.
type Player struct {
	enabled *esphome.Switch
	state   *esphome.TextSensor

	out *out

	mu      sync.Mutex
	running context.CancelFunc
	done    chan struct{} // closed once the running listener has let go of the port
	wake    chan struct{}
}

var (
	once   sync.Once
	shared *Player
)

func Get() *Player {
	once.Do(func() { shared = build() })
	return shared
}

func build() *Player {
	p := &Player{
		out:  newOut(speaker.Get()),
		wake: make(chan struct{}, 1),
	}

	p.enabled = &esphome.Switch{
		Base: esphome.Base{
			ObjectID: "sendspin",
			Name:     "Sendspin",
			Icon:     "mdi:speaker-multiple",
			Category: esphome.CategoryConfig,
			DeviceID: component.DevicePlayback,
		},
		OnCommand: func(on bool) {
			if on && pairedServer() == "" {
				slog.Warn("sendspin: refusing to enable without a paired Music Assistant server")
				p.enabled.Set(false)
				p.state.Set(stateUnpaired)
				return
			}
			p.enabled.Set(on)
			if err := config.Set().Sendspin().Enabled(on); err != nil {
				slog.Error("saving a setting failed", "setting", p.enabled.ObjectID, "err", err)
			}
			p.rethink()
		},
	}

	p.state = &esphome.TextSensor{
		Base: esphome.Base{
			ObjectID: "sendspin_state",
			Name:     "Sendspin state",
			Icon:     "mdi:lan-connect",
			Category: esphome.CategoryDiagnostic,
			DeviceID: component.DevicePlayback,
		},
	}
	p.state.Set(stateOff)
	return p
}

// What the state sensor says, from switched off to audible.
const (
	stateOff      = "off"
	stateUnpaired = "unpaired"
	stateWaiting  = "waiting"
	stateJoined   = "joined"
	statePlaying  = "playing"
)

// releaseWithin is how long stopping waits for the listener to let go of the port.
const releaseWithin = 5 * time.Second

func (p *Player) Name() string { return "sendspin" }

func (p *Player) Paired() bool { return pairedServer() != "" }

// Enabled and SetEnabled are the switch, for the settings sheet: the player holds a port open to
// the network while it is on.
func (p *Player) Enabled() bool { return config.Get().Sendspin.Enabled }

func (p *Player) SetEnabled(on bool) { p.enabled.OnCommand(on) }

func (p *Player) Entities() []esphome.Entity {
	return []esphome.Entity{p.enabled, p.state}
}

// Actions pairs Sendspin to one Music Assistant server. The server address is private network
// configuration and is accepted only over the encrypted Home Assistant API. An empty address
// unpairs the device and closes the listener.
func (p *Player) Actions() []*esphome.Action {
	return []*esphome.Action{{
		Name: "sendspin_server",
		Args: []esphome.Arg{{Name: "ip", Type: esphome.ArgString}},
		Run: func(c esphome.Call) (any, error) {
			if !security.APIEncrypted() {
				return nil, errors.New("sendspin_server: set an API encryption key first")
			}
			ip, err := normalizeServerIP(c.String("ip"))
			if err != nil {
				return nil, err
			}
			if err := config.Set().Sendspin().ServerIP(ip); err != nil {
				return nil, err
			}
			if ip == "" {
				_ = config.Set().Sendspin().Enabled(false)
				p.enabled.Set(false)
				p.state.Set(stateUnpaired)
				slog.Info("sendspin: Music Assistant server unpaired")
			} else {
				slog.Info("sendspin: Music Assistant server paired")
			}
			p.stop()
			p.rethink()
			return nil, nil
		},
	}}
}

func normalizeServerIP(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", nil
	}
	ip := net.ParseIP(raw)
	if ip == nil {
		return "", errors.New("sendspin_server: ip must be one literal IPv4 or IPv6 address")
	}
	return ip.String(), nil
}

func pairedServer() string {
	ip, _ := normalizeServerIP(config.Get().Sendspin.ServerIP)
	return ip
}

// Restore puts the switch back where it was left. Listening waits for Run, once there is a network.
func (p *Player) Restore(c config.Config) {
	if c.Sendspin.ServerIP == "" {
		if c.Sendspin.Enabled {
			_ = config.Set().Sendspin().Enabled(false)
			slog.Warn("sendspin: old enabled-but-unpaired state cleared during secure migration")
		}
		p.enabled.Set(false)
		p.state.Set(stateUnpaired)
		return
	}
	p.enabled.Set(c.Sendspin.Enabled)
}

// Run holds the port open for as long as the switch is on.
func (p *Player) Run(ctx context.Context) error {
	defer p.stop()

	for {
		p.settle(ctx)

		select {
		case <-ctx.Done():
			return nil
		case <-p.wake:
		}
	}
}

// rethink wakes the loop without blocking. A second ask while one is pending is the same ask.
func (p *Player) rethink() {
	select {
	case p.wake <- struct{}{}:
	default:
	}
}

// settle makes what is running match what was asked for.
func (p *Player) settle(parent context.Context) {
	cfg := config.Get().Sendspin
	want := cfg.Enabled && pairedServer() != ""

	p.mu.Lock()
	already := p.running != nil
	p.mu.Unlock()

	switch {
	case want && already, !want && !already:
		return
	case !want:
		p.stop()
		p.state.Set(stateOff)
		return
	}

	ctx, cancel := context.WithCancel(parent)
	done := make(chan struct{})
	p.mu.Lock()
	p.running, p.done = cancel, done
	p.mu.Unlock()

	// The vendor's chain drops what it was not told about.
	if err := firewall.Open(firewall.Sendspin, Port); err != nil {
		slog.Error("opening the sendspin port failed", "port", Port, "err", err)
	}

	name := config.Get().Device.Name
	l := newListener(p.out, speaker.Sound().Backgrounds(), p.state.Set, pairedServer())

	safe.Go("sendspin listen", func() {
		defer close(done)
		if err := l.serve(ctx, name); err != nil {
			slog.Error("sendspin listener stopped", "err", err)
		}
	})
	safe.Go("sendspin advertise", func() { advertise(ctx, name, Port) })

	p.state.Set(stateWaiting)
	slog.Info("sendspin waiting for a server", "name", name, "port", Port)
}

func (p *Player) stop() {
	p.mu.Lock()
	cancel, done := p.running, p.done
	p.running, p.done = nil, nil
	p.mu.Unlock()

	if cancel == nil {
		return
	}
	cancel()

	// Pairing stops the listener and starts it again at once, and the new one cannot have the port
	// while the old one still holds it (GENBU-133). Closing takes moments; a stuck one is not waited
	// out, and the new one's bind waits for the port instead.
	select {
	case <-done:
	case <-time.After(releaseWithin):
		slog.Warn("sendspin: the old listener is slow to let go of the port")
	}

	if err := firewall.Close(firewall.Sendspin); err != nil {
		slog.Warn("closing the sendspin port failed", "port", Port, "err", err)
	}
}
