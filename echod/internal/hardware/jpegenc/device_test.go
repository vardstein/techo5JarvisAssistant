//go:build !dot && !spot

package jpegenc

import (
	"bytes"
	"image"
	"image/jpeg"
	"testing"
	"time"
)

// TestEncodeOnDevice runs only where the encoder is: it encodes a test picture a few hundred times
// and checks that what comes back decodes to the same colors.
func TestEncodeOnDevice(t *testing.T) {
	if !Available() {
		t.Skip("no hardware encoder here")
	}
	for _, size := range []image.Point{{640, 360}, {1280, 720}} {
		img := image.NewRGBA(image.Rect(0, 0, size.X, size.Y))
		for y := 0; y < size.Y; y++ {
			for x := 0; x < size.X; x++ {
				i := img.PixOffset(x, y)
				switch x * 3 / size.X {
				case 0:
					img.Pix[i] = 255
				case 1:
					img.Pix[i+1] = 255
				default:
					img.Pix[i+2] = 255
				}
				img.Pix[i+3] = 255
			}
		}
		var jpg []byte
		start := time.Now()
		const n = 300
		for i := 0; i < n; i++ {
			var err error
			if jpg, err = Encode(img, 85); err != nil {
				t.Fatalf("%v: encode %d: %v", size, i, err)
			}
		}
		t.Logf("%v: %v a picture with the conversion, %d bytes", size, time.Since(start)/n, len(jpg))
		dec, err := jpeg.Decode(bytes.NewReader(jpg))
		if err != nil {
			t.Fatalf("%v: decode: %v", size, err)
		}
		if dec.Bounds().Size() != size {
			t.Fatalf("decoded %v, want %v", dec.Bounds().Size(), size)
		}
		for band, want := range [][3]uint32{{255, 0, 0}, {0, 255, 0}, {0, 0, 255}} {
			r, g, b, _ := dec.At(size.X*band/3+size.X/6, size.Y/2).RGBA()
			got := [3]uint32{r >> 8, g >> 8, b >> 8}
			for c := range got {
				if d := int(got[c]) - int(want[c]); d < -8 || d > 8 {
					t.Fatalf("%v band %d: decoded %v, want %v", size, band, got, want)
				}
			}
		}
	}
}
