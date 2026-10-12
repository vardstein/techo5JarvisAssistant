package jpegenc

import (
	"image"
	"image/color"
	"testing"
)

func TestToNV12(t *testing.T) {
	cases := []struct {
		c         color.RGBA
		y, cb, cr byte
	}{
		{color.RGBA{0, 0, 0, 255}, 0, 128, 128},
		{color.RGBA{255, 255, 255, 255}, 255, 128, 128},
		{color.RGBA{255, 0, 0, 255}, 77, 85, 255},
		{color.RGBA{0, 0, 255, 255}, 29, 255, 107},
		{color.RGBA{0, 255, 0, 255}, 149, 43, 21},
	}
	for _, tc := range cases {
		// An odd offset inside a bigger picture, so the stride and origin are exercised as well.
		big := image.NewRGBA(image.Rect(0, 0, 10, 8))
		img := big.SubImage(image.Rect(2, 2, 6, 6)).(*image.RGBA)
		for y := 2; y < 6; y++ {
			for x := 2; x < 6; x++ {
				img.SetRGBA(x, y, tc.c)
			}
		}
		const stride = 16
		buf := make([]byte, stride*4+stride*2)
		toNV12(img, buf[:stride*4], buf[stride*4:], stride)
		for y := 0; y < 4; y++ {
			for x := 0; x < 4; x++ {
				if got := buf[y*stride+x]; got != tc.y {
					t.Fatalf("%v: luma at %d,%d is %d, want %d", tc.c, x, y, got, tc.y)
				}
			}
		}
		uv := buf[stride*4:]
		for y := 0; y < 2; y++ {
			for x := 0; x < 4; x += 2 {
				if cb, cr := uv[y*stride+x], uv[y*stride+x+1]; cb != tc.cb || cr != tc.cr {
					t.Fatalf("%v: chroma %d,%d, want %d,%d", tc.c, cb, cr, tc.cb, tc.cr)
				}
			}
		}
	}
}

func TestQualityCode(t *testing.T) {
	for q, want := range map[int]uint32{75: 0x1, 80: 0x1, 84: 0x6, 85: 0x6, 87: 0xB, 90: 0x2, 95: 0x2} {
		if got := qualityCode(q); got != want {
			t.Errorf("quality %d: code %#x, want %#x", q, got, want)
		}
	}
}

func BenchmarkToNV12(b *testing.B) {
	img := image.NewRGBA(image.Rect(0, 0, 640, 360))
	for i := range img.Pix {
		img.Pix[i] = byte(i * 7)
	}
	buf := make([]byte, 640*368*3/2+4096)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		toNV12(img, buf[:640*368], buf[ceil(640*368, 4096):], 640)
	}
}
