//go:build spot

package display

import (
	"image/color"
	"log/slog"
	"math"
	"sync"

	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
)

// setMusicStrip saves the choice; the Spot has no strip of its own, and hides the row.
func (d *Display) setMusicStrip(i int) { setStrip(nil, i) }

// The star on the round face, mirroring Done on the other side of play and pause.
const starX, starY, starR = center + 88.0, 420.0, 25.0

// spotFaved is the last press of the star.
var spotFaved struct {
	sync.Mutex
	favPress
}

// onStar reports whether a tap at x, y is on the star.
func onStar(x, y int) bool {
	dx, dy := float64(x)-starX, float64(y)-starY
	return dx*dx+dy*dy <= (starR+8)*(starR+8)
}

// favoriteSpot saves what is playing to favorites, or takes it out again when the star is filled.
func (d *Display) favoriteSpot() {
	rd := home.Get().Radio()
	on := spotStarFilled(rd)
	var err error
	if on {
		err = home.Get().UnfavoriteNow()
	} else {
		err = home.Get().FavoriteNow()
	}
	if err != nil {
		slog.Warn("changing a favorite failed", "err", err, "was", on)
		return
	}
	spotFaved.Lock()
	spotFaved.favPress = favPress{favKeyOf(rd), !on}
	spotFaved.Unlock()
	d.wake()
}

// spotStarFilled is whether what is playing is a favorite: Music Assistant's mark on the track, or the
// station on a favorites list, with a press of the star standing in until those catch up.
func spotStarFilled(rd home.Radio) bool {
	mp := home.Get().MusicPlayback()
	listed := home.StationFavorite(rd.Now)
	if (mp.Playing || mp.Paused) && mp.Title != "" && mp.Title == rd.Title {
		listed = mp.Favorite
	}
	spotFaved.Lock()
	defer spotFaved.Unlock()
	return spotFaved.shown(favKeyOf(rd), listed)
}

// starMark fills a five-pointed star as a fan of triangles from its middle.
func (r *roundRenderer) starMark(cx, cy, rad float64, c color.RGBA) {
	var px, py [10]float64
	for i := range px {
		a := -math.Pi/2 + float64(i)*math.Pi/5
		rr := rad
		if i%2 == 1 {
			rr *= 0.45
		}
		px[i], py[i] = cx+rr*math.Cos(a), cy+rr*math.Sin(a)
	}
	for i := range px {
		j := (i + 1) % len(px)
		r.triangle(cx, cy, px[i], py[i], px[j], py[j], c)
	}
}
