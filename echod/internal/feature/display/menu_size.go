//go:build !dot && !spot

package display

import (
	"log/slog"
	"sync/atomic"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// The menu size is how large the settings screen is drawn: Large is the Car Thing's own sizes, which
// on a Show 5 fill the panel the way they filled the Car Thing's, and Normal and Small a step down
// from them, for more rows at once. Small is what a Show starts on: it looks the best, and the larger
// two are there for eyes that need them. The drawer has its own sizes and takes only Large as larger.

// menuSizes are the choices, in the order the screen and Home Assistant offer them.
var menuSizes = []struct {
	label   string
	percent int
}{
	{"Small", 75},
	{"Normal", 85},
	{"Large", 100},
}

// smallMenu is the size of a Show that never chose one, in percent.
const smallMenu = 75

// menuZoom is the chosen size in percent, kept apart from the config so a frame reads it without its lock.
var menuZoom atomic.Int32

func init() { menuZoom.Store(smallMenu) }

func menuSizeOptions() []string {
	out := make([]string, len(menuSizes))
	for i, m := range menuSizes {
		out[i] = m.label
	}
	return out
}

// menuSizeIndex is the saved choice's place in menuSizes. Nothing saved is Small; the old Large and
// Larger (115 and 130) read as Large, and anything else as Normal.
func menuSizeIndex() int { return menuSizeIndexOf(config.Get().Screen.MenuSize) }

func menuSizeIndexOf(v int) int {
	switch {
	case v == 0:
		v = smallMenu
	case v > 100:
		v = 100
	}
	for i, m := range menuSizes {
		if m.percent == v {
			return i
		}
	}
	return 1
}

// useMenuSize puts the choice at i in force without saving it.
func useMenuSize(i int) { menuZoom.Store(int32(menuSizes[i].percent)) }

// setMenuSize saves the choice at i, puts it in force and shows it in Home Assistant.
func setMenuSize(s *esphome.Select, i int) {
	if i < 0 || i >= len(menuSizes) {
		return
	}
	if err := config.Set().Screen().MenuSize(menuSizes[i].percent); err != nil {
		slog.Error("saving the menu size failed", "err", err)
		return
	}
	useMenuSize(i)
	s.Set(menuSizes[i].label)
}

// menuSizeSelect is the Home Assistant setting; wake redraws the screen once it changes.
func menuSizeSelect(wake func()) *esphome.Select {
	s := &esphome.Select{
		Base: esphome.Base{
			ObjectID: "screen_menu_size",
			Name:     "Menu size",
			Icon:     "mdi:magnify-plus-outline",
			Category: esphome.CategoryConfig,
		},
		Options: menuSizeOptions(),
	}
	s.OnCommand = func(v string) {
		for i, m := range menuSizes {
			if m.label == v {
				setMenuSize(s, i)
				wake()
				return
			}
		}
	}
	return s
}

// menuSizeRow is the setting on the screen, under Display.
func menuSizeRow() settingRow {
	return settingRow{id: "menusize", label: "Menu size", sub: "How large the settings are drawn", kind: ctlChoice,
		value: menuSizes[menuSizeIndex()].label}
}

func menuSizePicker() (pickerView, bool) {
	return pickerView{title: "Menu size", opts: menuSizeOptions(), cur: menuSizeIndex()}, true
}

func (d *Display) chooseMenuSize(i int) {
	setMenuSize(d.menuSize, i)
	d.wake()
}

// zoomed runs draw with every fixed size, and the drawer's text, made larger when the menu size is
// Large. Positions taken from the panel's edges stay where they are; what grows is what is measured in
// the Show 5's pixels, which is every size the drawer draws with.
func (r *renderer) zoomed(draw func()) {
	z := 100
	if menuZoom.Load() >= 100 {
		z = 115
	}
	if z <= 100 {
		draw()
		return
	}
	num, den, fc, more := r.sNum, r.sDen, r.fc, r.moreLines
	defer func() { r.sNum, r.sDen, r.fc, r.moreLines = num, den, fc, more }()
	r.moreLines = 2
	if den == 0 {
		r.sNum, r.sDen = 1, 1
	}
	r.sNum, r.sDen = r.sNum*z, r.sDen*100
	if r.zoomFaces == nil {
		r.zoomFaces = map[int]*sheetFaces{}
	}
	if r.zoomFaces[z] == nil {
		f := sheetFacesAt(r.s)
		r.zoomFaces[z] = &f
	}
	r.fc = r.zoomFaces[z]
	draw()
}
