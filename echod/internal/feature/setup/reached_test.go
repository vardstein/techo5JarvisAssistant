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
