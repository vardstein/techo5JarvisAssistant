package voice

import (
	"testing"
	"time"
)

func TestHeardUpTo(t *testing.T) {
	const text = "The kitchen is at 21 degrees and the bathroom at 19."
	cases := []struct {
		name   string
		played time.Duration
		audio  time.Duration
		whole  bool
		heard  string
		ok     bool
	}{
		{"nothing played yet", 0, 4 * time.Second, true, "", true},
		{"a third of it, timed", time.Second, 3 * time.Second, true, "The kitchen is at", true},
		{"half of it, timed, mid-word", 2 * time.Second, 4 * time.Second, true, "The kitchen is at 21", true},
		{"all of it", 4 * time.Second, 4 * time.Second, true, "", false},
		{"past the end", 5 * time.Second, 4 * time.Second, true, "", false},
		{"length unknown, from the rate", 2 * time.Second, 0, false, "The kitchen is at 21 degrees", true},
		{"length unknown, past the text", 10 * time.Second, 0, false, "", false},
		{"inside the first word", 100 * time.Millisecond, 4 * time.Second, true, "", true},
	}
	for _, c := range cases {
		heard, ok := heardUpTo(text, c.played, c.audio, c.whole)
		if heard != c.heard || ok != c.ok {
			t.Errorf("%s: heardUpTo = %q, %v, want %q, %v", c.name, heard, ok, c.heard, c.ok)
		}
	}
	if _, ok := heardUpTo("  ", time.Second, 0, false); ok {
		t.Error("an empty reply was reported as cut off")
	}
}
