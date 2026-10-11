//go:build !dot && !spot

package display

import (
	"image"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// sceneThemes are the presets and a dark custom palette, the splash's scene drawn on each.
func sceneThemes() []theme {
	return append(append([]theme{}, themes...),
		preset(customName, 0x1c1511, 0xe9a23b, 0xe8dcc8, 0x8a7d6c, 0x3a2c22))
}

// The words over the scene have to read about as well as they would on the bare ground: behind the
// wordmark and the step line, no pixel of the scene takes more than a quarter off the contrast, or
// brings it under 7:1 (WCAG's AAA) where the ground gives more than enough.
// With SHOW_PREVIEW set, the splash is written there for every theme on both panels.
func TestSplashSceneKeepsTheWordsReadable(t *testing.T) {
	defer applyTheme(themes[0])
	dir := os.Getenv("SHOW_PREVIEW")
	for _, th := range sceneThemes() {
		applyTheme(th)
		for _, panel := range []struct {
			name       string
			wide, high int
		}{{"", showWide, showHigh}, {"-show8", show8Wide, show8High}} {
			img := image.NewRGBA(image.Rect(0, 0, panel.wide, panel.high))
			r := newRenderer(img)
			s := newSplash(r)
			scene := r.splashScene(s, s.markRect(r).Min.Y >= 0)
			bar := s.barRect(r)
			title := r.width(r.title, "GENBU")
			for _, area := range []struct {
				what string
				rect image.Rectangle
			}{
				{"wordmark", image.Rect((r.w-title)/2, s.markRect(r).Max.Y, (r.w+title)/2, bar.Min.Y)},
				{"step line", image.Rect(bar.Min.X, bar.Max.Y, bar.Max.X, bar.Max.Y+r.s(56))},
			} {
				want := min(0.75*contrast(cream, walnut), 7)
				for y := area.rect.Min.Y; y < area.rect.Max.Y; y++ {
					for x := area.rect.Min.X; x < area.rect.Max.X; x++ {
						if c := contrast(cream, scene.RGBAAt(x, y)); c < want {
							t.Fatalf("%s%s: the %s's text has %.1f:1 at %d,%d, want %.1f (the ground gives %.1f)",
								th.name, panel.name, area.what, c, x, y, want, contrast(cream, walnut))
						}
					}
				}
			}
			if dir == "" {
				continue
			}
			settle(r, s, stepHomeAssistant, 0, 2*time.Second)
			f, err := os.Create(filepath.Join(dir, "splash-"+strings.ToLower(strings.ReplaceAll(th.name, " ", "-"))+panel.name+".png"))
			if err != nil {
				t.Fatal(err)
			}
			if err := png.Encode(f, img); err != nil {
				t.Fatal(err)
			}
			f.Close()
		}
	}
}

// Genbu is drawn whole on the throne: the tail under the shell hangs over the seat, it is not cut
// off.
func TestSplashDrawsGenbusTail(t *testing.T) {
	defer applyTheme(themes[0])
	for _, th := range sceneThemes() {
		applyTheme(th)
		img := image.NewRGBA(image.Rect(0, 0, showWide, showHigh))
		r := newRenderer(img)
		s := newSplash(r)
		r.drawSplash(s, 0, stepStarting)
		scene := r.splashScene(s, true)
		m := s.markRect(r)
		tail := image.Rect(m.Min.X, m.Min.Y+m.Dy()*76/100, m.Max.X, m.Min.Y+m.Dy()*90/100)
		drawn := 0
		for y := tail.Min.Y; y < tail.Max.Y; y++ {
			for x := tail.Min.X; x < tail.Max.X; x++ {
				if img.RGBAAt(x, y) != scene.RGBAAt(x, y) {
					drawn++
				}
			}
		}
		if drawn < tail.Dx()*tail.Dy()/10 {
			t.Errorf("%s: only %d pixels of the tail drawn", th.name, drawn)
		}
	}
}

// The scene follows the theme, and is drawn once for it, not every frame.
func TestSplashSceneFollowsTheTheme(t *testing.T) {
	defer applyTheme(themes[0])
	r := newRenderer(image.NewRGBA(image.Rect(0, 0, showWide, showHigh)))
	s := newSplash(r)
	applyTheme(themes[themeIndex("Leaf Jade")])
	leaf := r.splashScene(s, true)
	if again := r.splashScene(s, true); again != leaf {
		t.Error("the scene was drawn again for the same theme")
	}
	applyTheme(themes[themeIndex("Sakura Jade")])
	sakura := r.splashScene(s, true)
	// A pixel of the jungle in the top left corner, and one of the temple's stone.
	for _, at := range []image.Point{{4, 4}, {r.w / 2, r.h * 3 / 4}} {
		if leaf.RGBAAt(at.X, at.Y) == sakura.RGBAAt(at.X, at.Y) {
			t.Errorf("Leaf and Sakura have the same color at %v", at)
		}
	}
}
