package home

import (
	"context"
	"errors"
	"log/slog"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/metrics"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/mass"
)

// How this Show's Home Assistant key stands, for Genbu to look after it (GENBU-196): a sensor that
// says ok, none, gone, remote or off, an action that answers with that and the key's name, and one
// that tries a home-only refusal once more. A refused key is no longer sent on every request
// (GENBU-158), so without these nobody in Home Assistant would learn that the Show stopped asking.
//
// The Music Assistant token for the lyrics has the same: a sensor that says ok, none or gone, and an
// action that answers with that and the token's name, so the installer keeps a token that still works
// instead of making a new one on every run, which drops the Show's player for a moment (GENBU-137).

func (f *Feature) buildKeySensor() {
	f.keyTxt = &esphome.TextSensor{Base: esphome.Base{
		ObjectID: "home_assistant_key",
		Name:     "Home Assistant key",
		Icon:     "mdi:key-chain-variant",
		Category: esphome.CategoryDiagnostic,
	}}
	hass.Get().Changed.Listen(func(struct{}) { f.showKey() })
	f.maKeyTxt = &esphome.TextSensor{Base: esphome.Base{
		ObjectID: "music_assistant_key",
		Name:     "Music Assistant key",
		Icon:     "mdi:key-chain",
		Category: esphome.CategoryDiagnostic,
	}}
	mass.Get().Changed.Listen(func(struct{}) { f.showMAKey() })
}

func (f *Feature) showKey() {
	f.keyTxt.Set(hass.Get().KeyState())
	f.Changed.Emit(struct{}{})
}

func (f *Feature) showMAKey() {
	f.maKeyTxt.Set(mass.Get().KeyState())
	f.Changed.Emit(struct{}{})
}

// keyActions are home_assistant_key, which says how the key stands and names it,
// home_assistant_retry, and music_assistant_key, home_assistant_key's twin for the lyrics token. Over
// the encrypted API only, like music_assistant: which key a Show holds is for Home Assistant to ask,
// not for anyone else on the network.
func keyActions() []*esphome.Action {
	encrypted := func(name string) error {
		if !security.APIEncrypted() {
			return errors.New(name + ": set an API encryption key first")
		}
		return nil
	}
	return []*esphome.Action{{
		Name:    "home_assistant_key",
		Answers: true,
		Run: func(esphome.Call) (any, error) {
			if err := encrypted("home_assistant_key"); err != nil {
				return nil, err
			}
			c := hass.Get()
			return map[string]any{"state": c.KeyState(), "key": c.KeyID()}, nil
		},
	}, {
		Name:    "home_assistant_retry",
		Answers: true,
		Run: func(esphome.Call) (any, error) {
			if err := encrypted("home_assistant_retry"); err != nil {
				return nil, err
			}
			return map[string]any{"state": hass.Get().Retry()}, nil
		},
	}, {
		Name:    "music_assistant_key",
		Answers: true,
		Run: func(esphome.Call) (any, error) {
			if err := encrypted("music_assistant_key"); err != nil {
				return nil, err
			}
			c := mass.Get()
			return map[string]any{"state": c.KeyState(), "key": c.KeyID()}, nil
		},
	}}
}

// addressesEvery is how often the Show looks whether its addresses changed.
const addressesEvery = 30 * time.Second

// retryOnMove tries a home-only refusal once more whenever the Show's addresses change: it was
// turned away for not coming from home, and a new address is the one thing on its side that can
// change that. Never on a timer, as each try that fails is a failed login in Home Assistant.
func (f *Feature) retryOnMove(ctx context.Context) {
	was := metrics.AddressKey(metrics.Addresses())
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(addressesEvery):
		}
		now := metrics.AddressKey(metrics.Addresses())
		if now == was {
			continue
		}
		was = now
		if now != "" && hass.Get().KeyState() == hass.KeyRemote {
			slog.Info("home: the Show's addresses changed; trying its home-only key again")
			if hass.Get().Retry() == hass.KeyOK {
				f.wake()
			}
		}
	}
}
