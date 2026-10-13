//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// Every page of the starter guide fits its card on both panels in every language the screen speaks:
// the heading inside the card, the last line clear of the dots and the hints at the foot.
// With SHOW_PREVIEW set, each page is written there.
func TestStarterGuideFitsThePanel(t *testing.T) {
	applyTheme(themes[0])
	defer applyTheme(themes[0])
	defer i18n.Changed()
	dir := os.Getenv("SHOW_PREVIEW")
	for _, lang := range []string{"", "de", "es", "fr", "it", "nl"} {
		config.Use(filepath.Join(t.TempDir(), "state.json"))
		if err := config.Set().Screen().Language(lang); err != nil {
			t.Fatal(err)
		}
		i18n.Changed()
		for _, panel := range []struct {
			name       string
			wide, high int
		}{{"", showWide, showHigh}, {"-show8", show8Wide, show8High}} {
			img := image.NewRGBA(image.Rect(0, 0, panel.wide, panel.high))
			r := newRenderer(img)
			card := r.guideCard()
			for n, p := range guidePages() {
				where := fmt.Sprintf("%q%s page %d", lang, panel.name, n+1)
				dots := r.s(24)*len(guidePages()) + r.s(16)
				if w := r.width(r.title, p.title); r.s(40)+w > card.Dx()-r.s(40)-dots {
					t.Errorf("%s: the heading %q runs into the page dots", where, i18n.T(p.title))
				}
				if w := r.width(r.small, "Skip") + r.width(r.small, "Tap for the next page"); w > card.Dx()-2*r.s(40)-r.s(24) {
					t.Errorf("%s: Skip and the hint run into each other", where)
				}
				lines := r.guideLines(p)
				last := lines[len(lines)-1]
				room := r.guideFoot() - r.small.Metrics().Ascent.Ceil() - r.s(16)
				if bottom := last.y + r.small.Metrics().Descent.Ceil(); bottom > room {
					t.Errorf("%s: the text runs to %d, into the foot at %d (%q)", where, bottom, room, last.text)
				}
				if dir == "" || (lang != "" && lang != "de") {
					continue
				}
				r.welcome(scene{}, n)
				name := fmt.Sprintf("guide%s-%d%s.png", suffix(lang), n+1, panel.name)
				f, err := os.Create(filepath.Join(dir, name))
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
}

// With SHOW_PREVIEW set, the first page is written there in every theme as well.
func TestStarterGuideInEveryTheme(t *testing.T) {
	dir := os.Getenv("SHOW_PREVIEW")
	if dir == "" {
		t.Skip("previews only")
	}
	defer applyTheme(themes[0])
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	for _, th := range sceneThemes() {
		applyTheme(th)
		img := image.NewRGBA(image.Rect(0, 0, showWide, showHigh))
		newRenderer(img).welcome(scene{}, 0)
		f, err := os.Create(filepath.Join(dir, "guide-theme-"+strings.ToLower(strings.ReplaceAll(th.name, " ", "-"))+".png"))
		if err != nil {
			t.Fatal(err)
		}
		if err := png.Encode(f, img); err != nil {
			t.Fatal(err)
		}
		f.Close()
	}
}

func suffix(lang string) string {
	if lang == "" {
		return ""
	}
	return "-" + lang
}

// A tap is the next page; the last page's tap puts the guide away and saves that, so it stays away.
// Skip puts it away from any page.
func TestStarterGuideTapsThrough(t *testing.T) {
	newDisplay := func() *Display {
		config.Use(filepath.Join(t.TempDir(), "state.json"))
		return &Display{
			poke:  make(chan struct{}, 1),
			view:  voice.State{Phase: "idle"},
			power: newPower(), bright: newLevel(),
			on: true, ceiling: 60,
			r: newRenderer(image.NewRGBA(image.Rect(0, 0, showWide, showHigh))),
		}
	}
	middle := func(d *Display) touch.Gesture {
		return touch.Gesture{Kind: touch.Tap, X: d.r.w / 2, Y: d.r.h / 2}
	}

	d := newDisplay()
	for n := 1; n < len(guidePages()); n++ {
		d.gesture(middle(d))
		if config.Get().Screen.Welcomed {
			t.Fatalf("the guide went away after %d taps, it has %d pages", n, len(guidePages()))
		}
		if d.guidePage != n {
			t.Fatalf("after %d taps the guide is on page %d", n, d.guidePage+1)
		}
	}
	d.gesture(middle(d))
	if !config.Get().Screen.Welcomed {
		t.Fatal("the last page's tap left the guide up")
	}
	if d.guidePage != 0 {
		t.Fatalf("the put-away guide kept page %d, a reset would bring it back there", d.guidePage+1)
	}

	d = newDisplay()
	skip := d.r.guideSkip()
	d.gesture(touch.Gesture{Kind: touch.Tap, X: skip.Min.X + skip.Dx()/2, Y: skip.Min.Y + skip.Dy()/2})
	if !config.Get().Screen.Welcomed {
		t.Fatal("Skip on the first page left the guide up")
	}
}
