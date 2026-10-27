//go:build !dot && !spot

package update

import "testing"

func TestJarvisShowReleaseChannels(t *testing.T) {
	old := releases
	t.Cleanup(func() { releases = old })
	releases = defaultReleases
	if got, want := Stable.URL(), "https://github.com/vardstein/techo5JarvisAssistant/releases/latest/download/manifest.json"; got != want {
		t.Fatalf("stable URL = %q, want %q", got, want)
	}
	if got, want := Staging.URL(), "https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-staging/manifest.json"; got != want {
		t.Fatalf("staging URL = %q, want %q", got, want)
	}
	if got, want := Dev.URL(), "https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest.json"; got != want {
		t.Fatalf("dev URL = %q, want %q", got, want)
	}
	for _, c := range Channels() {
		if back, ok := map[string]Channel{"stable": Stable, "staging": Staging, "dev": Dev}[c.Label()]; !ok || back != c {
			t.Fatalf("channel %d has label %q, which does not name it", c, c.Label())
		}
	}
}

// Shows from before GENBU-191 read these names; releases still carry them, copied from the bridge
// release. cronos came after the first release, so it reads manifest-cronos.json and crown and checkers
// Shows, which refuse a manifest naming a board they don't know, never see it.
func TestOlderShowsReadTheOldNames(t *testing.T) {
	old := releases
	t.Cleanup(func() { releases = old })
	releases = defaultReleases
	for board, want := range map[string]string{
		"crown":    "https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest.json",
		"checkers": "https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest.json",
		"cronos":   "https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest-cronos.json",
	} {
		if got := Dev.URLFor(board); got != want {
			t.Fatalf("%s dev URL = %q, want %q", board, got, want)
		}
	}
	if got, want := Stable.URLFor("cronos"), "https://github.com/vardstein/techo5JarvisAssistant/releases/latest/download/manifest-cronos.json"; got != want {
		t.Fatalf("cronos stable URL = %q, want %q", got, want)
	}
}

// This echod reads manifest-v2.json on every board, by channel and by tag (GENBU-191).
func TestEveryBoardReadsTheV2Manifest(t *testing.T) {
	old, oldBoard := releases, deviceBoard
	t.Cleanup(func() { releases, deviceBoard = old, oldBoard })
	releases = defaultReleases
	for _, board := range []string{"crown", "checkers", "cronos", ""} {
		deviceBoard = board
		if got, want := channelURL(Dev), "https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest-v2.json"; got != want {
			t.Fatalf("%q dev URL = %q, want %q", board, got, want)
		}
		if got, want := channelURL(Stable), "https://github.com/vardstein/techo5JarvisAssistant/releases/latest/download/manifest-v2.json"; got != want {
			t.Fatalf("%q stable URL = %q, want %q", board, got, want)
		}
		if got, want := tagURL("v1.3.0"), "https://github.com/vardstein/techo5JarvisAssistant/releases/download/v1.3.0/manifest-v2.json"; got != want {
			t.Fatalf("%q tag URL = %q, want %q", board, got, want)
		}
	}
}
