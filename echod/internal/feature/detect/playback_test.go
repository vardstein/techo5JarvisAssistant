package detect

import (
	"testing"
	"time"
)

func playbackEngine(id string) (*Engine, *slot) {
	e := New(1, nil)
	e.Threshold = func(int) float64 { return 0.8 }
	e.Playback = func(slot int, id string) bool { return heardThrough(slot, id, true, false) }
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

func TestHeardThroughOnlyWhileMaskingOrReplying(t *testing.T) {
	cases := []struct {
		slot     int
		id       string
		masking  bool
		replying bool
		want     bool
	}{
		{0, "okay_nabu", true, false, true},
		{1, "okay_nabu", true, false, true},
		{0, "okay_nabu", false, false, false},
		{0, "okay_nabu", false, true, true},
		{0, "hey_jarvis", true, false, false},
		{0, "hey_jarvis", false, true, false},
		{StopSlot, "okay_nabu", true, false, false},
		{StopSlot, "okay_nabu", false, true, false},
	}
	for _, c := range cases {
		if got := heardThrough(c.slot, c.id, c.masking, c.replying); got != c.want {
			t.Errorf("heardThrough(%d, %q, %v, %v) = %v, want %v", c.slot, c.id, c.masking, c.replying, got, c.want)
		}
	}
}

// Over the device's own reply the allowance holds unless the reply says the word itself, which is the
// one way the device could wake itself (GENBU-250).
func TestOverOwnReply(t *testing.T) {
	cases := []struct {
		id       string
		text     string
		replying bool
		want     bool
	}{
		{"okay_nabu", "it is 21 degrees in the kitchen.", true, true},
		{"okay_nabu", "", true, true},
		{"okay_nabu", "to wake me, say okay nabu.", true, false},
		{"okay_nabu", "nabu casa runs the cloud.", true, false},
		{"okay_nabu", "it is 21 degrees.", false, false},
		{"hey_jarvis", "jarvis is listening.", true, false},
	}
	for _, c := range cases {
		if got := overOwnReply(c.id, c.text, c.replying); got != c.want {
			t.Errorf("overOwnReply(%q, %q, %v) = %v, want %v", c.id, c.text, c.replying, got, c.want)
		}
	}
}

// Okay Nabu said over the reply at the score seen on genbuShow5 starts the turn.
func TestAWordOverTheOwnReplyIsHeardTheFirstTime(t *testing.T) {
	e := New(1, nil)
	e.Threshold = func(int) float64 { return 0.8 }
	e.Playback = func(slot int, id string) bool {
		return heardThrough(slot, id, false, overOwnReply(id, "it is 21 degrees in the kitchen.", true))
	}
	s := &e.slots[0]
	s.model.ID = "okay_nabu"

	if fired, _ := say(e, s, 0.547, time.Now()); !fired {
		t.Fatal("okay_nabu at 0.547 over the device's own reply did not fire")
	}
}
