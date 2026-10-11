//go:build !linux

package update

import (
	"context"
	"errors"
)

func slotSystem() bool { return false }

// Committed reports whether the running build has passed its trial.
func Committed() bool { return !OnTrial() }

func installRootfs(context.Context, Manifest, func(float32)) error {
	return errors.New("update: slot installs only work on the device")
}
