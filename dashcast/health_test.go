package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

func TestWatchBrowserStopsWhenChromeIsGone(t *testing.T) {
	gone := make(chan struct{})
	close(gone)
	err := watchBrowser(context.Background(), gone, func(context.Context) error { return nil }, time.Hour, 3)
	if err == nil {
		t.Fatal("Chrome gone, and the watch carried on")
	}
}

func TestWatchBrowserStopsAfterMissesInARow(t *testing.T) {
	var asked atomic.Int32
	err := watchBrowser(context.Background(), nil, func(context.Context) error {
		asked.Add(1)
		return errors.New("no answer")
	}, time.Millisecond, 3)
	if err == nil {
		t.Fatal("Chrome never answered, and the watch carried on")
	}
	if n := asked.Load(); n != 3 {
		t.Fatalf("asked %d times before giving up, want 3", n)
	}
}

func TestWatchBrowserForgivesAMissThatIsAnswered(t *testing.T) {
	// No answer twice, an answer, no answer twice: never three in a row, so Chrome is fine. The
	// watch is then ended from outside and says nothing is wrong.
	answers := []bool{false, false, true, false, false, true}
	ctx, cancel := context.WithCancel(context.Background())
	var i atomic.Int32
	err := watchBrowser(ctx, nil, func(context.Context) error {
		n := int(i.Add(1)) - 1
		if n >= len(answers) {
			cancel()
			return nil
		}
		if answers[n] {
			return nil
		}
		return errors.New("no answer")
	}, time.Millisecond, 3)
	if err != nil {
		t.Fatalf("misses with answers between them ended the watch: %v", err)
	}
}

func TestWatchBrowserEndsQuietlyWhenDashcastCloses(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	gone := make(chan struct{})
	close(gone) // closing dashcast ends Chrome's context too
	cancel()
	if err := watchBrowser(ctx, gone, func(context.Context) error { return nil }, time.Hour, 3); err != nil {
		t.Fatalf("dashcast closing was taken for Chrome breaking: %v", err)
	}
}

func TestTabGivenUpAfterMissesInARow(t *testing.T) {
	var h tabHealth
	for i := 1; i < tabMisses; i++ {
		if h.answered(false) {
			t.Fatalf("given up after %d misses, want %d", i, tabMisses)
		}
	}
	if !h.answered(false) {
		t.Fatalf("not given up after %d misses", tabMisses)
	}

	h = tabHealth{}
	for round := 0; round < 3; round++ {
		for i := 1; i < tabMisses; i++ {
			if h.answered(false) {
				t.Fatalf("round %d: given up although an answer came between", round)
			}
		}
		h.answered(true)
	}
	if h.missed != 0 {
		t.Fatalf("an answer did not reset the misses: %d", h.missed)
	}
}

func TestTabMemory(t *testing.T) {
	const mb = 1 << 20
	start := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)
	var h tabHealth

	reload, report := h.heap(start, 100*mb, 512*mb)
	if reload || !report {
		t.Fatalf("first look, under the limit: reload %t report %t, want no reload and a report", reload, report)
	}
	if reload, report = h.heap(start.Add(time.Minute), 100*mb, 512*mb); reload || report {
		t.Fatalf("a minute on: reload %t report %t, want neither", reload, report)
	}
	if reload, _ = h.heap(start.Add(2*time.Minute), 600*mb, 512*mb); !reload {
		t.Fatal("over the limit, and not loaded again")
	}
	if reload, _ = h.heap(start.Add(3*time.Minute), 600*mb, 512*mb); reload {
		t.Fatal("loaded again for its memory a minute after the last time")
	}
	if reload, _ = h.heap(start.Add(2*time.Minute+heapRest), 600*mb, 512*mb); !reload {
		t.Fatal("still over the limit after the rest, and not loaded again")
	}
	if _, report = h.heap(start.Add(heapReport), 100*mb, 512*mb); !report {
		t.Fatal("no report after an hour")
	}
}

func TestHeapLookedAtOncePerInterval(t *testing.T) {
	start := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)
	h := tabHealth{lastHeap: start}
	if h.heapDue(start.Add(heapEvery / 2)) {
		t.Fatal("looked at the memory again too soon")
	}
	if !h.heapDue(start.Add(heapEvery)) {
		t.Fatal("did not look at the memory when due")
	}
	if h.heapDue(start.Add(heapEvery + time.Second)) {
		t.Fatal("looked at the memory twice in one interval")
	}
}

func TestTabMemoryFrom(t *testing.T) {
	for v, want := range map[string]int{"256": 256 << 20, " 1024 ": 1024 << 20, "": 512 << 20, "0": 512 << 20, "-5": 512 << 20, "lots": 512 << 20} {
		if got := tabMemoryFrom(v); got != want {
			t.Errorf("tabMemoryFrom(%q) = %d, want %d", v, got, want)
		}
	}
}

// TestBrokenBrowserAndTabsAreNoticed crashes a real tab's page and kills a real Chrome, and checks
// both are seen for what they are (GENBU-270). Skipped without a browser, unless
// DASHCAST_REQUIRE_BROWSER is set (browser_test.go).
func TestBrokenBrowserAndTabsAreNoticed(t *testing.T) {
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
		_, _ = w.Write([]byte("<!doctype html><title>dashboard</title><p>a dashboard"))
	}))
	defer ha.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	b, err := newBrowser(ctx, config{ha: ha.URL})
	if err != nil {
		noBrowser("the browser did not start: %v", err)
	}
	defer b.close()

	if err := b.answer(ctx); err != nil {
		t.Fatalf("a running Chrome did not answer: %v", err)
	}

	// A crashed page: its tab says so, the watch calls it dead, and it is never handed out again.
	tab, closeTab, err := b.open(ctx, "", "/", 320, 240, nil, false, false)
	if err != nil {
		t.Fatalf("opening a tab: %v", err)
	}
	w := newWarmTab(tab, closeTab, "crash|320x240")
	if w.dead() {
		t.Fatal("a fresh tab counted as dead")
	}
	why := make(chan string, 1)
	wctx, wcancel := context.WithCancel(ctx)
	defer wcancel()
	go watchTab(wctx, w, "crash", func(s string) { why <- s })
	_ = within(tab, answerWithin, page.Crash()) // the page dies before it can answer
	select {
	case s := <-why:
		t.Logf("the watch said: %s", s)
	case <-time.After(tabEvery*tabMisses + 10*time.Second):
		t.Fatal("a crashed page was not noticed")
	}
	if !w.dead() {
		t.Fatal("a crashed tab did not count as dead")
	}
	warm.park(w) // closes it, since it is dead
	if got := warm.take("crash|320x240"); got != nil {
		t.Fatal("a crashed tab was parked and handed out again")
	}

	// Another tab still works: one crashed page is not the browser.
	if err := b.answer(ctx); err != nil {
		t.Fatalf("Chrome stopped answering after one page crashed: %v", err)
	}
	tab2, close2, err := b.open(ctx, "", "/", 320, 240, nil, false, false)
	if err != nil {
		t.Fatalf("a new tab after the crash: %v", err)
	}
	var title string
	if err := within(tab2, answerWithin, chromedp.Title(&title)); err != nil || title != "dashboard" {
		t.Fatalf("the new tab shows %q, %v", title, err)
	}
	close2()

	// Chrome killed: its context ends, and the watch says so at once.
	b.kill()
	select {
	case <-b.gone():
	case <-time.After(15 * time.Second):
		t.Fatal("Chrome was killed, and its context did not end")
	}
	if err := watchBrowser(ctx, b.gone(), b.answer, time.Hour, 3); err == nil {
		t.Fatal("the watch did not notice Chrome was gone")
	}
}

// TestADashboardThatNeverLoadsIsGivenUp is what held every Show on 2026-11-17: three screens
// connected, opening their dashboards never finished, and nothing ended it for an hour and a half.
// Opening now gives up after loadWithin. Skipped without a browser, unless DASHCAST_REQUIRE_BROWSER
// is set (browser_test.go).
func TestADashboardThatNeverLoadsIsGivenUp(t *testing.T) {
	if chromePath("") == "" {
		if os.Getenv("DASHCAST_REQUIRE_BROWSER") != "" {
			t.Fatal("no Chrome or headless-shell to run")
		}
		t.Skip("no Chrome or headless-shell to run")
	}
	release := make(chan struct{})
	ha := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Half a page, and then nothing: the load never ends.
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte("<!doctype html><title>dashboard</title><p>a dash"))
		w.(http.Flusher).Flush()
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer ha.Close()
	defer close(release)

	ctx, cancel := context.WithTimeout(context.Background(), loadWithin+60*time.Second)
	defer cancel()
	b, err := newBrowser(ctx, config{ha: ha.URL})
	if err != nil {
		t.Fatalf("the browser did not start: %v", err)
	}
	defer b.close()

	start := time.Now()
	_, closeTab, err := b.open(ctx, "", "/", 320, 240, nil, false, false)
	if err == nil {
		closeTab()
		t.Fatal("a page that never finished loading was taken as opened")
	}
	if took := time.Since(start); took > loadWithin+10*time.Second {
		t.Fatalf("opening took %s to give up, want about %s", took, loadWithin)
	}
	if err := b.answer(ctx); err != nil {
		t.Fatalf("Chrome stopped answering after a page that never loaded: %v", err)
	}
}
