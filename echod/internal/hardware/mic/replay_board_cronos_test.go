//go:build !dot && !spot

package mic

import (
	"os"
	"testing"
)

// replayBoard reads a capture as the board it was taken on: TECHO5_BOARD=crown for a Show 8's six
// channels, anything else a Show 5's four. On a workstation onCrown always gives the Show 5, so a
// replay of a Show 8 take has to say so.
func replayBoard(t *testing.T) string {
	if os.Getenv("TECHO5_BOARD") != "crown" {
		return "Show 5"
	}
	c, m, r := Channels, Mics, RefFirst
	Channels, Mics, RefFirst = show8Channels, show8Mics, show8RefFirst
	t.Cleanup(func() { Channels, Mics, RefFirst = c, m, r })
	return "Show 8"
}
