//go:build linux

package update

import (
	"os"
	"path/filepath"
	"testing"
)

// The house counts a Show's version only once its slot is good, not while it is on trial.
func TestSlotGoodOnlyWhenTheBootedSlotIsGood(t *testing.T) {
	dir := t.TempDir()
	slot, states := filepath.Join(dir, "slot"), filepath.Join(dir, "slots")
	_ = os.MkdirAll(states, 0o755)
	write := func(name, v string) { _ = os.WriteFile(name, []byte(v), 0o644) }

	if slotGood(slot, states) {
		t.Error("good with no booted slot")
	}
	write(slot, "b\n")
	write(filepath.Join(states, "b.state"), "trial 2\n")
	if slotGood(slot, states) {
		t.Error("good while on trial")
	}
	write(filepath.Join(states, "b.state"), "good\n")
	if !slotGood(slot, states) {
		t.Error("not good once committed")
	}
	write(slot, "../b")
	if slotGood(slot, states) {
		t.Error("a slot name with a path in it was read")
	}
}
