//go:build !dot && !spot

package display

import (
	"image"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// What comes next fits its line in English and German on both panels, as the queue names it: by giving up
// the size, the take and who plays it before a letter of the song is cut. A Show had "Als Nächstes ·
// Christina …".
func TestUpNextFits(t *testing.T) {
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	t.Cleanup(i18n.Changed)
	nexts := []string{
		"Christina Aguilera – Genie in a Bottle",
		"Christina Aguilera - Genie in a Bottle (Remastered 2011)",
		"LL Cool J/Jennifer Lopez - All I Have (feat. Jennifer Lopez)",
		"Short",
	}
	for _, lang := range []string{"", "de"} {
		if err := config.Set().Screen().Language(lang); err != nil {
			t.Fatal(err)
		}
		i18n.Changed()
		for _, panel := range []image.Point{{X: showWide, Y: showHigh}, {X: show8Wide, Y: show8High}} {
			img := image.NewRGBA(image.Rect(0, 0, panel.X, panel.Y))
			r := newRenderer(img)
			_, col := r.nowPlayingLayout()
			for _, n := range nexts {
				_, text := r.upNext(n, col.Dx())
				text = i18n.T(text) // as drawn
				if strings.HasSuffix(text, "…") {
					t.Errorf("%q %v: %q is cut: %q", lang, panel, n, text)
				}
				if !strings.Contains(n, strings.TrimPrefix(text, i18n.T("Next")+"  ·  ")) && !strings.Contains(text, "Genie") && !strings.Contains(text, "All I Have") {
					t.Errorf("%q %v: %q lost the song: %q", lang, panel, n, text)
				}
			}
		}
		if dir := os.Getenv("SHOW_PREVIEW"); dir != "" {
			s := scene{now: time.Date(2026, 9, 18, 15, 0, 0, 0, time.Local), phase: "idle", nowPlaying: true, playing: true,
				radio: home.Radio{Playing: true, Now: "Music Assistant", Title: "All I Have", Artist: "LL Cool J/Jennifer Lopez",
					Album: "This Is Me...Then", Music: true},
				music: home.MusicPlaybackView{Next: nexts[1]}}
			writePNG(t, filepath.Join(dir, "upnext"+map[string]string{"de": "-de"}[lang]+".png"), frame(s))
		}
	}
}
