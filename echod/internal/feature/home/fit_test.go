package home

import (
	"image"
	"image/color"
	"math/rand"
	"testing"

	xdraw "golang.org/x/image/draw"
)

func TestFitSizeKeepsTheShape(t *testing.T) {
	for _, c := range []struct{ sw, sh, mw, mh, w, h int }{
		{640, 360, 960, 480, 853, 480},
		{1280, 720, 960, 480, 853, 480},
		{3840, 2160, 1280, 800, 1280, 720},
		{480, 640, 640, 480, 360, 480},
		{4000, 1, 960, 480, 960, 1}, // never squeezed to nothing
		{1, 4000, 960, 480, 1, 480},
	} {
		if w, h := fitSize(c.sw, c.sh, c.mw, c.mh); w != c.w || h != c.h {
			t.Errorf("fitSize(%d×%d in %d×%d) = %d×%d, want %d×%d", c.sw, c.sh, c.mw, c.mh, w, h, c.w, c.h)
		}
	}
}

func TestTapsStayInsideTheSource(t *testing.T) {
	for _, c := range [][2]int{{1, 1}, {1, 7}, {7, 1}, {2, 3}, {360, 480}, {720, 480}, {1280, 1280}, {40000, 3}} {
		n, m := c[0], c[1]
		tp := tapsFor(n, m)
		for i := 0; i < m; i++ {
			if tp.a[i] < 0 || tp.b[i] >= n || tp.b[i] < tp.a[i] || tp.b[i] > tp.a[i]+1 || tp.w[i] < 0 || tp.w[i] > 255 {
				t.Fatalf("tapsFor(%d, %d)[%d] = %d, %d, %d", n, m, i, tp.a[i], tp.b[i], tp.w[i])
			}
		}
	}
	// Same size: every pixel on its own sample.
	tp := tapsFor(5, 5)
	for i := 0; i < 5; i++ {
		if tp.a[i] != i || tp.w[i] != 0 {
			t.Fatalf("tapsFor(5, 5)[%d] = %d, %d", i, tp.a[i], tp.w[i])
		}
	}
}

// photo is something like a camera picture: smooth with some noise, where a scaler's mistakes show.
func photo(r image.Rectangle, ratio image.YCbCrSubsampleRatio) *image.YCbCr {
	rnd := rand.New(rand.NewSource(1))
	m := image.NewYCbCr(r, ratio)
	for y := r.Min.Y; y < r.Max.Y; y++ {
		for x := r.Min.X; x < r.Max.X; x++ {
			m.Y[m.YOffset(x, y)] = uint8((x*3+y*2)%200 + rnd.Intn(40))
			m.Cb[m.COffset(x, y)] = uint8(90 + (x/7)%80)
			m.Cr[m.COffset(x, y)] = uint8(100 + (y/5)%70)
		}
	}
	return m
}

// meanDiff is the mean difference per channel between two pictures of the same size, and the largest.
func meanDiff(a, b *image.RGBA) (mean float64, most int) {
	sum := 0
	for i := range a.Pix {
		d := int(a.Pix[i]) - int(b.Pix[i])
		if d < 0 {
			d = -d
		}
		sum += d
		most = max(most, d)
	}
	return float64(sum) / float64(len(a.Pix)), most
}

func TestScaleMatchesXImage(t *testing.T) {
	rgba := image.NewRGBA(image.Rect(0, 0, 160, 90))
	ycc := photo(rgba.Rect, image.YCbCrSubsampleRatio444)
	for y := 0; y < 90; y++ {
		for x := 0; x < 160; x++ {
			rgba.Set(x, y, ycc.At(x, y))
		}
	}
	for _, c := range []struct {
		name string
		src  image.Image
		w, h int
		// Colour is taken from the nearest sample, not blended, so it may stray further at an edge.
		mean float64
		most int
	}{
		{"420 up", photo(image.Rect(0, 0, 160, 90), image.YCbCrSubsampleRatio420), 213, 120, 1.5, 40},
		{"420 down", photo(image.Rect(0, 0, 320, 180), image.YCbCrSubsampleRatio420), 213, 120, 1.5, 40},
		{"420 cut out", photo(image.Rect(0, 0, 330, 190), image.YCbCrSubsampleRatio420).SubImage(image.Rect(5, 7, 325, 187)), 213, 120, 1.5, 40},
		{"422", photo(image.Rect(0, 0, 160, 90), image.YCbCrSubsampleRatio422), 213, 120, 1.5, 40},
		{"440", photo(image.Rect(0, 0, 160, 90), image.YCbCrSubsampleRatio440), 213, 120, 1.5, 40},
		{"444", ycc, 213, 120, 1.5, 40},
		{"rgba", rgba, 213, 120, 0.5, 2},
		{"rgba cut out", rgba.SubImage(image.Rect(10, 10, 150, 80)), 100, 50, 0.5, 2},
	} {
		t.Run(c.name, func(t *testing.T) {
			want := image.NewRGBA(image.Rect(0, 0, c.w, c.h))
			xdraw.ApproxBiLinear.Scale(want, want.Rect, c.src, c.src.Bounds(), xdraw.Src, nil)
			got := image.NewRGBA(want.Rect)
			scaleInto(got, c.src)
			if mean, most := meanDiff(got, want); mean > c.mean || most > c.most {
				t.Fatalf("differs from x/image by %.2f on average, %d at most", mean, most)
			}
		})
	}
}

func TestScaleSameSizeIsACopy(t *testing.T) {
	src := photo(image.Rect(0, 0, 64, 36), image.YCbCrSubsampleRatio420)
	got := image.NewRGBA(src.Rect)
	scaleInto(got, src)
	for _, p := range []image.Point{{0, 0}, {63, 35}, {17, 9}} {
		if got.At(p.X, p.Y) != color.RGBAModel.Convert(src.At(p.X, p.Y)) {
			t.Fatalf("pixel %v = %v, want %v", p, got.At(p.X, p.Y), src.At(p.X, p.Y))
		}
	}
}

func TestFitTakesAnyPicture(t *testing.T) {
	// A grey PNG has a scaler of x/image's, not ours; it still comes out the panel's shape.
	g := image.NewGray(image.Rect(0, 0, 400, 300))
	if b := fit(g, 960, 480).Bounds(); b.Dx() != 640 || b.Dy() != 480 {
		t.Fatalf("fit = %v", b)
	}
}

func BenchmarkScaleYCbCr(b *testing.B) {
	src := photo(image.Rect(0, 0, 640, 360), image.YCbCrSubsampleRatio420)
	dst := image.NewRGBA(image.Rect(0, 0, 853, 480))
	for b.Loop() {
		scaleInto(dst, src)
	}
}
