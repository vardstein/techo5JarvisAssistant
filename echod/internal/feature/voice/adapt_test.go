package voice

import "testing"

// The echo canceller learns from everything the device plays, its own replies included, and stops
// only while somebody is being listened to. Stopping for the whole turn left a device whose only sound
// was its replies with a filter that never learned anything.
func TestCancellerLearnsExceptWhileListening(t *testing.T) {
	var got []bool
	was := setAdapting
	setAdapting = func(on bool) { got = append(got, on) }
	t.Cleanup(func() { setAdapting = was })

	c := &conversation{}
	phases := []phase{phaseListening, phaseThinking, phaseReplying, phaseListening, phaseIdle}
	for _, p := range phases {
		c.enter(p)
	}
	want := []bool{false, true, true, false, true}
	if len(got) != len(want) {
		t.Fatalf("adapting set %d times, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("%s: adapting %v, want %v", phases[i], got[i], want[i])
		}
	}
}
