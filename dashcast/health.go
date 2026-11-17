package main

// What keeps dashcast from hanging on with a broken browser (GENBU-270). On 2026-10-29 the machine
// it ran in had no memory left; everything there crawled for hours, the Shows kept a frozen picture,
// and nothing noticed. On 2026-11-17 the Shows froze again, with dashcast up and its tabs idle. Now:
//
//   - Chrome gone, or not answering: dashcast stops with an error, and Docker's restart policy
//     (unless-stopped) starts it again with a fresh Chrome (watchBrowser).
//   - A tab crashed or stuck: its screen is closed rather than parked, so the Show, which connects
//     again by itself, gets a new tab (watchTab).
//   - A page that holds more memory than tabMemory is loaded again, which starts it fresh.
//   - The container's memory limit (README) is the last wall: a tab that grows past all of this is
//     killed inside the container, and the two above pick up after it, rather than the whole
//     machine slowing to a crawl.

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"time"

	cdpbrowser "github.com/chromedp/cdproto/browser"
	"github.com/chromedp/cdproto/cdp"
	cdpruntime "github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

const (
	// answerWithin is what Chrome or a tab gets to answer a small question. A crashed or stuck page
	// never answers, so nothing asks one without a limit.
	answerWithin = 5 * time.Second
	// loadWithin is what a page load or a navigation gets.
	loadWithin = 45 * time.Second

	browserEvery  = 30 * time.Second
	browserMisses = 3 // about a minute and a half of no answer, then dashcast stops

	tabEvery  = 5 * time.Second
	tabMisses = 6 // half a minute of no answer, and the tab is given up

	heapEvery  = time.Minute
	heapReport = time.Hour        // the log says each tab's memory this often, to see a leak grow
	heapRest   = 30 * time.Minute // a page loaded again for its memory is not loaded again sooner
)

// tabMemory is the most a tab's page may hold before it is loaded again, DASHCAST_TAB_MEMORY (in
// MB) to change it. A dashboard normally holds well under a tenth of this.
var tabMemory = 512 << 20

// tabMemoryFrom is tabMemory as DASHCAST_TAB_MEMORY says, in MB, or the default for anything else.
func tabMemoryFrom(v string) int {
	if mb, err := strconv.Atoi(strings.TrimSpace(v)); err == nil && mb > 0 {
		return mb << 20
	}
	return 512 << 20
}

// within runs actions on a tab, or on the browser's own context, and gives up after d.
func within(tab context.Context, d time.Duration, actions ...chromedp.Action) error {
	ctx, cancel := context.WithTimeout(tab, d)
	defer cancel()
	return chromedp.Run(ctx, actions...)
}

// gone is closed once Chrome is lost: it exited or crashed, or its connection broke (chromedp ends
// the browser's context then), or dashcast is closing it.
func (b *browser) gone() <-chan struct{} { return b.ctx.Done() }

// answer asks Chrome itself, not one of its tabs, for its version.
func (b *browser) answer(ctx context.Context) error {
	c := chromedp.FromContext(b.ctx)
	if c == nil || c.Browser == nil {
		return errors.New("no browser")
	}
	_, _, _, _, _, err := cdpbrowser.GetVersion().Do(cdp.WithExecutor(ctx, c.Browser))
	return err
}

// kill ends Chrome's process at once. Closing it the polite way waits on a Chrome that may be
// stuck.
func (b *browser) kill() {
	if c := chromedp.FromContext(b.ctx); c != nil && c.Browser != nil {
		if p := c.Browser.Process(); p != nil {
			_ = p.Kill()
		}
	}
}

// watchBrowser returns why Chrome is no use any more: it is gone, or it did not answer ask misses
// times in a row, every so often. It returns nil once ctx ends, dashcast closing.
func watchBrowser(ctx context.Context, gone <-chan struct{}, ask func(context.Context) error, every time.Duration, misses int) error {
	t := time.NewTicker(every)
	defer t.Stop()
	missed := 0
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-gone:
			if ctx.Err() != nil {
				return nil
			}
			return errors.New("Chrome is gone")
		case <-t.C:
		}
		actx, cancel := context.WithTimeout(ctx, answerWithin)
		err := ask(actx)
		cancel()
		if ctx.Err() != nil {
			return nil
		}
		if err == nil {
			missed = 0
			continue
		}
		missed++
		slog.Warn("Chrome did not answer", "times", missed, "err", err)
		if missed >= misses {
			return fmt.Errorf("Chrome did not answer %d times in a row: %w", missed, err)
		}
	}
}

// tabHealth is what watchTab keeps about one tab.
type tabHealth struct {
	missed     int
	lastHeap   time.Time // last look at its memory
	lastReport time.Time // last time the log had it
	lastReload time.Time // last load for its memory
}

// answered counts one look at the tab, and is true once it has not answered tabMisses times in a
// row.
func (h *tabHealth) answered(ok bool) (dead bool) {
	if ok {
		h.missed = 0
		return false
	}
	h.missed++
	return h.missed >= tabMisses
}

// heapDue is true when it is time to look at the tab's memory again.
func (h *tabHealth) heapDue(now time.Time) bool {
	if now.Sub(h.lastHeap) < heapEvery {
		return false
	}
	h.lastHeap = now
	return true
}

// heap is what the page's memory, used bytes, asks for: loading it again when it is over limit and
// was not loaded again for that a moment ago, and a line in the log every heapReport.
func (h *tabHealth) heap(now time.Time, used float64, limit int) (reload, report bool) {
	if h.lastReport.IsZero() || now.Sub(h.lastReport) >= heapReport {
		h.lastReport, report = now, true
	}
	if used > float64(limit) && (h.lastReload.IsZero() || now.Sub(h.lastReload) >= heapRest) {
		h.lastReload, reload = now, true
	}
	return reload, report
}

// watchTab looks at a screen's tab every tabEvery until ctx ends. It calls dead, and stops, once the
// page crashed or has not answered for half a minute; on the way, it loads the page again when it
// holds too much memory.
func watchTab(ctx context.Context, w *warmTab, name string, dead func(why string)) {
	t := time.NewTicker(tabEvery)
	defer t.Stop()
	h := tabHealth{lastHeap: time.Now()}
	for {
		select {
		case <-ctx.Done():
			return
		case <-w.crashed:
			dead("its page crashed")
			return
		case <-t.C:
		}
		err := within(w.ctx, answerWithin, chromedp.Evaluate(`1`, nil))
		if ctx.Err() != nil {
			return
		}
		if h.answered(err == nil) {
			dead(fmt.Sprintf("its page did not answer for %s: %v", tabEvery*tabMisses, err))
			return
		}
		if err != nil || !h.heapDue(time.Now()) {
			continue
		}
		var used float64
		if within(w.ctx, answerWithin, chromedp.ActionFunc(func(ctx context.Context) error {
			var err error
			used, _, _, _, err = cdpruntime.GetHeapUsage().Do(ctx)
			return err
		})) != nil {
			continue
		}
		reload, report := h.heap(time.Now(), used, tabMemory)
		if report {
			slog.Info("tab memory", "name", name, "mb", int(used)>>20)
		}
		if reload {
			slog.Warn("the page holds too much memory; loading it again", "name", name, "mb", int(used)>>20, "most", tabMemory>>20)
			_ = within(w.ctx, loadWithin, chromedp.Reload())
		}
	}
}
