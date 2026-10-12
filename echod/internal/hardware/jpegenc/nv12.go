package jpegenc

import "image"

func ceil(v, a int) int { return (v + a - 1) / a * a }

// toNV12 writes img as NV12: full-range BT.601, what a JFIF decoder assumes. Luma rows are stride
// bytes apart, and the chroma plane is interleaved Cb Cr at half the size each way, each pair the
// average of a 2x2 block. Width and height are even.
func toNV12(img *image.RGBA, y, uv []byte, stride int) {
	w, h := img.Rect.Dx(), img.Rect.Dy()
	base := img.PixOffset(img.Rect.Min.X, img.Rect.Min.Y)
	for row := 0; row < h; row += 2 {
		p0 := img.Pix[base+row*img.Stride : base+row*img.Stride+w*4]
		p1 := img.Pix[base+(row+1)*img.Stride : base+(row+1)*img.Stride+w*4]
		y0 := y[row*stride : row*stride+w]
		y1 := y[(row+1)*stride : (row+1)*stride+w]
		c := uv[(row/2)*stride : (row/2)*stride+w]
		for x := 0; x+1 < w; x += 2 {
			i := x * 4
			a, b := p0[i:i+8:i+8], p1[i:i+8:i+8]
			r0, g0, b0 := int(a[0]), int(a[1]), int(a[2])
			r1, g1, b1 := int(a[4]), int(a[5]), int(a[6])
			r2, g2, b2 := int(b[0]), int(b[1]), int(b[2])
			r3, g3, b3 := int(b[4]), int(b[5]), int(b[6])
			// The weights add up to 256, so luma cannot leave 0..255.
			y0[x] = byte((77*r0 + 150*g0 + 29*b0 + 128) >> 8)
			y0[x+1] = byte((77*r1 + 150*g1 + 29*b1 + 128) >> 8)
			y1[x] = byte((77*r2 + 150*g2 + 29*b2 + 128) >> 8)
			y1[x+1] = byte((77*r3 + 150*g3 + 29*b3 + 128) >> 8)
			r, g, bl := r0+r1+r2+r3, g0+g1+g2+g3, b0+b1+b2+b3
			c[x] = clamp(((-43*r - 85*g + 128*bl + 512) >> 10) + 128)
			c[x+1] = clamp(((128*r - 107*g - 21*bl + 512) >> 10) + 128)
		}
	}
}

func clamp(v int) byte {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return byte(v)
}

// qualityCode picks the encoder's nearest step: it has Q80, Q84, Q87 and Q90.
func qualityCode(q int) uint32 {
	switch {
	case q < 82:
		return 0x1
	case q < 86:
		return 0x6
	case q < 89:
		return 0xB
	}
	return 0x2
}
