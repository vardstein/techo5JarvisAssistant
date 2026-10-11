// Package palette is the screen themes as data: five colors each, by name. The Show's screen draws
// with them (feature/display/theme.go) and the setup page wears the same ones, so a device's page
// looks like its screen. It imports nothing, so anything may use it.
package palette

import "fmt"

// Preset is one theme: the ground, the accent, the text, a dim text and the rules and boxes, as
// 0xRRGGBB.
type Preset struct {
	Name                             string
	Ground, Accent, Text, Dim, Rules uint32
}

// Presets are the themes offered, in the order the screen lists them. Jarvis Show exposes only the
// JODS palettes, so the screen and the setup page look like one device. The first, Obsidian Jade,
// is the default: Genbu's own black and serpent teal. A saved name that is no longer here falls back
// to it; Custom colors are kept separately by the screen config.
var Presets = []Preset{
	{"Obsidian Jade", 0x0f171d, 0x4fa89d, 0xdce8ec, 0x7d98a2, 0x22323c},
	// The light JODS palettes. Their five roles are the solid equivalents of the richer JODS shell
	// tokens (canvas, accent, primary text, muted text, soft borders).
	{"White Jade", 0xeeeff2, 0x86909d, 0x20242a, 0x7c8794, 0xd8dade},
	{"Leaf Jade", 0xedf6f1, 0x3d9a61, 0x1f3127, 0x698474, 0xd2e1d8},
	{"Sakura Jade", 0xf8edf3, 0xd66f99, 0x302128, 0x8b7480, 0xe5d9df},
	{"Ember Jade", 0xf7ece7, 0xea9468, 0x34180f, 0x8d5a48, 0xead9d2},
}

// Round is the Spot's palette, which is not chosen: the round screen has one look.
var Round = Preset{"Round", 0x0a0d12, 0xf05a3c, 0xecf0f4, 0x808a96, 0x252c36}

// Hex is the five colors as "#rrggbb", ground first.
func (p Preset) Hex() [5]string {
	var out [5]string
	for i, v := range []uint32{p.Ground, p.Accent, p.Text, p.Dim, p.Rules} {
		out[i] = fmt.Sprintf("#%06x", v&0xffffff)
	}
	return out
}

// Light is whether the ground is pale, so what is drawn on it should be dark.
func (p Preset) Light() bool {
	r, g, b := p.Ground>>16&0xff, p.Ground>>8&0xff, p.Ground&0xff
	return r*299+g*587+b*114 > 128*1000
}

// FromHex makes a preset from five "#rrggbb" colors, and reports false unless every one is exactly
// that: they come from saved settings and go into a style sheet, so nothing else gets through.
func FromHex(name string, colors ...string) (Preset, bool) {
	if len(colors) != 5 {
		return Preset{}, false
	}
	var v [5]uint32
	for i, s := range colors {
		if len(s) != 7 || s[0] != '#' {
			return Preset{}, false
		}
		for _, c := range s[1:] {
			var d uint32
			switch {
			case c >= '0' && c <= '9':
				d = uint32(c - '0')
			case c >= 'a' && c <= 'f':
				d = uint32(c-'a') + 10
			case c >= 'A' && c <= 'F':
				d = uint32(c-'A') + 10
			default:
				return Preset{}, false
			}
			v[i] = v[i]<<4 | d
		}
	}
	return Preset{name, v[0], v[1], v[2], v[3], v[4]}, true
}

// Named is the preset of that name, or the first for an unknown one.
func Named(name string) Preset {
	for _, p := range Presets {
		if p.Name == name {
			return p
		}
	}
	return Presets[0]
}
