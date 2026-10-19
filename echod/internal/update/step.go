package update

import (
	"context"
	"errors"
	"net"
	"sync/atomic"
)

// Step is how far a running install has got, for the screen to say more than "installing" and for
// Home Assistant to tell a stuck Show from a slow one (GENBU-214, GENBU-215).
type Step int32

const (
	StepNone Step = iota
	// StepChecking is the manifest's checks before the download, and the download's once it is in.
	StepChecking
	StepDownloading
	// StepWriting is the new binary going into /system, or slotctl filling the other slot.
	StepWriting
	// StepRestarting is an install that went in, waiting for the restart that puts it in service.
	StepRestarting
)

var step atomic.Int32

// CurrentStep is where the running install is, StepNone when none is running.
func CurrentStep() Step { return Step(step.Load()) }

// stepHook is told every change of step, so Home Assistant can follow an install it cannot see the
// screen of.
var stepHook atomic.Pointer[func(Step)]

// OnStep has f told each change of step, from the goroutine that makes it. One at a time: a second call
// replaces the first.
func OnStep(f func(Step)) { stepHook.Store(&f) }

func setStep(s Step) {
	if Step(step.Swap(int32(s))) == s {
		return
	}
	if f := stepHook.Load(); f != nil {
		(*f)(s)
	}
}

// The kinds of failure Reason can put in plain words. They are marked on the errors that already say
// what happened, so a log line reads the same as before.
var (
	ErrNoRoom         = errors.New("update: not enough room")
	ErrNotForThisShow = errors.New("update: not made for this Show")
	ErrUnsigned       = errors.New("update: not signed by the release key")
	ErrDamaged        = errors.New("update: the download does not match the release")
)

// marked is err with a kind Reason can find, its message left as it was.
type marked struct{ err, kind error }

func (m marked) Error() string   { return m.err.Error() }
func (m marked) Unwrap() []error { return []error{m.err, m.kind} }

func mark(kind, err error) error {
	if err == nil {
		return nil
	}
	return marked{err, kind}
}

// Reason is why an install or a check failed, in a few plain words for somebody looking at the Show
// rather than its log: the first cronos install failed on a Show that could not reach GitHub, and the
// only place that said so was the log. "" when the error is none of the kinds known here; the full
// error is still on the setup page and in the update status.
func Reason(err error) string {
	var ne net.Error
	switch {
	case err == nil:
		return ""
	case errors.Is(err, ErrNotForThisShow):
		return "the release isn't made for this Show"
	case errors.Is(err, ErrUnsigned):
		return "the release isn't signed with Genbu's key"
	case errors.Is(err, ErrNoRoom):
		return "not enough space on the Show"
	case errors.Is(err, ErrDamaged):
		return "the download was damaged"
	case errors.Is(err, ErrClock):
		return "the clock isn't set yet"
	case errors.Is(err, context.DeadlineExceeded), errors.As(err, &ne):
		return "can't reach the update server"
	}
	return ""
}
