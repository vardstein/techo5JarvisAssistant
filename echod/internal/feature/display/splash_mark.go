//go:build !dot && !spot

package display

import (
	"bytes"
	"embed"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"log/slog"
	"sync"

	xdraw "golang.org/x/image/draw"
)

// The Genbu mark over the wordmark, in the colors of the screen's palette: the shell, the serpent and
// the tail, drawn by the JODS Genbu generator once per palette and kept here as pictures
// (assets/genbu/README.md says how they were made). A custom palette gets the one whose accent is
// nearest its own.

// markSize is the mark's height and width in the Show 5's pixels.
const markSize = 150

//go:embed assets/genbu/*.png
var markFiles embed.FS

// markFile is the picture drawn for each preset, by its name.
var markFile = map[string]string{
	"Obsidian":    "obsidian",
	"White Jade":  "white",
	"Leaf Jade":   "leafgreen",
	"Sakura Jade": "sakura",
	"Ember Jade":  "ember",
}

// marks holds each picture at the size last asked for, so the splash scales it once and not every
// frame.
var marks struct {
	sync.Mutex
	scaled map[string]*image.RGBA
}

// markFor is the mark for the palette with this ground and accent, size pixels square, or nil if the
// picture cannot be read.
func markFor(ground, accent color.RGBA, size int) *image.RGBA {
	file := markFile[paletteOf(ground, accent)]
	marks.Lock()
	defer marks.Unlock()
	if m := marks.scaled[file]; m != nil && m.Rect.Dx() == size {
		return m
	}
	b, err := markFiles.ReadFile("assets/genbu/" + file + ".png")
	if err != nil {
		slog.Warn("display: no Genbu mark", "file", file, "err", err)
		return nil
	}
	src, err := png.Decode(bytes.NewReader(b))
	if err != nil {
		slog.Warn("display: Genbu mark unreadable", "file", file, "err", err)
		return nil
	}
	m := image.NewRGBA(image.Rect(0, 0, size, size))
	xdraw.CatmullRom.Scale(m, m.Rect, src, src.Bounds(), draw.Src, nil)
	if marks.scaled == nil {
		marks.scaled = map[string]*image.RGBA{}
	}
	marks.scaled[file] = m
	return m
}

// paletteOf is the preset with this ground and accent, or the one whose accent is closest for a
// custom palette.
func paletteOf(ground, accent color.RGBA) string {
	best, bestD := themes[0].name, -1
	for _, t := range themes {
		a := t.colors[roleAccent]
		if a == accent && t.colors[roleGround] == ground {
			return t.name
		}
		dr, dg, db := int(a.R)-int(accent.R), int(a.G)-int(accent.G), int(a.B)-int(accent.B)
		if d := dr*dr + dg*dg + db*db; bestD < 0 || d < bestD {
			best, bestD = t.name, d
		}
	}
	return best
}

// markRect is where the mark goes: centred, ending a little above the wordmark's capitals.
func (s *splash) markRect(r *renderer) image.Rectangle {
	size := r.s(markSize)
	x := (r.w - size) / 2
	bottom := s.titleY - r.s(46)
	return image.Rect(x, bottom-size, x+size, bottom)
}

// drawMark paints the mark of the palette the screen draws with, if it fits above the wordmark. It
// sits on the throne the scene puts under it, its tail over the seat (splash_scene.go).
func (r *renderer) drawMark(s *splash) {
	at := s.markRect(r)
	if at.Min.Y < 0 {
		return
	}
	if m := markFor(walnut, amber, at.Dx()); m != nil {
		draw.Draw(r.dst, at, m, image.Point{}, draw.Over)
	}
}
