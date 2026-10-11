package main

import (
	"image"
	"image/color"
	"testing"
	"time"
)

func frame(w, h int) *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for i := range img.Pix {
		img.Pix[i] = 40
	}
	return img
}

// The first frame goes whole; an unchanged one sends nothing; a change sends only its own tiles.
func TestChangesAreOnlyWhatChanged(t *testing.T) {
	d := &differ{}
	a := frame(320, 160)
	if got := d.changes(a); len(got) != 1 || got[0].img.Bounds().Dx() != 320 {
		t.Fatalf("first frame: %d patches, want the whole frame", len(got))
	}
	if got := d.changes(frame(320, 160)); len(got) != 0 {
		t.Fatalf("an unchanged frame sent %d patches", len(got))
	}
	b := frame(320, 160)
	b.Set(5, 5, color.RGBA{255, 0, 0, 255})
	got := d.changes(b)
	if len(got) != 1 {
		t.Fatalf("one changed pixel: %d patches", len(got))
	}
	if r := got[0].img.Bounds(); r != image.Rect(0, 0, tile, tile) {
		t.Errorf("one changed pixel sent %v, want its tile", r)
	}
}

// Changes far apart go as separate pictures, near ones as one.
func TestChangesFarApartStayApart(t *testing.T) {
	d := &differ{}
	d.changes(frame(320, 160))
	b := frame(320, 160)
	b.Set(2, 2, color.RGBA{255, 0, 0, 255})
	b.Set(300, 150, color.RGBA{255, 0, 0, 255})
	b.Set(20, 2, color.RGBA{255, 0, 0, 255})
	if got := d.changes(b); len(got) != 2 {
		t.Fatalf("changes at two corners: %d patches, want 2", len(got))
	}
}

func TestShrinkAverages(t *testing.T) {
	img := image.NewRGBA(image.Rect(0, 0, 2, 2))
	img.Set(0, 0, color.RGBA{0, 0, 0, 255})
	img.Set(1, 0, color.RGBA{100, 0, 0, 255})
	img.Set(0, 1, color.RGBA{100, 0, 0, 255})
	img.Set(1, 1, color.RGBA{200, 0, 0, 255})
	out := shrink(img)
	if out.Bounds().Dx() != 1 || out.RGBAAt(0, 0).R != 100 {
		t.Errorf("shrink = %v %v, want one pixel of 100", out.Bounds(), out.RGBAAt(0, 0))
	}
}

// A path is its panel, and nothing that could walk anywhere else.
func TestFirstPart(t *testing.T) {
	for path, want := range map[string]string{
		"/lovelace/0": "lovelace", "/home-refresh/lights": "home-refresh", "energy": "energy",
		"/config/dashboard": "config", "/../config": "", "//evil.example/x": "", "/": "", `\config`: "",
		"/lovelace/%2e%2e/config": "", "/lovelace/.	./config": "", "/lovelace/./x": "", "/lights?edit=1": "",
		"/lovelace/ 0": "", "": "",
	} {
		got, ok := firstPart(path)
		if got != want || ok != (want != "") {
			t.Errorf("firstPart(%q) = %q %v, want %q", path, got, ok, want)
		}
	}
}

// Home Assistant's origin is written as a browser writes location.origin.
func TestHAOrigin(t *testing.T) {
	for ha, want := range map[string]string{
		"http://192.0.2.10:8123":            "http://192.0.2.10:8123",
		"http://HA.Local:8123/":             "http://ha.local:8123",
		"https://ha.example.com:443":        "https://ha.example.com",
		"http://ha.example.com:80/lovelace": "http://ha.example.com",
		"HTTPS://Ha.Example.com":            "https://ha.example.com",
		"http://[2001:db8::1]:8123":         "http://[2001:db8::1]:8123",
	} {
		if got := haOrigin(ha); got != want {
			t.Errorf("haOrigin(%q) = %q, want %q", ha, got, want)
		}
	}
}

// A frame that changed something asks for the next at once; each unchanged one waits longer, up to a
// second.
func TestPace(t *testing.T) {
	d := &differ{}
	if got := d.pace(true); got != busyPace {
		t.Errorf("after a change: %v", got)
	}
	var last time.Duration
	for i := 0; i < 20; i++ {
		got := d.pace(false)
		if got < last || got > idlePace {
			t.Fatalf("unchanged frame %d: %v after %v", i, got, last)
		}
		last = got
	}
	if last != idlePace {
		t.Errorf("a long still page waits %v, want %v", last, idlePace)
	}
	if got := d.pace(true); got != busyPace {
		t.Errorf("a change after a still spell: %v", got)
	}
	if !(&differ{lastRaw: []byte("x")}).same([]byte("x")) {
		t.Error("the same bytes were not the same")
	}
}

func TestJarvisShowApplianceProfiles(t *testing.T) {
	tests := []struct {
		board string
		w     int
		h     int
	}{
		{board: "crown", w: 1280, h: 800},
		{board: "checkers", w: 960, h: 480},
	}
	for _, tc := range tests {
		t.Run(tc.board, func(t *testing.T) {
			h := hello{Name: "device", W: 13, H: 17, Path: "/wrong", Kiosk: false}
			if !applyApplianceProfile(&h, tc.board) {
				t.Fatalf("profile %q rejected", tc.board)
			}
			if h.W != tc.w || h.H != tc.h || h.Path != "/jarvis-display" || !h.Kiosk {
				t.Fatalf("profile %q = %+v, want %dx%d /jarvis-display kiosk", tc.board, h, tc.w, tc.h)
			}
		})
	}
}

func TestGenericDashcastHelloIsUnchanged(t *testing.T) {
	h := hello{Name: "generic", W: 1024, H: 600, Path: "/lovelace/test", Kiosk: false}
	before := h
	if !applyApplianceProfile(&h, "") {
		t.Fatal("generic mode rejected")
	}
	if h != before {
		t.Fatalf("generic hello changed: got %+v want %+v", h, before)
	}
}

func TestUnknownJarvisShowBoardFailsClosed(t *testing.T) {
	h := hello{W: 1, H: 1, Path: "/bad"}
	if applyApplianceProfile(&h, "cronos") {
		t.Fatal("unsupported board accepted")
	}
}

func TestLostNudgesThenReloads(t *testing.T) {
	var l lost
	got := map[int]remedy{}
	for s := 1; s <= 61; s++ {
		if r := l.next(true); r != wait {
			got[s] = r
		}
	}
	want := map[int]remedy{nudgeAfter: nudge, reloadAfter: reload, reloadAfter + nudgeAfter: nudge, 2 * reloadAfter: reload}
	if len(got) != len(want) {
		t.Fatalf("remedies %v, want %v", got, want)
	}
	for s, r := range want {
		if got[s] != r {
			t.Fatalf("second %d: %v, want %v (all %v)", s, got[s], r, got)
		}
	}
	// Back in touch starts the count again.
	l.next(true)
	if l.next(false) != wait || l.next(true) != wait || l.next(true) != wait || l.next(true) != nudge {
		t.Fatal("a reconnect did not start the count again")
	}
}

// A touch on a still page has the frame Chrome is waiting on go at once, not after the idle second,
// and the frames after it at the busy pace.
func TestATouchWakesAStillPage(t *testing.T) {
	d := &differ{}
	for i := 0; i < 20; i++ {
		d.pace(false)
	}
	done := make(chan time.Duration)
	go func() {
		start := time.Now()
		d.wait(false)
		done <- time.Since(start)
	}()
	time.Sleep(50 * time.Millisecond)
	d.touched()
	select {
	case took := <-done:
		if took > idlePace/2 {
			t.Errorf("the wait after a touch took %v", took)
		}
	case <-time.After(2 * idlePace):
		t.Fatal("a touch did not end the wait")
	}
	if got := d.pace(false); got != busyPace<<1 {
		t.Errorf("after a touch the next still frame waits %v, want %v", got, busyPace<<1)
	}
	// A touch with no frame waiting is not lost: the next wait is short.
	d.touched()
	start := time.Now()
	d.wait(false)
	if took := time.Since(start); took > idlePace/2 {
		t.Errorf("a wait after an earlier touch took %v", took)
	}
}
