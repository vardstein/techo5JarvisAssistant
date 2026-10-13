package light

import "testing"

// A device with no ring puts up no ring light and no segments; one with a ring puts up all thirteen.
func TestTheRingLightOnlyWhereThereIsARing(t *testing.T) {
	was := ringPresent
	defer func() { ringPresent = was }()
	l := Get()

	ringPresent = func() bool { return false }
	if got := l.Entities(); len(got) != 0 {
		t.Errorf("no ring, yet %d entities", len(got))
	}
	ringPresent = func() bool { return true }
	if got := l.Entities(); len(got) != 13 {
		t.Errorf("a ring, and %d entities rather than 13", len(got))
	}
}
