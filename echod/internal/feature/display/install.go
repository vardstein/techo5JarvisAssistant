//go:build !dot && !spot

package display

import (
	"image"
	"image/color"
	"image/draw"
	"math"
	"time"
)

// installFrame is how often the screen redraws while an update installs: often enough for the
// spinner to turn rather than tick.
const installFrame = 100 * time.Millisecond

// installPill is the install, over whatever page is up: a spinner, the line, and a bar under them that
// fills with the download and then sweeps while the slot is written. It floats near the top, clear of
// the edge, and under the microphone's pill when that is up, so the two never cover each other.
func (r *renderer) installPill(s scene) {
	if s.install == "" || r.modalUp() != modalNone {
		return
	}
	spin := r.s(11)
	gap := r.s(12)
	pad := r.s(18)
	w := 2*spin + gap + r.width(r.tiny, s.install)
	x := (r.w - w) / 2
	top := r.s(18)
	if s.muted && !s.showSheet {
		top = r.s(32) + r.s(14) // the microphone's pill ends at 32
	}
	b := image.Rect(x-pad, top, x+w+pad, top+r.s(50))
	r.roundButton(b, float64(r.s(14)), walnut)
	r.spinner(x+spin, top+r.s(21), spin, s.now)
	r.text(r.tiny, s.install, x+2*spin+gap, top+r.s(29), cream)

	// The bar runs along the pill's lower edge, inside its rounding.
	bar := image.Rect(b.Min.X+r.s(14), b.Max.Y-r.s(12), b.Max.X-r.s(14), b.Max.Y-r.s(7))
	r.band(bar, ember)
	if s.installAt >= 0 {
		r.band(image.Rect(bar.Min.X, bar.Min.Y, bar.Min.X+int(float32(bar.Dx())*min(s.installAt, 1)), bar.Max.Y), amber)
		return
	}
	// A third of the bar going back and forth: something is happening, with no promise of how much.
	seg := bar.Dx() / 3
	phase := float64(s.now.UnixMilli()%1600) / 1600
	off := int((1 - math.Cos(2*math.Pi*phase)) / 2 * float64(bar.Dx()-seg))
	r.band(image.Rect(bar.Min.X+off, bar.Min.Y, bar.Min.X+off+seg, bar.Max.Y), amber)
}

// spinner is eight dots in a ring, one bright and the ones behind it fading, turning once a second.
func (r *renderer) spinner(cx, cy, rad int, now time.Time) {
	const dots = 8
	lead := int(now.UnixMilli()/(1000/dots)) % dots
	dot := max(rad/3, 2)
	for i := range dots {
		a := 2 * math.Pi * float64(i) / dots
		x := cx + int(float64(rad-dot)*math.Sin(a))
		y := cy - int(float64(rad-dot)*math.Cos(a))
		r.disc(x, y, dot, lerp(amber, ember, float64((lead-i+dots)%dots)/dots))
	}
}

// band fills b with c.
func (r *renderer) band(b image.Rectangle, c color.RGBA) {
	draw.Draw(r.dst, b, image.NewUniform(c), image.Point{}, draw.Src)
}
