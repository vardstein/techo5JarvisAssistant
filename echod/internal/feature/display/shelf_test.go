//go:build !dot && !spot

package display

import (
	"image"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
)

// A tile sliding off the left of the shelf takes its name and icon with it: none of it is pulled back
// onto the panel, under the next tile (on 2026-10-12 every left-most station's name ran into the next).
func TestShelfTileSlidesOffWhole(t *testing.T) {
	for _, m := range menuSizes {
		useMenuSize(menuSizeIndexOf(m.percent))
		img := image.NewRGBA(image.Rect(0, 0, showWide, showHigh))
		r := newRenderer(img)
		art, step, x0 := r.shelfArt(), r.shelfStep(), r.cz(carIn)
		// Tile 0 shows its last 6 px; the gap and tile 1 start right after.
		s := scene{now: time.Date(2026, 9, 18, 15, 0, 0, 0, time.Local), phase: "idle", showDrawer: true, drawerTab: drawerRadio,
			drawerScroll: x0 + art - 6,
			radio:        home.Radio{Configured: true, Source: config.RadioLocal, Stations: []string{"France Info", "RTL Info", "Anon.FM", "BBC World Service", "SWR3"}}}
		r.draw(s)
		gapTo := x0 + step - s.drawerScroll // tile 1's left edge
		for y := r.cz(shelfBand); y < showHigh; y++ {
			for x := 6; x < gapTo; x++ {
				if c := img.RGBAAt(x, y); c.R != walnut.R || c.G != walnut.G || c.B != walnut.B {
					t.Fatalf("at %d%%: (%d,%d) is %v, not the ground: something of the tile gone off the left was pulled back", m.percent, x, y, c)
				}
			}
		}
	}
	useMenuSize(menuSizeIndexOf(100))
}
