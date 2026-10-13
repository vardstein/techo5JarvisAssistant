//go:build !dot && !spot

package display

import (
	"image"
	"image/color"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
)

func TestWhatCountsAsALiveStation(t *testing.T) {
	for _, c := range []struct {
		name  string
		rd    home.Radio
		music home.MusicPlaybackView
		want  bool
	}{
		{"a track through Music Assistant", home.Radio{Now: "Music Assistant"}, home.MusicPlaybackView{}, false},
		{"a station through Music Assistant", home.Radio{Now: "Music Assistant"}, home.MusicPlaybackView{Station: "Hitradio FFH"}, true},
		{"the Show's own station", home.Radio{Now: "Test FM"}, home.MusicPlaybackView{}, true},
		{"the Show's own station, chosen and coming up", home.Radio{Chosen: "Test FM"}, home.MusicPlaybackView{}, true},
		{"nothing", home.Radio{}, home.MusicPlaybackView{}, false},
	} {
		if got := liveStation(c.rd, c.music); got != c.want {
			t.Errorf("%s: live %v, want %v", c.name, got, c.want)
		}
	}
}

// A station has nothing to skip to and can't be paused, only stopped: the page and the strip draw no
// back or forward, and a square where the pause bars were. The middle of the play button tells them
// apart, the gap between the bars against the inside of the square. (GENBU-152)
func TestAStationDrawsStopAndNothingToSkip(t *testing.T) {
	at := time.Date(2026, 10, 14, 15, 0, 0, 0, time.Local)
	r := testRenderer()
	_, back, play, next, _ := r.nowPlayingButtons()
	sback, splay, snext, _ := r.stripButtons()
	plain := home.Radio{Playing: true, Now: "Music Assistant", Title: "Some Song", Artist: "Someone", Music: true}
	logo := plain
	logo.Thumb, logo.Logo = testCover(400, true), true
	for _, c := range []struct {
		name             string
		s                scene
		back, play, next image.Rectangle
		mark, inStop     color.RGBA // the skip marks' color, and the stop square's
	}{
		// Marks with no buttons under them, on the ground a song without a cover gets.
		{"page", scene{nowPlaying: true, radio: plain}, back, play, next, white, color.RGBA{}},
		// The theme's buttons, under the station's logo.
		{"page-logo", scene{nowPlaying: true, radio: logo}, back, play, next, cream, onAccent()},
		{"strip", scene{strip: true, radio: plain}, sback, splay, snext, amber, amber},
	} {
		c.s.now, c.s.phase, c.s.playing = at, "idle", true
		track := frame(c.s)
		c.s.live = true
		station := frame(c.s)
		if dir := os.Getenv("SHOW_PREVIEW"); dir != "" {
			writePNG(t, filepath.Join(dir, "station-"+c.name+".png"), station)
		}
		for _, b := range []image.Rectangle{c.back, c.next} {
			if n := count(track, b, c.mark); n == 0 {
				t.Fatalf("%s: a track's skip button %v has no mark in %v to look for", c.name, b, c.mark)
			}
			if n := count(station, b, c.mark); n > 0 {
				t.Errorf("%s: %d pixels of a skip mark in %v on a station", c.name, n, b)
			}
		}
		mid := image.Pt((c.play.Min.X+c.play.Max.X)/2, (c.play.Min.Y+c.play.Max.Y)/2)
		if track.RGBAAt(mid.X, mid.Y) == station.RGBAAt(mid.X, mid.Y) {
			t.Errorf("%s: the middle of play is %v for a track and a station alike; want pause against stop",
				c.name, station.RGBAAt(mid.X, mid.Y))
		}
		if c.inStop != (color.RGBA{}) && station.RGBAAt(mid.X, mid.Y) != c.inStop {
			t.Errorf("%s: the middle of play on a station is %v, want the stop square's %v", c.name, station.RGBAAt(mid.X, mid.Y), c.inStop)
		}
	}
}

func count(img *image.RGBA, b image.Rectangle, c color.RGBA) int {
	n := 0
	for y := b.Min.Y; y < b.Max.Y; y++ {
		for x := b.Min.X; x < b.Max.X; x++ {
			if img.RGBAAt(x, y) == c {
				n++
			}
		}
	}
	return n
}
