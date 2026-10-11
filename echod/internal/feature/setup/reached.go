package setup

import (
	"log/slog"
	"time"
)

// Whether anybody can get to the page at all.
//
// The page is switched on at the screen and opened in a browser somewhere else, and between the two
// sits the network: a phone on another Wi-Fi or on mobile data, a VPN that sends the house's other
// subnet through a tunnel, a router that keeps the devices' network apart. None of that reaches the
// device, so from the screen it looked like a page that was on and simply never came. Now the device
// notices that nothing has asked for anything and says so.

// arrived is a request at the page, whoever made it.
func (f *Feature) arrived() {
	f.mu.Lock()
	first := !f.reached
	f.reached = true
	f.mu.Unlock()
	if first {
		slog.Info("setup page: a browser reached it")
		f.Changed.Emit(struct{}{})
	}
}

// knockedHTTPS is a browser asking for https:// while the page is up. It got here, so the page is
// not unreached; it has only to be told to ask for http://.
func (f *Feature) knockedHTTPS() {
	f.mu.Lock()
	first := f.on(time.Now()) && !f.httpsTried
	if first {
		f.httpsTried = true
	}
	f.mu.Unlock()
	if first {
		slog.Info("setup page: a browser asked for https://; the page is http:// only")
		f.Changed.Emit(struct{}{})
	}
}

// HTTPSTried is whether a browser asked for https:// since the page was opened and nothing has
// been served to it over http:// since.
func (f *Feature) HTTPSTried() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.on(time.Now()) && f.httpsTried && !f.reached
}

// Unreached is whether the page has been up for a while and nothing has asked it for anything.
func (f *Feature) Unreached() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.unreached(time.Now())
}

func (f *Feature) unreached(now time.Time) bool {
	return f.on(now) && !f.reached && !f.httpsTried && now.Sub(f.openedAt) >= unreachedAfter
}

// checkReached runs unreachedAfter past an opening, so the screen changes its line then and the log
// has the fact for later. A page closed and opened again in between is a different opening.
func (f *Feature) checkReached(opened time.Time) {
	f.mu.Lock()
	still := f.openedAt.Equal(opened) && f.unreached(time.Now())
	f.mu.Unlock()
	if still {
		slog.Warn("setup page: nothing has reached it yet; the browser is likely on another network or behind a VPN",
			"after", unreachedAfter)
		f.Changed.Emit(struct{}{})
	}
}
