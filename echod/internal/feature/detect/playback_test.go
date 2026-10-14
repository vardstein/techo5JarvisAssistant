package detect

import (
	"testing"
	"time"
)

func playbackEngine(id string) (*Engine, *slot) {
	e := New(1, nil)
	e.Threshold = func(int) float64 { return 0.8 }
	e.Playback = func(slot int, id string) bool { return heardThrough(slot, id, true) }
	s := &e.slots[0]
	s.model.ID = id
	return e, s
}

// Over the device's own playback, Okay Nabu said once is heard once: what would have been a near miss
// starts the turn.
func TestAWordSafeOverMusicIsHeardTheFirstTime(t *testing.T) {
	e, s := playbackEngine("okay_nabu")

	if fired, _ := say(e, s, 0.6, time.Now()); !fired {
		t.Fatalf("okay_nabu at 0.6 over playback did not fire against NearMiss %v", NearMiss)
	}
}

// Under NearMiss it is still nothing, music or not.
func TestBelowNearMissIsNothingOverMusic(t *testing.T) {
	e, s := playbackEngine("okay_nabu")

	if fired, _ := say(e, s, NearMiss-0.05, time.Now()); fired {
		t.Fatal("a score under NearMiss fired over playback")
	}
}

// A word nobody measured over music keeps its threshold there.
func TestAnUnmeasuredWordKeepsItsThresholdOverMusic(t *testing.T) {
	e, s := playbackEngine("hey_jarvis")

	if fired, _ := say(e, s, 0.6, time.Now()); fired {
		t.Fatal("hey_jarvis at 0.6 fired over playback, it is not in playbackWords")
	}
}

func TestHeardThroughOnlyWhileMasking(t *testing.T) {
	cases := []struct {
		slot    int
		id      string
		masking bool
		want    bool
	}{
		{0, "okay_nabu", true, true},
		{1, "okay_nabu", true, true},
		{0, "okay_nabu", false, false},
		{0, "hey_jarvis", true, false},
		{StopSlot, "okay_nabu", true, false},
	}
	for _, c := range cases {
		if got := heardThrough(c.slot, c.id, c.masking); got != c.want {
			t.Errorf("heardThrough(%d, %q, %v) = %v, want %v", c.slot, c.id, c.masking, got, c.want)
		}
	}
}
