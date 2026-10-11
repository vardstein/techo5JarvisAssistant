//go:build !dot

package display

import (
	"log/slog"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// What a voice turn looks like on the screen: Classic, the title and the words, or a picture moving
// with the voice being heard and the answer being spoken: Glow, a bar of light, Wave, a weave of lines,
// or Bars, an LED equalizer. Glow until somebody chooses: a freshly installed Show still showed the
// plain Classic page on 2026-10-11, after Glow had been built to be the one people see.

// defaultTurnStyle is what nothing saved reads as. Classic used to be kept as nothing, so a Show that
// had picked it before 2026-10-11 now gets Glow, once, until it is picked again.
const defaultTurnStyle = "glow"

// turnStyles are the choices, with what the config keeps for each.
var turnStyles = []struct {
	label string
	value string
}{
	{"Classic", "classic"},
	{"Glow", "glow"},
	{"Wave", "wave"},
	{"Bars", "equalizer"},
}

func turnStyleOptions() []string {
	out := make([]string, len(turnStyles))
	for i, t := range turnStyles {
		out[i] = t.label
	}
	return out
}

// turnStyleIndex is the saved choice's place in turnStyles; nothing saved reads as the default, and a
// value no choice has as Classic.
func turnStyleIndex() int {
	v := config.Get().Screen.TurnStyle
	if v == "" {
		v = defaultTurnStyle
	}
	for i, t := range turnStyles {
		if t.value == v {
			return i
		}
	}
	return 0
}

// setTurnStyle saves the choice at i and shows it in Home Assistant.
func setTurnStyle(s *esphome.Select, i int) {
	if i < 0 || i >= len(turnStyles) {
		return
	}
	if err := config.Set().Screen().TurnStyle(turnStyles[i].value); err != nil {
		slog.Error("saving the turn screen failed", "err", err)
		return
	}
	if s != nil {
		s.Set(turnStyles[i].label)
	}
}
