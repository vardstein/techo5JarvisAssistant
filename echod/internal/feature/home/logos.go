package home

import (
	"context"
	"image"
	"image/draw"
	_ "image/gif" // some stations' logos
	"log/slog"
	neturl "net/url"
	"strings"
	"time"

	xdraw "golang.org/x/image/draw"
	_ "golang.org/x/image/webp" // Radio Browser serves a fair share of logos as WebP

	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/radiometa"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

// The tiles on the radio shelf show each station's logo, as the Car Thing's shelf showed each one's
// art. Radio Browser's lists carry a picture for most stations; a station from Home Assistant's own
// lists or the device's is looked up the way the playing one is, on iHeartRadio or TuneIn. They are
// fetched while the shelf is up, a couple at a time, and kept small: a list can be fifty stations
// long, and a tile is a couple of hundred pixels across.

const (
	// logoSide is the largest a kept logo is: the Show's tile with room round the logo. One that
	// comes smaller is kept at its own size and the tile scales it.
	logoSide = 192
	// logosKept is how many logos are kept at once, the oldest let go first: two lists' worth.
	logosKept = 120
	// logoRetry is how long a station whose logo could not be had waits before it is asked again.
	logoRetry = 6 * time.Hour
	// logosAtOnce is how many logos are fetched at once.
	logosAtOnce = 2
)

// logoKept is one station's logo, or the note that it has none for now.
type logoKept struct {
	img  *image.RGBA
	at   time.Time // fetched, or failed
	busy bool
}

type logoCache struct {
	byName  map[string]*logoKept
	order   []string // the names in the order they were fetched, for letting the oldest go
	running int
	queue   []string
}

// StationLogos is the logos there are for names, by name, and starts fetching the ones there are not
// yet. Changed fires as each arrives. names is what the shelf shows; a station the lists know a
// picture for is fetched from it, any other is looked up by name.
func (f *Feature) StationLogos(names []string) map[string]*image.RGBA {
	f.mu.Lock()
	defer f.mu.Unlock()
	c := &f.logos
	if c.byName == nil {
		c.byName = map[string]*logoKept{}
	}
	out := map[string]*image.RGBA{}
	for _, name := range names {
		k := c.byName[name]
		switch {
		case k != nil && k.img != nil:
			out[name] = k.img
		case k != nil && (k.busy || time.Since(k.at) < logoRetry):
		default:
			if k == nil {
				k = &logoKept{}
				c.byName[name] = k
			}
			k.busy = true
			c.queue = append(c.queue, name)
		}
	}
	for c.running < logosAtOnce && len(c.queue) > 0 {
		c.running++
		f.fetchLogos()
	}
	return out
}

// fetchLogos fetches queued logos one after another until the queue is empty. f.mu is held.
func (f *Feature) fetchLogos() {
	safe.Go("radio: fetching station logos", func() {
		for {
			f.mu.Lock()
			c := &f.logos
			if len(c.queue) == 0 {
				c.running--
				f.mu.Unlock()
				return
			}
			name := c.queue[0]
			c.queue = c.queue[1:]
			thumb := f.listedThumb(name)
			f.mu.Unlock()

			img, err := stationLogo(name, thumb)
			if err != nil {
				slog.Debug("radio: no logo for a station", "err", err)
			}

			f.mu.Lock()
			if k := c.byName[name]; k != nil {
				k.img, k.at, k.busy = img, time.Now(), false
				if img != nil {
					c.order = append(c.order, name)
					for len(c.order) > logosKept {
						delete(c.byName, c.order[0])
						c.order = c.order[1:]
					}
				}
			}
			f.mu.Unlock()
			if img != nil {
				f.Changed.Emit(struct{}{})
			}
		}
	})
}

// listedThumb is the picture the lists from Home Assistant give for name, if they list it. f.mu is held.
func (f *Feature) listedThumb(name string) string {
	for _, l := range []*radioList{&f.lists.ma, &f.lists.local, &f.lists.popular} {
		for _, st := range l.stations {
			if st.Name == name && st.Thumb != "" {
				return st.Thumb
			}
		}
	}
	return ""
}

// stationLogo fetches a station's logo: thumb when the list gave one, else what iHeartRadio or TuneIn
// has for its name. nil with no error is a station with no logo anywhere.
func stationLogo(name, thumb string) (*image.RGBA, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if thumb == "" {
		st, err := radiometa.Resolve(ctx, name)
		if err != nil {
			return nil, err
		}
		thumb = st.Logo
	}
	if !fetchable(thumb) {
		return nil, nil
	}
	b, err := hass.Get().FetchURL(thumb)
	if err != nil {
		return nil, err
	}
	src, err := decodeWithin(b, maxArtPixels, "a station's logo")
	if err != nil {
		return nil, err
	}
	return logoThumb(src), nil
}

// fetchable is a picture's address the device will fetch: a path on Home Assistant, or a web address.
// A list's pictures are whatever someone typed into Radio Browser.
func fetchable(u string) bool {
	if strings.HasPrefix(u, "/") && !strings.HasPrefix(u, "//") {
		return true
	}
	p, err := neturl.Parse(u)
	return err == nil && (p.Scheme == "http" || p.Scheme == "https") && p.Host != ""
}

// logoThumb is src fitted into a square of at most logoSide, centered, clear around it.
func logoThumb(src image.Image) *image.RGBA {
	sb := src.Bounds()
	sw, sh := sb.Dx(), sb.Dy()
	side := min(max(sw, sh), logoSide)
	w, h := side, side
	if sw > sh {
		h = max(sh*side/sw, 1)
	} else {
		w = max(sw*side/sh, 1)
	}
	dst := image.NewRGBA(image.Rect(0, 0, side, side))
	at := image.Rect((side-w)/2, (side-h)/2, (side-w)/2+w, (side-h)/2+h)
	if w == sw && h == sh {
		draw.Draw(dst, at, src, sb.Min, draw.Src)
	} else {
		xdraw.CatmullRom.Scale(dst, at, src, sb, draw.Src, nil)
	}
	return dst
}
