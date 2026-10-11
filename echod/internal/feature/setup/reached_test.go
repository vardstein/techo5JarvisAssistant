package setup

import (
	"testing"
	"time"
)

// A page that is up and that nothing has asked for says so once it has had the time to be typed in,
// and stops saying so at the first request, from anyone.
func TestAPageNobodyReachesSaysSo(t *testing.T) {
	f := build()
	f.Open()
	if f.Unreached() {
		t.Fatal("unreached the moment it opened")
	}

	f.mu.Lock()
	f.openedAt = time.Now().Add(-unreachedAfter - time.Second)
	f.mu.Unlock()
	if !f.Unreached() {
		t.Fatal("up past the wait with no request and not said")
	}

	get(f, "/setup", nil) // not let in, but it got here
	if f.Unreached() {
		t.Error("still unreached after a browser asked")
	}

	// Closed and opened again is a new opening: whoever came last time says nothing about this one.
	f.Close()
	f.Open()
	f.mu.Lock()
	f.openedAt = time.Now().Add(-unreachedAfter - time.Second)
	f.mu.Unlock()
	if !f.Unreached() {
		t.Error("a reopened page kept the last opening's visitor")
	}

	f.Close()
	if f.Unreached() {
		t.Error("a closed page called unreached")
	}
}

// A browser that asked for https:// got here: the page says to ask for http:// rather than that
// nothing came, and stops saying it once a request has been served.
func TestABrowserAskingForHTTPSIsToldHTTP(t *testing.T) {
	f := build()
	f.knockedHTTPS()
	if f.HTTPSTried() {
		t.Fatal("a knock on a closed page counted")
	}

	f.Open()
	f.knockedHTTPS()
	f.mu.Lock()
	f.openedAt = time.Now().Add(-unreachedAfter - time.Second)
	f.mu.Unlock()
	if !f.HTTPSTried() {
		t.Fatal("the https ask was not kept")
	}
	if f.Unreached() {
		t.Error("a page a browser knocked on called unreached")
	}

	get(f, "/setup", nil)
	if f.HTTPSTried() {
		t.Error("still asking for http after a request came over it")
	}

	f.Close()
	f.Open()
	if f.HTTPSTried() {
		t.Error("a reopened page kept the last opening's https ask")
	}
}
