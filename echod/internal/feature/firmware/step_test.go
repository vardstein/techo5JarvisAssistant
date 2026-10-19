package firmware

import (
	"errors"
	"fmt"
	"net"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/update"
)

// Each step is a line the house's update card can read; no version, no line (GENBU-215).
func TestEachStepIsALine(t *testing.T) {
	for s, want := range map[update.Step]string{
		update.StepChecking:    "Checking v1.0.3",
		update.StepDownloading: "Downloading v1.0.3",
		update.StepWriting:     "Installing v1.0.3",
		update.StepRestarting:  "Restarting into v1.0.3",
		update.StepNone:        "",
	} {
		if got := stepLine(s, "v1.0.3"); got != want {
			t.Errorf("step %d: %q, want %q", s, got, want)
		}
	}
	if got := stepLine(update.StepDownloading, ""); got != "" {
		t.Errorf("no version: %q", got)
	}
}

// A failure says why first, and keeps the error for whoever looks into it.
func TestAFailureSaysWhyFirst(t *testing.T) {
	refused := &net.OpError{Op: "dial", Net: "tcp", Err: errors.New("connection refused")}
	err := fmt.Errorf("update: fetching x: %w", refused)
	if got := explained(err); got != "can't reach the update server ("+err.Error()+")" {
		t.Errorf("known: %q", got)
	}
	if got := explained(errors.New("update: something new")); got != "update: something new" {
		t.Errorf("unknown: %q", got)
	}
}

// The house line's reason is what this process saw, else the slot trial taking it out.
func TestWhyFailed(t *testing.T) {
	u := &Firmware{}
	if got := u.whyFailed("v1.0.3"); got != "" {
		t.Errorf("nothing seen: %q", got)
	}
	u.rolledBack = "v1.0.3"
	if got := u.whyFailed("v1.0.3"); got != "it didn't come back after the restart" {
		t.Errorf("rolled back: %q", got)
	}
	u.fail("v1.0.3", errors.New("update: odd"))
	if got := u.whyFailed("v1.0.3"); got != "update: odd" {
		t.Errorf("no plain reason: %q", got)
	}
	u.fail("v1.0.3", fmt.Errorf("x: %w", update.ErrNoRoom))
	if got := u.whyFailed("v1.0.3"); got != "not enough space on the Show" {
		t.Errorf("plain reason: %q", got)
	}
	if got := u.whyFailed(""); got != "" {
		t.Errorf("no version: %q", got)
	}
}
