//go:build !dot && !spot

package display

import (
	"image"
	"image/draw"
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// A name is drawn as it is on a German screen: a network called "Office" and a speaker called
// "Connected" are not drawn as "Büro" and "Verbunden", while the Show's own "Office" still is
// (GENBU-201).
func TestNamesAreDrawnAsTheyAre(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	t.Cleanup(func() {
		// Later tests draw with whatever config is left: leave it English again.
		_ = config.Set().Screen().Language("")
		i18n.Changed()
	})
	drawn := func(s string) *image.RGBA {
		img := image.NewRGBA(image.Rect(0, 0, 600, 80))
		draw.Draw(img, img.Rect, image.Black, image.Point{}, draw.Src)
		r := newRenderer(img)
		r.text(r.body, s, 10, 50, cream)
		return img
	}
	english := map[string]*image.RGBA{}
	for _, name := range []string{"Office", "Connected"} {
		english[name] = drawn(name)
	}

	if err := config.Set().Screen().Language("de"); err != nil {
		t.Fatal(err)
	}
	i18n.Changed()
	for name, want := range english {
		r := newRenderer(image.NewRGBA(image.Rect(0, 0, 600, 80)))
		if got := drawn(r.fit(r.body, i18n.Verbatim(name), 500)); !samePixels(got, want) {
			t.Errorf("the name %q is not drawn as it is on a German screen", name)
		}
		if samePixels(drawn(name), want) {
			t.Errorf("the Show's own %q is not translated on a German screen, so this test proves nothing", name)
		}
	}
}

func samePixels(a, b *image.RGBA) bool {
	if a.Rect != b.Rect {
		return false
	}
	for i := range a.Pix {
		if a.Pix[i] != b.Pix[i] {
			return false
		}
	}
	return true
}
