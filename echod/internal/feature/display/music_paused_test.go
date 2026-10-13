//go:build !dot && !spot

package display

import (
	"testing"
	"time"
)

// A paused song keeps the screen for pausedPageFor and then gives it back, later if somebody touched
// the page meanwhile. Playing, or another song, starts the count over.
func TestAPausedSongGivesTheScreenBack(t *testing.T) {
	d := &Display{}
	now := time.Unix(1_790_000_000, 0)
	song := "Sawbone's Stew\x00Music Assistant\x00media_player.living_room"

	if d.pausedTooLong(now, song, false) {
		t.Fatal("a playing song lost the screen")
	}
	if d.pausedTooLong(now, song, true) {
		t.Fatal("a song lost the screen the moment it paused")
	}
	if d.pausedTooLong(now.Add(pausedPageFor-time.Second), song, true) {
		t.Fatal("a paused song lost the screen early")
	}
	if !d.pausedTooLong(now.Add(pausedPageFor), song, true) {
		t.Fatal("a paused song kept the screen past pausedPageFor")
	}
	if !d.pausedTooLong(now.Add(time.Hour), song, true) {
		t.Fatal("a paused song came back on its own")
	}

	// Play brings the page back, and the next pause is counted from itself.
	later := now.Add(time.Hour)
	if d.pausedTooLong(later, song, false) {
		t.Fatal("a song played again stayed off the screen")
	}
	if d.pausedTooLong(later.Add(time.Minute), song, true) || d.pausedTooLong(later.Add(time.Minute+pausedPageFor-time.Second), song, true) {
		t.Fatal("a second pause was counted from the first")
	}

	// Another song paused is a page of its own.
	if d.pausedTooLong(later.Add(time.Minute+pausedPageFor), "Amsterdam\x00Music Assistant\x00media_player.living_room", true) {
		t.Fatal("another song was counted from the one before it")
	}

	// Somebody touching the page keeps it.
	d = &Display{}
	d.pausedTooLong(now, song, true)
	d.touchedAt = now.Add(4 * time.Minute)
	if d.pausedTooLong(now.Add(pausedPageFor+time.Minute), song, true) {
		t.Fatal("a page somebody was using went away")
	}
	if !d.pausedTooLong(now.Add(4*time.Minute+pausedPageFor), song, true) {
		t.Fatal("a touch kept the page for good")
	}
}
