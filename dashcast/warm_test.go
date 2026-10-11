package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

// A parked tab that is taken back keeps drawing. Parking used to freeze the page as well, and
// Chromium 152 sends one frame of a thawed page and then nothing: the Show that came back to it
// showed the moment it reconnected, clock and all, for good.
func TestATakenBackTabKeepsDrawing(t *testing.T) {
	required := os.Getenv("DASHCAST_REQUIRE_BROWSER") != ""
	noBrowser := func(why string, args ...any) {
		t.Helper()
		if required {
			t.Fatalf(why, args...)
		}
		t.Skipf(why, args...)
	}
	if chromePath("") == "" {
		noBrowser("no Chrome or headless-shell to run")
	}
	ha := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte(`<!doctype html><title>clock</title><p id=c></p><script>
			(function tick() { c.textContent = performance.now(); requestAnimationFrame(tick) })()
		</script>`))
	}))
	defer ha.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	b, err := newBrowser(ctx, config{ha: ha.URL})
	if err != nil {
		noBrowser("the browser did not start: %v", err)
	}
	defer b.close()

	tab, closeTab, err := b.open(ctx, "", "/lovelace/0", 480, 480, map[string]bool{"lovelace": true}, false, false)
	if err != nil {
		t.Fatal(err)
	}
	pool := &warmPool{}
	w := newWarmTab(tab, closeTab, "screen")
	defer w.close()

	frames := func(within time.Duration) int64 {
		t.Helper()
		var n atomic.Int64
		w.watch(func(f *page.EventScreencastFrame) {
			n.Add(1)
			go func() { _ = chromedp.Run(w.ctx, page.ScreencastFrameAck(f.SessionID)) }()
		})
		if err := chromedp.Run(w.ctx, page.StartScreencast().WithFormat(page.ScreencastFormatPng)); err != nil {
			t.Fatal(err)
		}
		time.Sleep(within)
		return n.Load()
	}

	// Chromium's own headless keeps a tab that is not in front hidden, and a hidden tab draws
	// nothing; the image's headless-shell draws every tab. In front, it is the same in both.
	if err := chromedp.Run(w.ctx, page.BringToFront()); err != nil {
		t.Fatal(err)
	}
	if n := frames(2 * time.Second); n < 3 {
		t.Fatalf("a new tab sent %d frames in 2s", n)
	}
	pool.park(w)
	time.Sleep(500 * time.Millisecond)
	if pool.take("screen") != w {
		t.Fatal("the parked tab was not given back")
	}
	if n := frames(2 * time.Second); n < 3 {
		t.Errorf("the tab taken back sent %d frames in 2s", n)
	}
}

func TestWarmFrom(t *testing.T) {
	for v, want := range map[string]time.Duration{
		"2h": 2 * time.Hour, " 10m ": 10 * time.Minute, "": 30 * time.Minute,
		"soon": 30 * time.Minute, "-5m": 30 * time.Minute, "0": 30 * time.Minute,
	} {
		if got := warmFrom(v); got != want {
			t.Errorf("warmFrom(%q) = %v, want %v", v, got, want)
		}
	}
}
