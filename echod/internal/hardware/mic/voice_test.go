package mic

import (
	"math"
	"testing"
)

// The level two Shows compare is the loudest fifth of the last second (GENBU-224): a wake word's louder
// syllables, not the quiet around them, and not one knock on its own.
func TestHeardIsTheLoudestFifthOfASecond(t *testing.T) {
	var h heard
	if rms, clipped := h.loudest(); rms != 0 || clipped {
		t.Fatalf("an empty second is %v, clipped %v", rms, clipped)
	}

	for i := range heardFrames {
		band := float32(100)
		if i%5 == 0 {
			band = 1000
		}
		h.push(band, band)
	}
	if rms, _ := h.loudest(); math.Abs(float64(rms)-1000) > 0.5 {
		t.Errorf("ten frames at 1000 among forty at 100 heard as %v, want 1000", rms)
	}

	var knock heard
	for range heardFrames - 1 {
		knock.push(100, 100)
	}
	knock.push(30000, 30000)
	if rms, _ := knock.loudest(); rms > 10000 {
		t.Errorf("one knock carried the level to %v", rms)
	}
}

// A clipped frame counts while it is in the last second, and not after.
func TestHeardClipsForASecond(t *testing.T) {
	var h heard
	h.push(5000, clipSample)
	if _, clipped := h.loudest(); !clipped {
		t.Fatal("a peak at the converter's limit is not clipped")
	}
	for range heardFrames {
		h.push(100, 100)
	}
	if _, clipped := h.loudest(); clipped {
		t.Error("a clip more than a second old still counts")
	}
}

// Silence is a number JSON can carry.
func TestSilenceHasALevel(t *testing.T) {
	if v := dbfs(0); math.IsInf(v, 0) || math.IsNaN(v) || v > -90 {
		t.Errorf("silence is %v dBFS", v)
	}
	if v := dbfs(fullScale); math.Abs(v) > 1e-9 {
		t.Errorf("full scale is %v dBFS", v)
	}
}
