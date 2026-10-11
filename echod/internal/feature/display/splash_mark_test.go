//go:build !dot && !spot

package display

import (
	"image"
	"image/color"
	"testing"
	"time"
)

func TestSplashMarkPerPalette(t *testing.T) {
	defer applyTheme(themes[0])
	seen := map[color.RGBA]string{}
	for _, th := range themes {
		applyTheme(th)
		img := image.NewRGBA(image.Rect(0, 0, 960, 480))
		r := newRenderer(img)
		s := newSplash(r)
		r.drawSplash(s, time.Second, stepStarting)
		at := s.markRect(r)
		if at.Min.Y < 0 || at.Max.Y >= s.titleY-r.s(35) {
			t.Fatalf("%s: mark at %v runs off the top or into the wordmark", th.name, at)
		}
		if !at.In(img.Rect) {
			t.Fatalf("%s: mark at %v is off the panel", th.name, at)
		}
		// The serpent crosses the middle of the shell, so the centre is never the ground.
		c := img.RGBAAt(at.Min.X+at.Dx()/2, at.Min.Y+at.Dy()/2)
		if c == walnut {
			t.Errorf("%s: no mark drawn", th.name)
		}
		if other, ok := seen[c]; ok {
			t.Errorf("%s draws the same mark as %s", th.name, other)
		}
		seen[c] = th.name
	}
}

func TestSplashMarkForCustomPalette(t *testing.T) {
	ground := color.RGBA{0x10, 0x10, 0x10, 0xff}
	for _, th := range themes {
		if got := paletteOf(ground, th.colors[roleAccent]); got != th.name {
			t.Errorf("custom palette with %s's accent gets %s's mark", th.name, got)
		}
	}
	if got := paletteOf(ground, color.RGBA{0xe0, 0x70, 0x90, 0xff}); got != "Sakura Jade" {
		t.Errorf("a pink accent gets %s's mark, want Sakura Jade's", got)
	}
	if markFor(themes[0].colors[roleGround], themes[0].colors[roleAccent], 150) == nil {
		t.Error("the White Jade mark cannot be read")
	}
}
