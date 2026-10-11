package update

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

// The house can take a Show back, but never below the floor, and the channel path still cannot.
func TestAlignGoesBothWaysAboveTheFloor(t *testing.T) {
	for _, c := range []struct {
		offered, running, floor string
		ok                      bool
	}{
		{"v1.0.3-stable.52", "v1.0.2-stable.48", "", true},
		{"v1.0.2-stable.48", "v1.0.3-stable.52", "", true},
		{"v1.0.2-stable.48", "v1.0.2-stable.48", "", true},
		{"v1.0.2-stable.48", "v1.0.3-stable.52", "v1.0.2-stable.48", true},
		{"v1.0.2-stable.47", "v1.0.3-stable.52", "v1.0.2-stable.48", false},
		{"v1.0.2-stable.48", "a build of my own", "", false},
	} {
		err := alignable(c.offered, c.running, c.floor)
		if (err == nil) != c.ok {
			t.Errorf("%s over %s, floor %q: %v", c.offered, c.running, c.floor, err)
		}
	}
	if err := notOlder("v1.0.2-stable.48", "v1.0.3-stable.52"); err == nil {
		t.Error("the channel path went back a version")
	}
}

func TestAlignRefusesAManifestForAnotherBoard(t *testing.T) {
	somewhere(t)
	restoreBoard, running := deviceBoard, layout.Version
	t.Cleanup(func() { deviceBoard, layout.Version = restoreBoard, running })
	deviceBoard, layout.Version = "crown", "v1.0.3-stable.52"

	b := Binary{URL: "http://example/echod", SHA256: strings.Repeat("a", 64), Size: 1}
	m := Manifest{Product: JarvisShowProduct, Boards: []string{"checkers"}, Version: "v1.0.2-stable.48",
		Binaries: map[string]Binary{arch: b}}
	if err := Align(context.Background(), m, nil); err == nil || !strings.Contains(err.Error(), "board") {
		t.Errorf("%v, want a board refusal", err)
	}
}

// A tag is a release name before it is part of a URL, and the manifest at it must name it.
func TestFetchTagReadsThatReleaseOnly(t *testing.T) {
	seed, pub := testKey(t)
	served := "v1.0.3-stable.52"
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		body, _ := json.Marshal(Manifest{Product: JarvisShowProduct, Boards: []string{"crown"}, Version: served,
			Binaries: map[string]Binary{arch: {URL: "https://example/echod", SHA256: strings.Repeat("a", 64), Size: 1}}})
		if strings.HasSuffix(r.URL.Path, ".sig") {
			sig, _ := Sign(body, seed)
			_, _ = w.Write([]byte(sig))
			return
		}
		_, _ = w.Write(body)
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	restoreKey, restoreClock, restoreURL, restoreBoard := releaseKey, clockSet, tagURL, deviceBoard
	t.Cleanup(func() { releaseKey, clockSet, tagURL, deviceBoard = restoreKey, restoreClock, restoreURL, restoreBoard })
	releaseKey, deviceBoard = pub, "crown"
	clockSet = func() bool { return true }
	var asked string
	tagURL = func(tag string) string { asked = tag; return srv.URL + "/" + tag + "/manifest.json" }

	if m, err := FetchTag(context.Background(), "v1.0.3-stable.52"); err != nil || m.Version != served {
		t.Fatalf("the tag's own manifest: %v, %v", m.Version, err)
	}
	if _, err := FetchTag(context.Background(), "v1.0.2-stable.48"); err == nil {
		t.Error("a manifest naming another release was taken")
	}
	asked = ""
	for _, bad := range []string{"../../evil", "channel-dev", "", "v1.0.3/../x"} {
		if _, err := FetchTag(context.Background(), bad); err == nil {
			t.Errorf("%q was fetched", bad)
		}
	}
	if asked != "" {
		t.Errorf("a bad tag reached the URL: %q", asked)
	}
}
