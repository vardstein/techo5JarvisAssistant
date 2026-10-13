//go:build !dot && !spot

package display

import "time"

// pausedPageFor is how long a paused song keeps the screen before the clock, slideshow or dashboard
// has it back. Music Assistant leaves a speaker paused for as long as nobody touches it, and Home
// Assistant reports it paused all that while, so the page would otherwise stay up for good.
const pausedPageFor = 5 * time.Minute

// pausedTooLong is whether a paused song has had the screen for pausedPageFor: counted from the pause,
// or from the last finger on the panel if that came later, as somebody using the page still wants it.
// Playing again, or another song, starts the count over; key is the song and where it is.
func (d *Display) pausedTooLong(now time.Time, key string, paused bool) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if !paused {
		d.pausedKey, d.pausedSince = "", time.Time{}
		return false
	}
	if key != d.pausedKey || d.pausedSince.IsZero() {
		d.pausedKey, d.pausedSince = key, now
	}
	from := d.pausedSince
	if d.touchedAt.After(from) {
		from = d.touchedAt
	}
	return now.Sub(from) >= pausedPageFor
}
