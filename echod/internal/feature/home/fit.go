package home

import (
	"image"
	"image/draw"

	xdraw "golang.org/x/image/draw"
)

// A camera frame has to be scaled to the panel every time one comes in, and on the Show's cores
// x/image's bilinear scaler takes about 150 ms for a 960×480 panel, whatever size the frame was
// (GENBU-116), against 45 ms for the one below. That alone held the view to a few frames a second. The
// frames are nearly always JPEGs, which decode to YCbCr, or the sensor's own RGBA, so those two get a
// scaler of their own here: the same two-by-two bilinear sampling in fixed point, with the tables
// worked out once per frame instead of per pixel. In YCbCr only the brightness is blended; the colour
// is taken from the nearest sample, which is what the subsampled colour of a JPEG holds anyway.
// Anything else goes through x/image as before.

// fitSize is the largest size with src's shape that fits in maxW×maxH.
func fitSize(sw, sh, maxW, maxH int) (w, h int) {
	w, h = maxW, sh*maxW/sw
	if h > maxH {
		w, h = sw*maxH/sh, maxH
	}
	return max(w, 1), max(h, 1)
}

// fit scales src to fit maxW×maxH, keeping its shape.
func fit(src image.Image, maxW, maxH int) *image.RGBA {
	b := src.Bounds()
	w, h := fitSize(b.Dx(), b.Dy(), maxW, maxH)
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	scaleInto(dst, src)
	return dst
}

// scaleInto scales src to fill dst.
func scaleInto(dst *image.RGBA, src image.Image) {
	if dst.Bounds().Size() == src.Bounds().Size() {
		// Nothing to scale: image/draw converts a JPEG's colours more than twice as fast as the
		// sampling below does with weights that come to nothing.
		draw.Draw(dst, dst.Bounds(), src, src.Bounds().Min, draw.Src)
		return
	}
	switch s := src.(type) {
	case *image.YCbCr:
		if hs, vs, ok := chromaShift(s.SubsampleRatio); ok {
			scaleYCbCr(dst, s, hs, vs)
			return
		}
	case *image.RGBA:
		scaleRGBA(dst, s)
		return
	}
	xdraw.ApproxBiLinear.Scale(dst, dst.Bounds(), src, src.Bounds(), xdraw.Src, nil)
}

// chromaShift is how many times the colour planes are halved across and down.
func chromaShift(r image.YCbCrSubsampleRatio) (hs, vs uint, ok bool) {
	switch r {
	case image.YCbCrSubsampleRatio444:
		return 0, 0, true
	case image.YCbCrSubsampleRatio422:
		return 1, 0, true
	case image.YCbCrSubsampleRatio420:
		return 1, 1, true
	case image.YCbCrSubsampleRatio440:
		return 0, 1, true
	}
	return 0, 0, false
}

// taps is, for each pixel along one side of dst, the two source samples it lies between (as offsets
// from the start of the source) and how far towards the second it is, out of 256.
type taps struct {
	a, b []int
	w    []int32
}

// tapsFor samples n source pixels for m destination pixels, centre to centre, as ApproxBiLinear does.
func tapsFor(n, m int) taps {
	t := taps{a: make([]int, m), b: make([]int, m), w: make([]int32, m)}
	for i := 0; i < m; i++ {
		// The centre of destination pixel i in source pixels, times 256; in 64 bits for the Spot's
		// 32-bit ints.
		p := int((int64(2*i+1)*int64(n)*256/int64(m) - 256) / 2)
		if p < 0 {
			p = 0
		}
		a := p >> 8
		if a >= n-1 {
			t.a[i], t.b[i], t.w[i] = n-1, n-1, 0
			continue
		}
		t.a[i], t.b[i], t.w[i] = a, a+1, int32(p&255)
	}
	return t
}

func scaleYCbCr(dst *image.RGBA, src *image.YCbCr, hs, vs uint) {
	sb, db := src.Rect, dst.Bounds()
	dw, dh := db.Dx(), db.Dy()
	if dw <= 0 || dh <= 0 || sb.Empty() {
		return
	}
	xs, ys := tapsFor(sb.Dx(), dw), tapsFor(sb.Dy(), dh)
	// The colour column of each destination column: the nearest source pixel's, in the colour plane.
	cx := make([]int, dw)
	for i := range cx {
		x := xs.a[i]
		if xs.w[i] >= 128 {
			x = xs.b[i]
		}
		cx[i] = (sb.Min.X+x)>>hs - sb.Min.X>>hs
	}
	for j := 0; j < dh; j++ {
		y0 := src.Y[(ys.a[j])*src.YStride:]
		y1 := src.Y[(ys.b[j])*src.YStride:]
		wy := ys.w[j]
		yc := ys.a[j]
		if wy >= 128 {
			yc = ys.b[j]
		}
		crow := ((sb.Min.Y+yc)>>vs - sb.Min.Y>>vs) * src.CStride
		cb, cr := src.Cb[crow:], src.Cr[crow:]
		out := dst.Pix[(db.Min.Y+j-dst.Rect.Min.Y)*dst.Stride+(db.Min.X-dst.Rect.Min.X)*4:]
		for i := 0; i < dw; i++ {
			a, b, wx := xs.a[i], xs.b[i], xs.w[i]
			top := int32(y0[a])*(256-wx) + int32(y0[b])*wx
			bot := int32(y1[a])*(256-wx) + int32(y1[b])*wx
			yy := (top*(256-wy) + bot*wy + 1<<15) >> 16
			r, g, bl := ycbcrToRGB(yy, int32(cb[cx[i]]), int32(cr[cx[i]]))
			o := out[i*4 : i*4+4 : i*4+4]
			o[0], o[1], o[2], o[3] = r, g, bl, 0xff
		}
	}
}

// ycbcrToRGB is image/color's YCbCrToRGB, taking the brightness already blended.
func ycbcrToRGB(y, cb, cr int32) (uint8, uint8, uint8) {
	yy1 := y * 0x10101
	cb1 := cb - 128
	cr1 := cr - 128
	r := yy1 + 91881*cr1
	if uint32(r)&0xff000000 == 0 {
		r >>= 16
	} else {
		r = ^(r >> 31)
	}
	g := yy1 - 22554*cb1 - 46802*cr1
	if uint32(g)&0xff000000 == 0 {
		g >>= 16
	} else {
		g = ^(g >> 31)
	}
	b := yy1 + 116130*cb1
	if uint32(b)&0xff000000 == 0 {
		b >>= 16
	} else {
		b = ^(b >> 31)
	}
	return uint8(r), uint8(g), uint8(b)
}

func scaleRGBA(dst, src *image.RGBA) {
	sb, db := src.Rect, dst.Bounds()
	dw, dh := db.Dx(), db.Dy()
	if dw <= 0 || dh <= 0 || sb.Empty() {
		return
	}
	xs, ys := tapsFor(sb.Dx(), dw), tapsFor(sb.Dy(), dh)
	for j := 0; j < dh; j++ {
		r0 := src.Pix[ys.a[j]*src.Stride:]
		r1 := src.Pix[ys.b[j]*src.Stride:]
		wy := ys.w[j]
		out := dst.Pix[(db.Min.Y+j-dst.Rect.Min.Y)*dst.Stride+(db.Min.X-dst.Rect.Min.X)*4:]
		for i := 0; i < dw; i++ {
			a, b, wx := xs.a[i]*4, xs.b[i]*4, xs.w[i]
			o := out[i*4 : i*4+4 : i*4+4]
			for c := 0; c < 4; c++ {
				top := int32(r0[a+c])*(256-wx) + int32(r0[b+c])*wx
				bot := int32(r1[a+c])*(256-wx) + int32(r1[b+c])*wx
				o[c] = uint8((top*(256-wy) + bot*wy + 1<<15) >> 16)
			}
		}
	}
}
