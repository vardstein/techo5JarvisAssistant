//go:build !dot && !spot

package display

import (
	"image"
	"image/color"
	"image/draw"
	"math"
	"sync"

	xdraw "golang.org/x/image/draw"
)

// The splash's scene: a Maya step pyramid in the jungle at dusk, drawn as pixel art, with the Genbu
// mark seated on a stone throne on its top platform, its tail hanging down the front of the seat.
// Every color is the palette's ground mixed with a little leaf, stone or fire, weakly in the middle
// where the words go and more strongly at the edges, so the wordmark and the steps of coming up read
// as well on a light palette as on Obsidian. The leaf, stone and fire take on the palette's
// accent and dim text, so the scene changes with the theme. It is drawn once per panel and palette
// and copied in on every frame after.

const (
	// scenePixel is the size of one pixel of the scene, in the Show 5's pixels.
	scenePixel = 4

	// markSeat is where the shell ends in the Genbu picture, from its top: it sits on the seat
	// there, and its tail hangs over the front below (72% of its height in every palette's picture).
	markSeat = 0.72
)

// The hues the scene starts from, before the palette tints them.
var (
	scLeafDeep  = rgb(0x1d4a2a)
	scLeaf      = rgb(0x4a8a3c)
	scStone     = rgb(0x857862)
	scStoneDark = rgb(0x3f362b)
	scFlame     = rgb(0xffa733)
	scFlameCore = rgb(0xffe08a)
)

// sceneKey is what a drawn scene depends on.
type sceneKey struct {
	w, h                int
	ground, accent, dim color.RGBA
	mark                image.Rectangle
	seated              bool
}

// scenes keeps the last scene drawn.
var scenes struct {
	sync.Mutex
	key   sceneKey
	scene *image.RGBA
}

// splashScene is the scene for the splash s on r's screen, drawn under the mark. seated is whether
// the mark fits and so the throne is drawn at all.
func (r *renderer) splashScene(s *splash, seated bool) *image.RGBA {
	key := sceneKey{r.w, r.h, walnut, amber, dim, s.markRect(r), seated}
	scenes.Lock()
	defer scenes.Unlock()
	if scenes.scene != nil && scenes.key == key {
		return scenes.scene
	}
	p := newSplashArt(r.w, r.h, max(2, r.s(scenePixel)), key)
	p.paint()
	scenes.scene, scenes.key = p.blow(), key
	return scenes.scene
}

// splashArt is the drawing in progress, in its own coarse pixels: u screen pixels to one.
type splashArt struct {
	w, h, u int
	back    *image.RGBA
	ground  color.RGBA
	accent  color.RGBA
	seated  bool

	// The hues, tinted with the palette.
	leaf, leafDeep, stone, stoneDark, flame, flameCore color.RGBA

	cx     int // the middle column: shapes are mirrored about its left edge
	markY0 int // the top of the mark
	ms     int // the mark's size
	seatY  int // where the shell sits: the top of the throne's front
	baseY  int // the foot of the throne: the pyramid's top platform
	seatHW int // half the seat's width
	topHW  int // half the top platform's width
	pyB    int // the pyramid's foot
	sky    int // the horizon behind it
}

func newSplashArt(w, h, u int, k sceneKey) *splashArt {
	lw, lh := (w+u-1)/u, (h+u-1)/u
	m := k.mark
	p := &splashArt{
		w: lw, h: lh, u: u,
		back:   image.NewRGBA(image.Rect(0, 0, lw, lh)),
		ground: k.ground, accent: k.accent, seated: k.seated,
		cx:     (m.Min.X + m.Max.X) / 2 / u,
		markY0: m.Min.Y / u,
		ms:     max(m.Dx()/u, 12),
	}
	// The jungle grows toward the accent, the stone toward the dim text, and the fire takes a
	// little of the accent too.
	p.leaf, p.leafDeep = lerp(scLeaf, k.accent, 0.4), lerp(scLeafDeep, lerp(k.accent, k.ground, 0.5), 0.35)
	p.stone, p.stoneDark = lerp(scStone, k.dim, 0.5), lerp(scStoneDark, lerp(k.dim, k.ground, 0.5), 0.4)
	p.flame, p.flameCore = lerp(scFlame, k.accent, 0.25), lerp(scFlameCore, k.accent, 0.15)
	p.seatY = (m.Min.Y + int(markSeat*float64(m.Dy()))) / u
	p.seatHW = p.ms / 2
	p.baseY = p.seatY + p.ms*28/100
	if !k.seated {
		// No mark, no throne: the pyramid's top is where the throne's foot would be.
		p.baseY = max(lh/3, p.baseY)
	}
	p.topHW = p.seatHW + p.ms/3
	p.pyB = lh - max(lh/16, 3)
	p.sky = p.baseY + (p.pyB-p.baseY)*2/5
	return p
}

// tone is the ground with k of c in it.
func (p *splashArt) tone(c color.RGBA, k float64) color.RGBA { return lerp(p.ground, c, k) }

func put(img *image.RGBA, x, y int, c color.RGBA) {
	if image.Pt(x, y).In(img.Rect) {
		img.SetRGBA(x, y, c)
	}
}

// block paints [x0,x1) by [y0,y1).
func block(img *image.RGBA, x0, y0, x1, y1 int, c color.RGBA) {
	draw.Draw(img, image.Rect(x0, y0, x1, y1).Intersect(img.Rect), image.NewUniform(c), image.Point{}, draw.Src)
}

// mirrored paints [cx-x1, cx-x0) and [cx+x0, cx+x1): a pair of spans the same distance either side
// of the middle.
func (p *splashArt) mirrored(img *image.RGBA, x0, y0, x1, y1 int, c color.RGBA) {
	block(img, p.cx-x1, y0, p.cx-x0, y1, c)
	block(img, p.cx+x0, y0, p.cx+x1, y1, c)
}

// bayer is the ordered dither's threshold map, so blends come out as the pixel-art checker.
var bayer = [4][4]float64{{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}}

func dither(x, y int, t float64) bool { return t > (bayer[y&3][x&3]+0.5)/16 }

// noise is a fixed pseudo-random number in [0, 1) for a pixel, so the scene is the same every time.
func noise(x, y int) float64 {
	h := uint32(x)*374761393 + uint32(y)*668265263
	h = (h ^ h>>13) * 1274126177
	return float64(h^h>>16) / (1 << 32)
}

func (p *splashArt) paint() {
	p.paintSky()
	p.paintHills()
	p.paintPyramid()
	if p.seated {
		p.paintBraziers()
		p.paintBackrest()
		p.paintSeat()
	}
	p.paintJungle()
}

// paintSky is the dusk: bands of the accent fading toward the horizon, and a low sun behind the
// throne.
func (p *splashArt) paintSky() {
	const bands = 6
	top, bottom := p.tone(p.accent, 0.24), p.tone(p.accent, 0.04)
	sunR := float64(p.ms) * 0.95
	sunY := float64(p.markY0) + float64(p.ms)*0.45
	for y := range p.h {
		t := clamp01(float64(y) / float64(max(p.sky, 1)))
		band := t * bands
		for x := range p.w {
			k := math.Floor(band)
			if dither(x, y, band-k) {
				k++
			}
			c := lerp(top, bottom, k/bands)
			dx, dy := float64(x-p.cx)+0.5, (float64(y)-sunY)*1.15
			if d := math.Hypot(dx, dy); d < sunR {
				ring := d / sunR
				switch {
				case ring < 0.78:
					c = p.tone(p.flame, 0.30)
				case ring < 0.9 || dither(x, y, (1-ring)*8):
					c = p.tone(p.flame, 0.20)
				}
			}
			p.back.SetRGBA(x, y, c)
		}
	}
}

// paintHills is the jungle far off: two rows of round treetops, the nearer darker, with mist
// between them.
func (p *splashArt) paintHills() {
	for layer, k := range []float64{0.14, 0.22} {
		c, lit := p.tone(p.leafDeep, k), p.tone(p.leaf, k)
		base := p.sky - 4 + layer*4
		for x := 0; x < p.w; x += 5 + int(noise(x, layer)*5) {
			r := 3 + noise(x, layer+7)*4
			cy := float64(base) - noise(x, layer+11)*3
			p.blob(p.back, float64(x), cy, r, c, lit)
		}
		block(p.back, 0, base, p.w, p.pyB, c)
		// Mist in front of each row: a band of the sky's color, thinning downward.
		mist := p.tone(p.accent, 0.04)
		for y := base + 1; y < base+4; y++ {
			k := 0.45 - float64(y-base)*0.12
			for x := range p.w {
				p.back.SetRGBA(x, y, lerp(p.back.RGBAAt(x, y), mist, k))
			}
		}
	}
	// The ground the pyramid stands on.
	block(p.back, 0, p.pyB, p.w, p.h, p.tone(p.leafDeep, 0.30))
	for x := range p.w {
		if noise(x, 3) < 0.35 {
			put(p.back, x, p.pyB-1, p.tone(p.leafDeep, 0.30))
		}
	}
}

// blob is one round treetop: lit on its upper left, its lower edge a shade darker.
func (p *splashArt) blob(img *image.RGBA, cx, cy, r float64, c, lit color.RGBA) {
	shade := lerp(c, p.leafDeep, 0.25)
	for y := int(cy - r); y <= int(cy+r); y++ {
		for x := int(cx - r); x <= int(cx+r); x++ {
			dx, dy := float64(x)+0.5-cx, float64(y)+0.5-cy
			d := math.Hypot(dx, dy)
			if d > r {
				continue
			}
			col := c
			switch side := (dx + dy) / r; {
			case side < -0.55 && d > r*0.35:
				col = lit
			case dy > r*0.55:
				col = shade
			}
			put(img, x, y, col)
		}
	}
}

// paintPyramid is the temple: five sloping tiers with a sunken panel along each, the stair up the
// middle with a ramp either side, and moss and creepers where the stone has gone green.
func (p *splashArt) paintPyramid() {
	const tiers = 5
	face, ledge, shadow := p.tone(p.stone, 0.24), p.tone(p.stone, 0.12), p.tone(p.stoneDark, 0.34)
	panel, moss := p.tone(p.stoneDark, 0.24), p.tone(p.leaf, 0.32)
	baseHW := max(p.w*2/5, p.topHW+tiers*3)
	th := max((p.pyB-p.baseY)/tiers, 3)
	dw := (baseHW - p.topHW) / tiers

	// The top platform.
	p.mirrored(p.back, 0, p.baseY-1, p.topHW, p.baseY, ledge)
	for i := range tiers {
		y0 := p.baseY + i*th
		y1 := y0 + th
		if i == tiers-1 {
			y1 = p.pyB
		}
		hw0 := p.topHW + i*dw
		for y := y0; y < y1; y++ {
			// The lower two thirds of a tier slope out, the upper third stands straight.
			hw := hw0 + 2
			if f := float64(y-y0) / float64(th); f > 0.36 {
				hw += int((f - 0.36) / 0.64 * float64(dw-2))
			}
			c := face
			if y == y0 {
				c = ledge
			}
			p.mirrored(p.back, 0, y, hw, y+1, c)
			// Light from the left: the right-hand end of each tier is in shade.
			block(p.back, p.cx+hw-2, y, p.cx+hw, y+1, shadow)
			block(p.back, p.cx-hw, y, p.cx-hw+1, y+1, ledge)
		}
		// The sunken panels of the upright part, either side of the stair.
		py0, py1 := y0+1, y0+max(th/3, 2)
		for x := p.topHW/2 + 3; x+6 < hw0; x += 9 {
			p.mirrored(p.back, x, py0, x+6, py1, panel)
		}
		// Moss along the ledge, and a few creepers hanging off it.
		for x := p.cx - hw0 - 2; x < p.cx+hw0+2; x++ {
			n := noise(x, y0)
			if n < 0.28 {
				put(p.back, x, y0, moss)
			}
			if n < 0.05 {
				for d := 1; d < 2+int(noise(x, y0+1)*float64(th)); d++ {
					put(p.back, x, y0+d, moss)
				}
			}
		}
	}

	// The stair: treads and risers up the middle, a ramp either side.
	sw := max(p.seatHW*3/5, 4)
	tread, riser, ramp := p.tone(p.stone, 0.16), p.tone(p.stone, 0.30), p.tone(p.stone, 0.34)
	for y := p.baseY; y < p.pyB; y++ {
		grow := (y - p.baseY) / 6
		c := tread
		if (y-p.baseY)%3 == 2 {
			c = riser
		}
		p.mirrored(p.back, 0, y, sw+grow, y+1, c)
		p.mirrored(p.back, sw+grow, y, sw+grow+2, y+1, ramp)
	}
}

// paintBraziers are the two fire bowls on the platform, either side of the throne.
func (p *splashArt) paintBraziers() {
	bowl, leg := p.tone(p.stone, 0.55), p.tone(p.stoneDark, 0.6)
	for _, side := range []int{-1, 1} {
		x := p.cx + side*(p.seatHW+p.ms/5) - 2
		if side < 0 {
			x -= 1
		}
		y := p.baseY - 1
		block(p.back, x, y-2, x+1, y, leg)
		block(p.back, x+4, y-2, x+5, y, leg)
		block(p.back, x-1, y-4, x+6, y-2, bowl)
		block(p.back, x, y-2, x+5, y-1, bowl)
		// The flame: a cone of fire round a pale heart.
		for d, row := range []string{"..x..", ".xxx.", ".xox.", "xxoxx", "xooox"} {
			for i, ch := range row {
				switch ch {
				case 'x':
					put(p.back, x+i, y-9+d, p.tone(p.flame, 0.85))
				case 'o':
					put(p.back, x+i, y-9+d, p.tone(p.flameCore, 0.9))
				}
			}
		}
	}
}

// paintBackrest is the throne's back, standing behind the shell: a carved slab with a stepped
// crest and a serpent's head at each top corner.
func (p *splashArt) paintBackrest() {
	slab, edge, carve := p.tone(p.stone, 0.5), p.tone(p.stoneDark, 0.6), p.tone(p.stoneDark, 0.42)
	hw := p.seatHW - 1
	top := p.markY0 + p.ms*24/100
	block(p.back, p.cx-hw, top, p.cx+hw, p.seatY, slab)
	// The crest: three steps up to the middle.
	for i := range 3 {
		w := hw - 3 - i*4
		if w <= 0 {
			break
		}
		block(p.back, p.cx-w, top-2*(i+1), p.cx+w, top-2*i, slab)
		block(p.back, p.cx-w, top-2*(i+1), p.cx+w, top-2*(i+1)+1, edge)
	}
	// A border cut round the face, and stepped frets inside it.
	block(p.back, p.cx-hw, top, p.cx+hw, top+1, edge)
	block(p.back, p.cx-hw, top, p.cx-hw+1, p.seatY, edge)
	block(p.back, p.cx+hw-1, top, p.cx+hw, p.seatY, edge)
	block(p.back, p.cx-hw+2, top+2, p.cx-hw+3, p.seatY, carve)
	block(p.back, p.cx+hw-3, top+2, p.cx+hw-2, p.seatY, carve)
	for y := top + 3; y+3 < p.seatY; y += 5 {
		p.mirrored(p.back, hw-6, y, hw-4, y+1, carve)
		p.mirrored(p.back, hw-5, y+1, hw-4, y+3, carve)
	}
	// Serpent heads on the top corners, looking out.
	for _, side := range []int{-1, 1} {
		p.head(p.back, side, p.cx+side*hw, top, slab)
	}
	// The seat's top, which the shell sits on.
	block(p.back, p.cx-p.seatHW, p.seatY-2, p.cx+p.seatHW, p.seatY, p.tone(p.stone, 0.36))
}

// head is a blocky beast's head at x (its inner edge), facing away from the middle on side.
func (p *splashArt) head(img *image.RGBA, side, x, y int, c color.RGBA) {
	rows := []string{"x..x.", "xxxxx", "xxoxx", "xxxxx", ".x.x."}
	for d, row := range rows {
		for i, ch := range row {
			px := x + side*i
			if side < 0 {
				px--
			}
			switch ch {
			case 'x':
				put(img, px, y-3+d, c)
			case 'o':
				put(img, px, y-3+d, p.tone(p.flame, 0.9))
			}
		}
	}
}

// paintJungle is the jungle up close: treetops hanging over the top corners with creepers off them,
// a palm and undergrowth at each side, and leaves up from the bottom corners. It keeps to the outer
// columns, clear of the words.
func (p *splashArt) paintJungle() {
	c, lit := p.tone(p.leafDeep, 0.55), p.tone(p.leaf, 0.5)
	edge := max(p.w/7, 8)
	for _, side := range []int{-1, 1} {
		at := func(x float64) float64 {
			if side < 0 {
				return x
			}
			return float64(p.w-1) - x
		}
		// mirror turns an angle drawn for the left side into the same one on this side.
		mirror := func(ang float64) float64 {
			if side < 0 {
				return ang
			}
			return math.Pi - ang
		}
		p.palm(at(float64(edge)*0.55), float64(p.pyB+2), float64(p.h)*0.62, float64(-side), mirror)
		for x := 0; x < edge*3/2; x += 4 {
			r := 3 + noise(x, side+50)*3 + float64(edge*3/2-x)/float64(edge)*3
			p.blob(p.back, at(float64(x)), float64(p.pyB)-r*0.4, r, c, lit)
		}
		for x := -2; x < edge+edge/2; x += 3 {
			r := 3 + noise(x, side+20)*4 + float64(edge-min(x, edge))/3
			y := noise(x, side+30)*3 + float64(min(x, edge))/float64(edge)*2
			p.blob(p.back, at(float64(x)), y, r, c, lit)
		}
		// Creepers.
		for i, x := range []int{edge / 3, edge * 4 / 5, edge + edge/3} {
			n := 8 + int(noise(x, side+40)*float64(p.h/4))
			for d := 3; d < n; d++ {
				sway := int(math.Round(math.Sin(float64(d)/5+float64(i)) * 0.8))
				vx := int(at(float64(x))) + sway
				put(p.back, vx, d, c)
				if d%3 == 0 {
					put(p.back, vx+1-2*(d/3%2), d, lit)
				}
			}
		}
		// Leaves up from the bottom corner.
		for i, ang := range []float64{0.3, 0.7, 1.1} {
			p.frond(at(0), float64(p.h), mirror(ang), float64(p.h)/3.4-float64(i)*3, c, lit)
		}
	}
}

// palm is a palm tree rooted at (x, y), height tall, its trunk bowing toward lean (-1 left, 1
// right), its fronds hanging all round the top.
func (p *splashArt) palm(x, y, height, lean float64, mirror func(float64) float64) {
	bark, ring := p.tone(p.stoneDark, 0.42), p.tone(p.stoneDark, 0.56)
	c, lit := p.tone(p.leafDeep, 0.6), p.tone(p.leaf, 0.55)
	var tx, ty float64
	for d := 0.0; d <= height; d++ {
		t := d / height
		tx, ty = x+lean*t*t*height*0.12, y-d
		col := bark
		if int(d)%3 == 0 {
			col = ring
		}
		block(p.back, int(tx)-1, int(ty), int(tx)+1, int(ty)+1, col)
	}
	for i, ang := range []float64{0.15, 0.6, 1.2, 1.9, 2.5, 3.0} {
		p.frond(tx, ty, mirror(ang), height*(0.28+0.06*noise(i, 60)), c, lit)
	}
	block(p.back, int(tx)-2, int(ty)-1, int(tx)+2, int(ty)+2, c)
}

// frond is a palm leaf from (x, y), leaving at ang radians (counterclockwise from rightward) and
// bending down toward its tip.
func (p *splashArt) frond(x, y, ang, length float64, c, lit color.RGBA) {
	dx, dy := math.Cos(ang), -math.Sin(ang)
	for t := 0.0; t < 1; t += 0.5 / length {
		fx := x + dx*length*t
		fy := y + dy*length*t + t*t*length*0.4
		put(p.back, int(fx), int(fy), c)
		leaflet := (1 - t*0.7) * length * 0.2
		if int(t*length)%2 == 0 {
			for s := 1.0; s < leaflet; s++ {
				col := c
				if s < 2 {
					col = lit
				}
				put(p.back, int(fx+dy*s*0.6), int(fy-dx*s*0.6+s*0.6), col)
				put(p.back, int(fx-dy*s*0.6), int(fy+dx*s*0.6+s*0.6), col)
			}
		}
	}
}

// paintSeat is the front of the throne, under the mark's tail: the seat's carved face with a
// jaguar's head at each end, on two legs and a plinth.
func (p *splashArt) paintSeat() {
	f := p.back
	face, top, dark := p.tone(p.stone, 0.6), p.tone(p.stone, 0.4), p.tone(p.stoneDark, 0.72)
	carve := p.tone(p.stoneDark, 0.55)
	faceH := max(p.ms*12/100, 4)
	y0, y1 := p.seatY, p.seatY+faceH
	hw := p.seatHW
	block(f, p.cx-hw, y0-1, p.cx+hw, y0, top)
	block(f, p.cx-hw, y0, p.cx+hw, y1, face)
	block(f, p.cx-hw, y1-1, p.cx+hw, y1, dark)
	// The stepped fret along the face.
	fret := []string{"xxxx....", "...x.xx.", ".xxx..x.", "......xx"}
	for y := y0 + 1; y < y1-1 && y-y0-1 < len(fret); y++ {
		row := fret[y-y0-1]
		for x := p.cx - hw + 2; x < p.cx+hw-2; x++ {
			if row[((x-p.cx+hw)%8+8)%8] == 'x' {
				put(f, x, y, carve)
			}
		}
	}
	// Jaguars at the ends of the seat.
	for _, side := range []int{-1, 1} {
		p.head(f, side, p.cx+side*hw, y0+2, face)
	}
	// The legs, the shade between them, and the plinth.
	legW := max(hw/4, 3)
	plinth := p.baseY - 2
	block(f, p.cx-hw+2, y1, p.cx+hw-2, plinth, p.tone(p.stoneDark, 0.5))
	p.mirrored(f, hw-2-legW, y1, hw-2, plinth, face)
	p.mirrored(f, hw-3, y1, hw-2, plinth, dark)
	block(f, p.cx-hw-2, plinth, p.cx+hw+2, p.baseY, top)
	block(f, p.cx-hw-2, p.baseY-1, p.cx+hw+2, p.baseY, dark)
}

// blow enlarges the drawing to the screen's pixels, each one u square.
func (p *splashArt) blow() *image.RGBA {
	out := image.NewRGBA(image.Rect(0, 0, p.w*p.u, p.h*p.u))
	xdraw.NearestNeighbor.Scale(out, out.Rect, p.back, p.back.Rect, draw.Src, nil)
	return out
}
