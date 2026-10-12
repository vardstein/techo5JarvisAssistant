//go:build !dot && !spot

package display

import (
	"image/color"
	"math"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
)

// The Classic turn page, laid out like the Car Thing's voice screen: the theme's ground, three rings in
// the bottom corner that ripple with the voice being heard, pull in and pulse while the answer is
// worked out and breathe with the answer being spoken, and the words in big bold type from the top
// corner. Sizes are the Car Thing's own (its screen is 800 by 480, near enough a Show 5's), at the
// panel's scale rather than the menu size, since nobody reaches for this page.

// turnFaces is the Car Thing's type scale, largest first: the question while it is all there is, then
// the steps the answer is fitted down through.
type turnFaces struct {
	forte, brio, alto, canon, cello font.Face
	label                           font.Face // canon in bold, for what the page is doing
}

// turnType is the page's type at this panel's size, made once.
func (r *renderer) turnType() *turnFaces {
	if r.turnFc != nil {
		return r.turnFc
	}
	bold, regular := carFonts()
	f := func(fn *opentype.Font, size int) font.Face {
		fc, _ := opentype.NewFace(fn, &opentype.FaceOptions{Size: float64(r.s(size)), DPI: 72, Hinting: font.HintingFull})
		return fc
	}
	r.turnFc = &turnFaces{forte: f(bold, 72), brio: f(bold, 52), alto: f(bold, 44), canon: f(regular, 36), cello: f(regular, 32), label: f(bold, 36)}
	return r.turnFc
}

// turnStep is one size words can be set in, and its line height.
type turnStep struct {
	face  font.Face
	lineH int
}

// The ring box's size, the gap round the page, and how long the rings take to move between phases.
const (
	ringBoxW  = 150
	ringBoxH  = 156
	turnPad   = 40
	ringShift = 450 * time.Millisecond
)

// carTurn draws the whole turn page in the Classic style.
func (r *renderer) carTurn(s scene) {
	r.fill(r.dst.Rect, walnut)
	tf := r.turnType()
	pad := r.s(turnPad)
	maxW := r.w - 2*pad

	// The rings sit in the bottom corner; while an answer is spoken they shrink to make room for it.
	answer := s.phase == "replying" || s.phase == "lingering"
	scale := 1.0
	if answer {
		scale = 0.5
	}
	boxW, boxH := float64(r.s(ringBoxW))*scale, float64(r.s(ringBoxH))*scale
	boxTop := float64(r.h-pad) - boxH
	cy := boxTop + boxH*78/156
	r.rings(s, float64(pad)+boxW/2, cy, scale)

	// What it is doing beside the rings, and the time on the other side of the same line.
	var label string
	switch s.phase {
	case "listening":
		label = "Listening…"
	case "thinking":
		label = "Thinking…"
	}
	lineFace := tf.alto
	if answer {
		lineFace = tf.canon
	}
	base := mid(lineFace, int(cy))
	if label != "" {
		r.text(lineFace, label, pad+int(boxW)+r.s(24), base, softInk())
	}
	clock := clockText(s.now)
	r.text(tf.canon, clock, r.w-pad-r.width(tf.canon, clock), mid(tf.canon, int(cy)), subInk())

	top := pad
	bottom := int(boxTop) - r.s(20)
	if answer {
		bottom = int(boxTop) - r.s(8) // the small rings leave the answer more of the page
	}
	heard := sentenceCase(s.heard)
	if !answer {
		// The question, as big as three lines allow.
		if heard != "" {
			r.turnWords(heard, top, bottom, maxW, 3, cream, turnStep{tf.forte, r.s(72)}, turnStep{tf.brio, r.s(60)})
		}
		return
	}

	// The question a line in quiet type, and the answer under it as big as it fits.
	if heard != "" {
		r.text(tf.canon, r.fit(tf.canon, heard, maxW), pad, top+r.s(36), softInk())
		top += r.s(48) + r.s(16)
	}
	if s.reply != "" {
		r.turnWords(s.reply, top, bottom, maxW, 0, cream,
			turnStep{tf.brio, r.s(60)}, turnStep{tf.alto, r.s(49)}, turnStep{tf.canon, r.s(48)}, turnStep{tf.cello, r.s(40)})
	}
}

// turnWords sets text from top in the first step that fits above bottom in at most most lines (no
// limit when 0), or in the last step cut short with an ellipsis.
func (r *renderer) turnWords(text string, top, bottom, maxW, most int, c color.RGBA, steps ...turnStep) {
	var st turnStep
	var lines []string
	for _, st = range steps {
		lines = r.wrap(st.face, text, maxW)
		fits := len(lines)*st.lineH <= bottom-top
		if fits && (most == 0 || len(lines) <= most) {
			break
		}
	}
	room := max(1, (bottom-top)/st.lineH)
	if most > 0 {
		room = min(room, most)
	}
	if len(lines) > room {
		lines = lines[:room]
		lines[room-1] = r.fit(st.face, lines[room-1]+"…", maxW)
	}
	// The first baseline sits a cap height under top, so the words line up with the page's edge.
	asc := st.face.Metrics().CapHeight.Ceil()
	if asc == 0 {
		asc = st.face.Metrics().Ascent.Ceil() * 7 / 10
	}
	y := top + asc
	for _, l := range lines {
		r.text(st.face, l, r.s(turnPad), y, c)
		y += st.lineH
	}
}

// sentenceCase is s with its first letter a capital, as the Car Thing shows what it heard.
func sentenceCase(s string) string {
	s = strings.TrimSpace(s)
	c, n := utf8.DecodeRuneInString(s)
	if n == 0 || !unicode.IsLower(c) {
		return s
	}
	return string(unicode.ToUpper(c)) + s[n:]
}

// ringInk is the three rings' colors on the theme: the accent, the accent toward the text, and the
// text, each a little fainter than the last.
func ringInk() [3]color.RGBA {
	return [3]color.RGBA{amber, lerp(amber, cream, 0.45), cream}
}

var ringAlpha = [3]float64{1, 0.8, 0.5}

// rings draws the three rings around (cx, cy) at scale.
//
// While it listens all three lie on one circle, and the part of it facing the page ripples with the
// voice: the Car Thing's jellyfish, whose ripple is the radius pulled in by an amount that rides a sine
// and fades out away from one point of the circle. While it thinks they part into three circles that
// swell and settle together. While the answer is spoken they keep those circles and swell with it.
func (r *renderer) rings(s scene, cx, cy, scale float64) {
	level := 0.0
	if s.eq != nil && s.eq.level != nil && !s.eq.thinking {
		for _, v := range s.eq.level {
			level += v
		}
		level /= float64(len(s.eq.level))
	}
	secs := float64(s.now.UnixMilli()%1_000_000) / 1000
	// The ripple turns at the Car Thing's 0.02π a frame at sixty frames a second.
	phase := math.Mod(secs*1.2*math.Pi, 2*math.Pi)
	// The thinking pulse: out by up to 8 and back, at the Car Thing's 0.18 a frame.
	tick := math.Mod(secs*60*0.18, 16)
	pulse := tick
	if tick > 8 {
		pulse = 16 - tick
	}
	// A phase just begun moves from where the last one left the rings.
	into := 1.0
	if !s.since.IsZero() {
		into = easeOutExpo(min(1, float64(s.now.Sub(s.since))/float64(ringShift)))
	}

	type ring struct{ radius, amp, freq float64 }
	listen := [3]ring{{72, 14, 3.3}, {72, 6, 3.3}, {72, 1, 1}}
	think := [3]float64{62, 47, 30}
	u := float64(r.s(100)) / 100 * scale
	w := 6 * u
	var radius [3]func(a float64) float64
	for i := range radius {
		switch s.phase {
		case "listening":
			l := listen[i]
			// The Car Thing's level is a running average that rarely passes a third; the bands' mean
			// runs higher, so it counts for less here.
			amp := l.amp * (1 + level*1.2) * into
			radius[i] = func(a float64) float64 { return jellyRadius(a, l.radius, l.freq, phase, amp) }
		case "thinking":
			// From the one listening circle to three, eased.
			rad := 72 + (think[i]+pulse-72)*into
			radius[i] = func(float64) float64 { return rad }
		default:
			rad := think[i] + 10*level
			radius[i] = func(float64) float64 { return rad }
		}
	}
	r.warpRings(cx, cy, u, w, radius, ringInk(), s.phase == "listening")
}

// jellyRadius is the listening ring's radius at angle a (degrees, y down): the Car Thing's warpedRadius,
// with its turn for the microphone facing straight ahead.
func jellyRadius(a, radius, freq, phase, amp float64) float64 {
	x := (a + 102 - 100) / radius
	for x > 180/radius {
		x -= 360 / radius
	}
	act := 20 / (20 + math.Pow(x*2.8, 4))
	return radius - act*act*(amp+amp*math.Sin(x*freq-phase))
}

// warpRings draws three closed lines of width w around (cx, cy) whose radius at each angle (degrees, y
// down, in the rings' own units) is radius[i](a), each unit u pixels, the first over the others. Unless
// warped, each radius is the same all the way round.
//
// A Show 5 has about 66 ms for a whole frame, and asking every pixel near the rings its angle and how far
// it is from each line took most of it. So the radii and how steeply they change are read once a frame
// into tables of half degrees, each pixel's distance and angle are worked out once for all three rings,
// and pixels nowhere near any ring are passed over before either.
func (r *renderer) warpRings(cx, cy, u, w float64, radius [3]func(a float64) float64, ink [3]color.RGBA, warped bool) {
	const steps = 720
	var tab, slope [3][steps + 1]float64
	near, reach := math.Inf(1), 0.0
	for k := range radius {
		n := 1
		if warped {
			n = steps + 1
		}
		for i := range n {
			tab[k][i] = radius[k](float64(i)/2-180) * u
			near, reach = min(near, tab[k][i]), max(reach, tab[k][i])
		}
		if !warped {
			continue
		}
		// How many pixels the radius moves for a degree.
		for i := range tab[k] {
			lo, hi := max(i-2, 0), min(i+2, steps)
			slope[k][i] = (tab[k][hi] - tab[k][lo]) / (float64(hi-lo) / 2)
		}
	}
	// The ripple only pulls a ring in on the side facing the page, so each side gets its own inner
	// bound: behind (angles past 90), beside (45 to 90) and ahead (within 45).
	var side [3]float64
	if warped {
		side = [3]float64{math.Inf(1), math.Inf(1), math.Inf(1)}
		for k := range tab {
			for i, v := range tab[k] {
				a := math.Abs(float64(i)/2 - 180)
				s := 0
				if a <= 45 {
					s = 2
				} else if a <= 90 {
					s = 1
				}
				side[s] = min(side[s], v)
			}
		}
	} else {
		side = [3]float64{near, near, near}
	}
	var in2 [3]float64
	for s, v := range side {
		in2[s] = max(0, v-w) * max(0, v-w)
	}
	out := reach + w
	out2 := out * out
	x0, x1 := max(0, int(cx-out)), min(r.w-1, int(cx+out)+1)
	y0, y1 := max(0, int(cy-out)), min(r.h-1, int(cy+out)+1)
	for y := y0; y <= y1; y++ {
		py := float64(y) + 0.5 - cy
		for x := x0; x <= x1; x++ {
			px := float64(x) + 0.5 - cx
			d2 := px*px + py*py
			s := 0
			if px > 0 {
				s = 1
				if px >= math.Abs(py) {
					s = 2
				}
			}
			if d2 < in2[s] || d2 > out2 {
				continue
			}
			d := math.Sqrt(d2)
			f, i, frac := 0.0, 0, 0.0
			if warped {
				f = (math.Atan2(py, px)*180/math.Pi + 180) * 2
				i = max(0, min(int(f), steps-1))
				frac = f - float64(i)
			}
			// The last ring first, so the first is drawn over it.
			for k := 2; k >= 0; k-- {
				rr, sl := tab[k][0], 0.0
				if warped {
					rr = tab[k][i] + (tab[k][i+1]-tab[k][i])*frac
					sl = slope[k][i] / (d * math.Pi / 180)
				}
				off := math.Abs(d - rr)
				if off > w {
					continue
				}
				// Measured along the radius, corrected for how steeply the line runs there.
				if sl != 0 {
					off /= math.Sqrt(1 + sl*sl)
				}
				if a := clamp01(0.5 - (off - w/2)); a > 0 {
					r.blendAt(x, y, ink[k], ringAlpha[k]*a)
				}
			}
		}
	}
}

func easeOutExpo(x float64) float64 {
	if x >= 1 {
		return 1
	}
	return 1 - math.Pow(2, -10*x)
}
