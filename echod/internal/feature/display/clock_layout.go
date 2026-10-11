//go:build !dot && !spot

package display

import (
	"image/color"
	"log/slog"
	"math"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/lib/palette"
)

// Where the home screen's clock sits, and the date's color: for a photo behind the clock, whose
// subject is so often in the middle. The center and the date's usual color stay the default.

// clockPositions are the choices, with what the config keeps for each; the center is kept as nothing.
var clockPositions = []struct {
	label, value string
}{
	{"Center", ""},
	{"Bottom left", "bottom-left"},
	{"Bottom right", "bottom-right"},
}

// dateColors are the date's colors, all taken from the theme in force so none is left over from an
// older look: Default is the theme's dim text, Text and Accent its own, and the three after them the
// other JODS palettes' accents. Each is made to read on the ground it is drawn over (readableOn). A
// name saved by an older build (white, gold, …) is not among them and reads as Default: those were
// made for a dark ground, and White on White Jade is gone.
var dateColors = []struct {
	label, value string
	of           func() color.RGBA
}{
	{"Default", "", func() color.RGBA { return dim }},
	{"Text", "text", func() color.RGBA { return cream }},
	{"Accent", "accent", func() color.RGBA { return amber }},
	{"Leaf Jade", "leaf", func() color.RGBA { return rgb(palette.Named("Leaf Jade").Accent) }},
	{"Sakura Jade", "sakura", func() color.RGBA { return rgb(palette.Named("Sakura Jade").Accent) }},
	{"Ember Jade", "ember", func() color.RGBA { return rgb(palette.Named("Ember Jade").Accent) }},
}

// dateReadable is the contrast every date color keeps against the ground, the WCAG figure for large
// text; the themes' own dim text sits just above it.
const dateReadable = 3.0

// readableOn is c, darkened on a light ground or lightened on a dark one until it reads there.
func readableOn(c, ground color.RGBA) color.RGBA {
	toward := color.RGBA{0, 0, 0, 0xff}
	if luminance(ground) < 0.4 {
		toward = color.RGBA{0xff, 0xff, 0xff, 0xff}
	}
	for i := 0; i < 10 && contrast(c, ground) < dateReadable; i++ {
		c = lerp(c, toward, 0.15)
	}
	return c
}

// luminance is c's relative luminance, as WCAG works it out.
func luminance(c color.RGBA) float64 {
	lin := func(v uint8) float64 {
		f := float64(v) / 255
		if f <= 0.04045 {
			return f / 12.92
		}
		return math.Pow((f+0.055)/1.055, 2.4)
	}
	return 0.2126*lin(c.R) + 0.7152*lin(c.G) + 0.0722*lin(c.B)
}

// contrast is the WCAG contrast ratio of a and b, 1 to 21.
func contrast(a, b color.RGBA) float64 {
	la, lb := luminance(a), luminance(b)
	if la < lb {
		la, lb = lb, la
	}
	return (la + 0.05) / (lb + 0.05)
}

func clockPositionIndex() int {
	v := config.Get().Screen.ClockPosition
	for i, p := range clockPositions {
		if p.value == v {
			return i
		}
	}
	return 0
}

func dateColorIndex() int {
	v := config.Get().Screen.DateColor
	for i, c := range dateColors {
		if c.value == v {
			return i
		}
	}
	return 0
}

func clockPositionOptions() []string {
	out := make([]string, len(clockPositions))
	for i, p := range clockPositions {
		out[i] = p.label
	}
	return out
}

func dateColorOptions() []string {
	out := make([]string, len(dateColors))
	for i, c := range dateColors {
		out[i] = c.label
	}
	return out
}

// clockAlign is where the clock sits across: -1 on the left, 0 centered, 1 on the right; and whether
// it sits at the foot rather than across the middle.
func clockAlign() (align int, foot bool) {
	switch clockPositions[clockPositionIndex()].value {
	case "bottom-left":
		return -1, true
	case "bottom-right":
		return 1, true
	}
	return 0, false
}

// dateColor is the date's color: the one chosen, or fallback, the theme's. A chosen one is kept over a
// photo too (chosenColor), where the theme's would turn gold.
func dateColor(fallback color.RGBA) color.Color {
	if i := dateColorIndex(); i > 0 {
		return chosenColor(readableOn(dateColors[i].of(), walnut))
	}
	return fallback
}

func setClockPosition(s *esphome.Select, i int) {
	if i < 0 || i >= len(clockPositions) {
		return
	}
	if err := config.Set().Screen().ClockPosition(clockPositions[i].value); err != nil {
		slog.Error("saving the clock position failed", "err", err)
		return
	}
	if s != nil {
		s.Set(clockPositions[i].label)
	}
}

func setDateColor(s *esphome.Select, i int) {
	if i < 0 || i >= len(dateColors) {
		return
	}
	if err := config.Set().Screen().DateColor(dateColors[i].value); err != nil {
		slog.Error("saving the date color failed", "err", err)
		return
	}
	if s != nil {
		s.Set(dateColors[i].label)
	}
}

// clockLayoutRows are the two settings on the screen, under Display.
func clockLayoutRows() []settingRow {
	return []settingRow{
		{id: "clockpos", label: "Clock position", sub: "Out of a photo's way", kind: ctlChoice, value: clockPositions[clockPositionIndex()].label},
		{id: "datecolor", label: "Date color", sub: "The date and AM/PM", kind: ctlChoice, value: dateColors[dateColorIndex()].label},
	}
}

// clockLayoutPicker is the list for either setting.
func clockLayoutPicker(id string) (pickerView, bool) {
	switch id {
	case "clockpos":
		return pickerView{title: "Clock position", opts: clockPositionOptions(), cur: clockPositionIndex()}, true
	case "datecolor":
		return pickerView{title: "Date color", opts: dateColorOptions(), cur: dateColorIndex()}, true
	}
	return pickerView{}, false
}

// chooseClockLayout takes a choice from either list, and says whether id was one of them.
func (d *Display) chooseClockLayout(id string, i int) bool {
	switch id {
	case "clockpos":
		setClockPosition(d.clockPos, i)
	case "datecolor":
		setDateColor(d.dateCol, i)
	default:
		return false
	}
	d.wake()
	return true
}

// clockLayoutSelects are Clock position and Date color in Home Assistant.
func clockLayoutSelects(wake func()) (position, date *esphome.Select) {
	position = &esphome.Select{
		Base: esphome.Base{
			ObjectID: "screen_clock_position",
			Name:     "Clock position",
			Icon:     "mdi:clock-outline",
			Category: esphome.CategoryConfig,
		},
		Options: clockPositionOptions(),
	}
	position.OnCommand = func(v string) {
		for i, p := range clockPositions {
			if p.label == v {
				setClockPosition(position, i)
				wake()
				return
			}
		}
	}
	date = &esphome.Select{
		Base: esphome.Base{
			ObjectID: "screen_date_color",
			Name:     "Date color",
			Icon:     "mdi:palette",
			Category: esphome.CategoryConfig,
		},
		Options: dateColorOptions(),
	}
	date.OnCommand = func(v string) {
		for i, c := range dateColors {
			if c.label == v {
				setDateColor(date, i)
				wake()
				return
			}
		}
	}
	return position, date
}
