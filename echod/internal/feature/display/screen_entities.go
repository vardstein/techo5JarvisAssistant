//go:build !dot

package display

import (
	"github.com/HuskerMinion/techo5/echod/internal/config"
	esphome "github.com/ygelfand/go-esphome-device"
)

// The screen in Home Assistant: a switch and a brightness number, not a light. Home Assistant has no
// screen of its own to make it, and as a light it went out with "turn the lights off in here", which
// meant the lamps.
func screenEntities() (*esphome.Switch, *esphome.Number) {
	return &esphome.Switch{
			Base: esphome.Base{ObjectID: "screen", Name: "Screen", Icon: "mdi:monitor"},
		}, &esphome.Number{
			Base: esphome.Base{ObjectID: "screen_brightness", Name: "Screen brightness", Icon: "mdi:brightness-6"},
			Min:  1, Max: 100, Step: 1, Unit: "%", Mode: esphome.NumberSlider,
		}
}

// screenOn is whether the screen is switched on.
func (d *Display) screenOn() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.on
}

// screenLevel is the brightness it is set to, the usual one if it has none yet.
func (d *Display) screenLevel() int {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.ceiling == 0 {
		return config.DefaultScreenBrightness
	}
	return d.ceiling
}
