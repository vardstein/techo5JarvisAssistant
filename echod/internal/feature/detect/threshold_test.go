package detect

import "testing"

// The stop word gets an allowance for a playing speaker; the wake words do not get that one (the few
// measured safe over music go further, see playback_test.go).
//
// While the speaker plays loud, what reaches the detector is the residual and the word scores lower.
// The stop word is always said over a playing speaker, so its threshold drops by playingSlack then. A
// wake word keeps its own: with the same drop, the device's own songs woke it.
func TestOnlyTheStopWordGetsTheSlack(t *testing.T) {
	base := func(int) float64 { return 0.8 }
	const stop = 0.7

	if got := thresholdFor(StopSlot, base, stop, false); got != stop {
		t.Errorf("stop threshold in a quiet room is %v, want the configured %v", got, stop)
	}
	if got, want := thresholdFor(StopSlot, base, stop, true), stop-playingSlack; got != want {
		t.Errorf("stop threshold while the speaker masks is %v, want %v", got, want)
	}
	if got := thresholdFor(0, base, stop, true); got != base(0) {
		t.Errorf("wake threshold while the speaker masks is %v, want its own %v", got, base(0))
	}
}

// The slack never drags the stop word's threshold below the floor, however low it was configured.
func TestTheSlackStopsAtTheFloor(t *testing.T) {
	base := func(int) float64 { return 0.52 }

	if got := thresholdFor(StopSlot, base, 0.52, true); got != slackFloor {
		t.Errorf("a stop threshold of 0.52 with slack is %v, want the floor %v", got, slackFloor)
	}
}

// A slot that is not the stop word is judged against its own threshold, not the stop word's, whether
// or not the speaker plays.
func TestTheSlotsKeepTheirOwnThresholds(t *testing.T) {
	base := func(slot int) float64 { return 0.6 + float64(slot)/100 }

	for _, slot := range []int{0, 1, 2} {
		for _, masking := range []bool{false, true} {
			if got, want := thresholdFor(slot, base, 0.7, masking), base(slot); got != want {
				t.Errorf("slot %d judged at %v (masking %v), want its own %v", slot, got, masking, want)
			}
		}
	}
}
