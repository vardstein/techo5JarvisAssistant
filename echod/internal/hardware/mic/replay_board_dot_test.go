//go:build dot

package mic

import "testing"

// replayBoard has nothing to choose on the Dot: one board, one frame.
func replayBoard(*testing.T) string { return "Dot" }
