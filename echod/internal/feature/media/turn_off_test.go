package media

import (
	"testing"

	esphome "github.com/ygelfand/go-esphome-device"
)

// "Turn the music in the bedroom off" reaches the speaker as turn_off. Home Assistant only sends it to a
// player that says it can take it, and an area with one that can't failed the whole request on
// 2026-10-12, so the Show's music played on and the assistant made up a second player.
func TestTheSpeakerTakesTurnOffButNotTurnOn(t *testing.T) {
	f := build().mp.Features
	if f&esphome.MediaPlayerFeatureTurnOff == 0 {
		t.Error("the speaker does not offer turn_off")
	}
	if f&esphome.MediaPlayerFeatureTurnOn != 0 {
		t.Error("the speaker offers turn_on, which would start music on \"turn the bedroom on\"")
	}
}
