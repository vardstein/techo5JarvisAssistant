//go:build !dot && !spot

package display

import (
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// Custom colors with a pale ground read: the words go dark rather than staying the pale ones a dark
// ground had, and every color the strips offer for words reads on the ground chosen.
func TestCustomColorsReadOnAnyGround(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	for _, g := range []int{0, 1} { // the dark neutral ground, then the light one
		setRole(roleGround, swatch(roleGround, g))
		th := current()
		ground := th.colors[roleGround]
		if g == 1 && !lightGround() {
			t.Fatalf("ground %s is not taken for a light one", hex(ground))
		}
		if c := contrast(th.colors[roleText], ground); c < textReadable {
			t.Errorf("ground %s: text %s reads at %.1f", hex(ground), hex(th.colors[roleText]), c)
		}
		for _, role := range []int{roleDim, roleAccent} {
			if c := contrast(th.colors[role], ground); c < dimReadable {
				t.Errorf("ground %s: %s %s reads at %.1f", hex(ground), roleNames[role], hex(th.colors[role]), c)
			}
		}
		for _, role := range []int{roleText, roleDim, roleAccent} {
			for i := range swatchCount {
				c := swatch(role, i)
				if r := contrast(c, ground); r < readableFor(role) {
					t.Errorf("ground %s: %s swatch %d %s reads at %.1f", hex(ground), roleNames[role], i, hex(c), r)
				}
			}
		}
	}
	// The pale words a dark ground had, kept when the ground turned white: drawn near black.
	if err := config.Set().Screen().Custom(config.Palette{Ground: "#ffffff", Accent: "#f7d07a", Text: "#e8f0f8", Dim: "#d8dce4", Rules: "#e0e0e0"}); err != nil {
		t.Fatal(err)
	}
	th := current()
	if c := th.colors[roleText]; luminance(c) > 0.04 {
		t.Errorf("pale text on white came out %s", hex(c))
	}
}

// The presets are drawn as they were made.
func TestPresetsAreNotTouched(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	for _, p := range themes {
		if err := config.Set().Screen().Theme(p.name); err != nil {
			t.Fatal(err)
		}
		if current() != p {
			t.Errorf("%s changed", p.name)
		}
	}
}
