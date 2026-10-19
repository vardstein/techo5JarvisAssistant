package home

import (
	"image"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// fakeSendspinArt makes the page's picture Sendspin's (a track it carries or one held), or not, for one test.
func fakeSendspinArt(t *testing.T, sendspin bool) {
	t.Helper()
	old := sendspinArt
	sendspinArt = func() bool { return sendspin }
	t.Cleanup(func() { sendspinArt = old })
}

// GENBU-169, genbuShow5 on 2026-10-13: "Sawbone's Stew" paused by voice and held on the page, and the room's
// speaker, still the route, moved on to another song. Its cover went in under the held title.
func TestTheRoutesCoverStaysOffSendspinsTrack(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	fakeCarried(t, false)
	t.Cleanup(func() { RemoteArt(nil, nil) })
	if err := config.Set().Home().MusicPrimary(testPrimary); err != nil {
		t.Fatal(err)
	}
	cover := func(path string, side int) {
		fake.setState(testPrimary, "playing", "Living Room Sonos")
		fake.mu.Lock()
		fake.states[testPrimary].Attributes["entity_picture"] = path
		if fake.images == nil {
			fake.images = map[string][]byte{}
		}
		fake.images[path] = testJPEG(t, side, side)
		fake.mu.Unlock()
	}
	shown := func() *image.RGBA { art, _ := remoteArt(); return art }

	cover("/api/media_player_proxy/first", 400)
	f.musicRouteTick()
	fakeSendspinArt(t, false)
	f.musicPlaybackTick()
	if shown() == nil {
		t.Fatal("the route's cover is not shown")
	}

	// Sendspin's track, paused from here and held, with its own picture.
	fakeSendspinArt(t, true)
	RemoteArt(testJPEG(t, 600, 600), func() bool { return true })
	held := shown()
	cover("/api/media_player_proxy/next", 500)
	f.musicPlaybackTick()
	if shown() != held {
		t.Fatal("the route's next song put its cover under the held track")
	}

	// The held track is let go: the page is the route's again, and so is the picture, though the
	// route's own did not change in the meantime.
	fakeSendspinArt(t, false)
	f.musicPlaybackTick()
	if art := shown(); art == nil || art == held {
		t.Fatal("the held track's cover stayed under the route's song")
	}
}
