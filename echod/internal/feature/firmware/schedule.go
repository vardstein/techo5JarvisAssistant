package firmware

import (
	"context"
	"errors"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/update"
)

// checkEvery is how often the device looks for a newer build of itself.
//
// It has to look on its own: esphome entities do not poll, so the only check Home Assistant ever sends
// is somebody pressing refresh, and a device left alone would never learn a release exists. Every ten
// minutes: a manifest is a few hundred bytes from the release's download, not GitHub's rate-limited API,
// so six an hour cost nothing, and a fix released while somebody stands at the Show should be on it
// before they have walked off.
const checkEvery = 10 * time.Minute

// A check that got no answer is tried again sooner. clockRetry while the clock is not set yet, which is
// a minute or two after boot, or as soon as Home Assistant says what time it is; failRetry after
// anything else, a network that is down or GitHub having a bad hour.
const (
	clockRetry = time.Minute
	failRetry  = 5 * time.Minute
)

// nextCheck is how long after a check that ended with err the next one is.
func nextCheck(err error) time.Duration {
	switch {
	case err == nil:
		return checkEvery
	case errors.Is(err, update.ErrClock):
		return clockRetry
	}
	return failRetry
}

// checkSettle is how long after starting the first check happens. Not immediately: a device coming up
// has a wake word to load and a network that may not be there yet, and nothing is waiting on this.
const checkSettle = 5 * time.Minute

// Run looks for a newer build on its own schedule, and keeps the one this process is running.
func (f *Firmware) Run(ctx context.Context) error {
	go f.autoLoop(ctx)
	go f.houseLoop(ctx)
	go f.showCommitted(ctx)
	first := time.NewTimer(checkSettle)
	defer first.Stop()

	select {
	case <-ctx.Done():
		return nil
	case <-first.C:
		// Having got this far is the only evidence there is that the build works, so it is the moment an
		// update stops being on trial. The same wait serves both: a device that has been up this long has
		// a network to ask over and a binary worth keeping.
		update.Commit()
	}

	for {
		// A check from the screen or Home Assistant in between does not move this one: the schedule is
		// what makes sure there is a check, not that there is only one.
		t := time.NewTimer(nextCheck(f.Check(ctx)))
		select {
		case <-ctx.Done():
			t.Stop()
			return nil
		case <-t.C:
		}
	}
}

// showCommitted publishes the running version as committed once its trial is over, which on a slot
// system is the boot script's word, five minutes after the daemon came up.
func (f *Firmware) showCommitted(ctx context.Context) {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		if update.Committed() {
			f.committed.Set(layout.Version)
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
