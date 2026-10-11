package buttons

import (
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/lib/input"
)

// A listener hearing the second of two buttons go down can already see the first one held, which is
// how the Show knows both volume buttons are being held for a factory reset; letting either go says
// so at once.
func TestDownSeesTheOtherButtonHeld(t *testing.T) {
	c := &Controller{}
	both := 0
	defer c.Events.Listen(func(e Event) {
		if e.Kind == Tap && c.Down(VolumeUp) && c.Down(VolumeDown) {
			both++
		}
	})()

	down := map[uint16]*held{}
	key := func(code uint16, v int32) { c.key(down, input.Event{Type: input.EvKey, Code: code, Value: v}) }
	key(115, 1) // volume up
	if !c.Down(VolumeUp) || c.Down(VolumeDown) {
		t.Fatalf("after volume up: up %v down %v", c.Down(VolumeUp), c.Down(VolumeDown))
	}
	key(114, 1) // volume down, joining it
	if both != 1 {
		t.Errorf("the second press was heard with both held %d times, want 1", both)
	}
	key(115, 0)
	if c.Down(VolumeUp) || !c.Down(VolumeDown) {
		t.Errorf("after letting up go: up %v down %v", c.Down(VolumeUp), c.Down(VolumeDown))
	}
	key(114, 0)
	if c.Down(VolumeDown) {
		t.Error("volume down still held after its release")
	}
}
