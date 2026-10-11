package main

import (
	"context"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

// Loading a dashboard is the dear part: on a Raspberry Pi a heavy one keeps Chrome busy for tens of
// seconds. So a screen that goes away does not take its tab with it at once. The tab is parked -
// its stream stopped - and a screen that comes back for the same dashboard within warmFor gets it
// back without loading anything. A device restarting, a dashboard switched away from and back, the
// connection dropping for a moment: none of them is a reload.
//
// A parked page keeps running. It used to be frozen as well (Page.setWebLifecycleState), but
// Chromium 152 never draws a frozen page again once it is thawed: its scripts run, the screencast
// sends one frame and then nothing, and not even a reload brings the frames back. A Show picked
// up that way kept the picture from the moment it reconnected, clock and all.

// warmFor is how long a parked tab waits, DASHCAST_WARM to change it. Half an hour, so a dashboard
// looked at again after a while - the room's, Music Assistant's - is still there rather than a
// load of Home Assistant from nothing. What it costs is bounded by warmCount, not by the time.
var warmFor = 30 * time.Minute

const warmCount = 4 // tabs parked at once, the oldest closed to make room

// warmFrom is warmFor as DASHCAST_WARM says: a duration (45m, 2h), or the default for anything else.
func warmFrom(v string) time.Duration {
	if d, err := time.ParseDuration(strings.TrimSpace(v)); err == nil && d > 0 {
		return d
	}
	return 30 * time.Minute
}

// warmTab is a tab and whoever is watching its frames: the session using it, or nobody while it is
// parked. A tab's frames arrive through one listener for its whole life, since a listener cannot be
// taken off again.
type warmTab struct {
	ctx   context.Context
	close func()

	mu      sync.Mutex
	onFrame func(*page.EventScreencastFrame)
	key     string
	expiry  *time.Timer
}

func newWarmTab(tab context.Context, closeTab func(), key string) *warmTab {
	w := &warmTab{ctx: tab, close: closeTab, key: key}
	chromedp.ListenTarget(tab, func(ev any) {
		f, ok := ev.(*page.EventScreencastFrame)
		if !ok {
			return
		}
		w.mu.Lock()
		on := w.onFrame
		w.mu.Unlock()
		if on != nil {
			on(f)
		}
	})
	return w
}

func (w *warmTab) watch(on func(*page.EventScreencastFrame)) {
	w.mu.Lock()
	w.onFrame = on
	w.mu.Unlock()
}

// warmPool is the parked tabs, by what they show to whom.
type warmPool struct {
	mu     sync.Mutex
	parked []*warmTab // oldest first
}

var warm = &warmPool{}

// take is the parked tab for key, still alive, or nil.
func (p *warmPool) take(key string) *warmTab {
	p.mu.Lock()
	var w *warmTab
	for i, t := range p.parked {
		if t.key == key {
			w = t
			p.parked = append(p.parked[:i], p.parked[i+1:]...)
			break
		}
	}
	p.mu.Unlock()
	if w == nil {
		return nil
	}
	w.expiry.Stop()
	if err := chromedp.Run(w.ctx, chromedp.Evaluate(`1`, nil)); err != nil {
		w.close()
		return nil
	}
	return w
}

// discard closes any parked tab matching key. It is used by Jarvis Crown's one-shot cold reload.
func (p *warmPool) discard(key string) {
	p.mu.Lock()
	var closed []*warmTab
	keep := p.parked[:0]
	for _, w := range p.parked {
		if w.key == key {
			closed = append(closed, w)
			continue
		}
		keep = append(keep, w)
	}
	p.parked = keep
	p.mu.Unlock()
	for _, w := range closed {
		if w.expiry != nil {
			w.expiry.Stop()
		}
		w.close()
	}
}

// park stops a tab's stream and keeps it for warmFor.
func (p *warmPool) park(w *warmTab) {
	w.watch(nil)
	if err := chromedp.Run(w.ctx, page.StopScreencast()); err != nil {
		w.close()
		return
	}
	p.mu.Lock()
	var evict *warmTab
	if len(p.parked) >= warmCount {
		evict, p.parked = p.parked[0], p.parked[1:]
	}
	p.parked = append(p.parked, w)
	w.expiry = time.AfterFunc(warmFor, func() { p.drop(w) })
	p.mu.Unlock()
	if evict != nil {
		evict.expiry.Stop()
		evict.close()
	}
}

// drop closes a parked tab whose time is up.
func (p *warmPool) drop(w *warmTab) {
	p.mu.Lock()
	for i, t := range p.parked {
		if t == w {
			p.parked = append(p.parked[:i], p.parked[i+1:]...)
			p.mu.Unlock()
			slog.Info("closing a parked dashboard nobody came back for", "screen", w.key)
			w.close()
			return
		}
	}
	p.mu.Unlock()
}
