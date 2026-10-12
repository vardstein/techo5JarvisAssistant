package home

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestTheShelfGetsStationLogos(t *testing.T) {
	// A wide logo, red on clear, as Radio Browser's pictures often are.
	wide := image.NewRGBA(image.Rect(0, 0, 400, 200))
	for i := 0; i < len(wide.Pix); i += 4 {
		wide.Pix[i], wide.Pix[i+3] = 0xff, 0xff
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, wide); err != nil {
		t.Fatal(err)
	}
	var asked atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked.Add(1)
		if r.URL.Path != "/logo.png" {
			http.NotFound(w, r)
			return
		}
		w.Write(buf.Bytes())
	}))
	defer srv.Close()

	f := &Feature{}
	f.lists.local.stations = []station{
		{Name: "Has a logo", Thumb: srv.URL + "/logo.png"},
		{Name: "Logo gone", Thumb: srv.URL + "/gone.png"},
		{Name: "Typed in wrong", Thumb: "javascript:alert(1)"},
	}
	names := []string{"Has a logo", "Logo gone", "Typed in wrong"}
	if got := f.StationLogos(names); len(got) != 0 {
		t.Fatalf("logos before any was fetched: %v", got)
	}
	var got map[string]*image.RGBA
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end); time.Sleep(10 * time.Millisecond) {
		f.mu.Lock()
		idle := f.logos.running == 0
		f.mu.Unlock()
		if idle {
			got = f.StationLogos(names)
			break
		}
	}
	logo := got["Has a logo"]
	if logo == nil || len(got) != 1 {
		t.Fatalf("logos = %v, want the one station's", got)
	}
	if b := logo.Bounds(); b.Dx() != logoSide || b.Dy() != logoSide {
		t.Errorf("logo is %v, want %d square", b, logoSide)
	}
	// Fitted, not cropped: clear above it, red in the middle.
	if a := logo.RGBAAt(logoSide/2, 2); a.A != 0 {
		t.Errorf("above the wide logo is %v, want clear", a)
	}
	if c := logo.RGBAAt(logoSide/2, logoSide/2); c != (color.RGBA{0xff, 0, 0, 0xff}) {
		t.Errorf("the middle is %v, want the logo's red", c)
	}
	// One fetch each for the two web addresses; the one that failed is not asked again every frame.
	if n := asked.Load(); n != 2 {
		t.Errorf("server asked %d times, want 2", n)
	}
	f.StationLogos(names)
	time.Sleep(50 * time.Millisecond)
	if n := asked.Load(); n != 2 {
		t.Errorf("server asked %d times after the shelf was drawn again, want still 2", n)
	}
}

func TestOnlyWebAndHomeAssistantLogosAreFetched(t *testing.T) {
	for u, want := range map[string]bool{
		"https://example.org/logo.png":   true,
		"http://example.org/favicon.ico": true,
		"/api/brands/radio_browser/logo": true,
		"//example.org/x.png":            false,
		"file:///etc/passwd":             false,
		"data:image/png;base64,AAAA":     false,
		"":                               false,
		"logo.png":                       false,
	} {
		if got := fetchable(u); got != want {
			t.Errorf("fetchable(%q) = %v, want %v", u, got, want)
		}
	}
}

func TestASmallLogoIsKeptAtItsOwnSize(t *testing.T) {
	small := image.NewRGBA(image.Rect(0, 0, 64, 48))
	got := logoThumb(small)
	if b := got.Bounds(); b.Dx() != 64 || b.Dy() != 64 {
		t.Errorf("a 64×48 logo is kept at %v, want 64 square", b)
	}
}
