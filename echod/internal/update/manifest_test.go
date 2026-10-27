package update

import (
	"strings"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

// A release published before Binaries existed describes an arm64 build in its top-level fields, and an
// arm64 device has to go on updating through it — that is the only route off the build it is running.
func TestForReadsAReleaseThatPredatesBinaries(t *testing.T) {
	m := Manifest{
		Version: "0.0.6",
		URL:     "https://example/echod",
		SHA256:  strings.Repeat("a", 64),
		Size:    24 << 20,
	}
	if err := m.Valid(); err != nil {
		t.Fatal(err)
	}

	b, err := m.For("arm64")
	if err != nil {
		t.Fatal(err)
	}
	if b.URL != m.URL || b.SHA256 != m.SHA256 || b.Size != m.Size {
		t.Errorf("offered %+v, want the manifest's own fields", b)
	}

	if _, err := m.For("arm"); err == nil {
		t.Error("offered a 32-bit device a release that only carries arm64")
	}
}

// Every device takes the build for what it is running, and a release carrying both must not hand
// either one the other's.
func TestForTakesTheArchitectureTheDeviceRuns(t *testing.T) {
	m := Manifest{
		Version: "0.0.7",
		URL:     "https://example/echod-arm64",
		SHA256:  strings.Repeat("a", 64),
		Size:    24 << 20,
		Binaries: map[string]Binary{
			"arm64": {URL: "https://example/echod-arm64", SHA256: strings.Repeat("a", 64), Size: 24 << 20},
			"arm":   {URL: "https://example/echod-arm", SHA256: strings.Repeat("b", 64), Size: 22 << 20},
		},
	}
	if err := m.Valid(); err != nil {
		t.Fatal(err)
	}

	for arch, want := range map[string]string{
		"arm64": "https://example/echod-arm64",
		"arm":   "https://example/echod-arm",
	} {
		b, err := m.For(arch)
		if err != nil {
			t.Errorf("%s: %v", arch, err)
			continue
		}
		if b.URL != want {
			t.Errorf("%s: offered %s, want %s", arch, b.URL, want)
		}
	}
}

// Home Assistant offers an update whenever the version it is told about differs from the one running,
// not when it is newer, so a manifest naming something it cannot rank leaves a card on for good. A
// device has to refuse such a manifest instead of offering it — which is what happened when a release
// passed the git tag (dot-v0.5.10) where the version belongs.
func TestValidRefusesAVersionHomeAssistantCannotRank(t *testing.T) {
	binaries := map[string]Binary{"arm": {URL: "https://example/echod-arm", SHA256: strings.Repeat("a", 64), Size: 22 << 20}}

	for version, want := range map[string]bool{
		"v0.7.13":              true,
		"v0.4.10-beta.4":       true,
		"0.5.10":               true, // Releases before the tags carry no v, and AwesomeVersion ranks either.
		"v0.5.10_20260922":     true, // Build detail, which Home Assistant truncates at the underscore.
		"dot-v0.5.10":          false,
		"spot-v0.4.10":         false,
		"":                     false,
		"v0.5":                 false,
		"dev":                  false,
		"TECHO5 v0.5.10":       false,
		"v0.5.10 (deadbeef)":   false,
		"refs/tags/dot-v0.5.1": false,
	} {
		err := Manifest{Version: version, Binaries: binaries}.Valid()
		switch {
		case want && err != nil:
			t.Errorf("%q was refused: %v", version, err)
		case !want && err == nil:
			t.Errorf("%q was accepted, and would reach Home Assistant", version)
		case !want && version != "" && !strings.Contains(err.Error(), version):
			t.Errorf("%q was refused without saying which version: %v", version, err)
		}
	}
}

// The other half of the same trap: the version a device reports as running is stamped in at build
// time, and an unstamped build falls back to layout.Version's default. If that default cannot be
// ranked either, every hand-built daemon shows a card no install can clear.
func TestTheDefaultVersionStampCanBeRanked(t *testing.T) {
	if err := ValidVersion(layout.Version); err != nil {
		t.Errorf("the default stamp %q would leave an update card on for good: %v", layout.Version, err)
	}
}

// Jarvis Show deliberately refuses manifests that do not name this product and this exact first-gen
// board, even when their CPU architecture happens to match.
func TestServesOnlyJarvisShowIdentityForThisBoard(t *testing.T) {
	if slotSystem() {
		t.Skip("running on a slot device")
	}
	restoreArch, restoreBoard := arch, deviceBoard
	t.Cleanup(func() { arch, deviceBoard = restoreArch, restoreBoard })
	arch, deviceBoard = "arm", "crown"

	show := Manifest{Product: JarvisShowProduct, Boards: []string{"crown", "checkers"}, Version: "0.0.9", Binaries: map[string]Binary{
		"arm": {URL: "https://example/echod-arm", SHA256: strings.Repeat("b", 64), Size: 22 << 20},
	}}
	if !show.Serves() {
		t.Error("a Crown-capable Jarvis Show release was not offered to Crown")
	}
	show.Boards = []string{"checkers"}
	if show.Serves() {
		t.Error("a Checkers-only release was offered to Crown")
	}
	show.Boards = []string{"crown"}
	show.Product = "techo5"
	if show.Serves() {
		t.Error("a different product identity was offered to Crown")
	}
}

func TestValidForBindsProductAndBoard(t *testing.T) {
	base := Manifest{Product: JarvisShowProduct, Boards: []string{"crown", "checkers"}, Version: "v1.2.3"}
	if err := base.ValidFor(JarvisShowProduct, "crown"); err != nil {
		t.Fatalf("valid Crown identity refused: %v", err)
	}
	if err := base.ValidFor(JarvisShowProduct, "checkers"); err != nil {
		t.Fatalf("valid Checkers identity refused: %v", err)
	}

	for name, mutate := range map[string]func(*Manifest){
		"wrong product":       func(m *Manifest) { m.Product = "techo5" },
		"no boards":           func(m *Manifest) { m.Boards = nil },
		"wrong board":         func(m *Manifest) { m.Boards = []string{"checkers"} },
		"only unknown boards": func(m *Manifest) { m.Boards = []string{"rooks"} },
		"duplicate board":     func(m *Manifest) { m.Boards = []string{"crown", "crown"} },
		"empty device board":  func(m *Manifest) {},
	} {
		m := base
		m.Boards = append([]string(nil), base.Boards...)
		mutate(&m)
		board := "crown"
		if name == "empty device board" {
			board = ""
		}
		if err := m.ValidFor(JarvisShowProduct, board); err == nil {
			t.Errorf("%s: invalid identity accepted", name)
		}
	}
}

// manifest-v2.json names every board of its release, newer ones too (GENBU-191), so a board this build
// doesn't know is passed over: what matters is whether the manifest names this Show's own board.
func TestValidForPassesOverBoardsItDoesNotKnow(t *testing.T) {
	m := Manifest{Product: JarvisShowProduct, Boards: []string{"rooks", "crown"}, Version: "v1.2.3"}
	if err := m.ValidFor(JarvisShowProduct, "crown"); err != nil {
		t.Fatalf("a manifest naming crown and a newer board was refused on crown: %v", err)
	}
	if err := m.ValidFor(JarvisShowProduct, "checkers"); err == nil {
		t.Fatal("a manifest not naming checkers was taken on checkers")
	}
}

// Assets arrived after devices were in the field, so the compatibility runs both ways: a release
// published before it exists carries none, and a daemon reading one must go on updating exactly as it
// did. Nothing in the update path consults them — they are there for the installers.
func TestAManifestFromBeforeAssetsIsUnaffected(t *testing.T) {
	m := Manifest{
		Version:  "0.7.14",
		URL:      "https://example/echod-arm",
		SHA256:   strings.Repeat("a", 64),
		Size:     22 << 20,
		Binaries: map[string]Binary{"arm": {URL: "https://example/echod-arm", SHA256: strings.Repeat("a", 64), Size: 22 << 20}},
	}
	if err := m.Valid(); err != nil {
		t.Fatal(err)
	}
	if _, ok := m.Asset("techo5-boot-v0.7.14.img"); ok {
		t.Error("a release naming no assets answered for one")
	}
	if _, err := m.For("arm"); err != nil {
		t.Errorf("the arm build is no longer offered: %v", err)
	}
}

// And an entry that is there but half filled in is refused where the manifest is written, so a release
// cannot publish cover an installer would take for real.
func TestAHalfFilledAssetIsRefused(t *testing.T) {
	base := Manifest{
		Version:  "0.7.15",
		URL:      "https://example/echod-arm",
		SHA256:   strings.Repeat("a", 64),
		Size:     22 << 20,
		Binaries: map[string]Binary{"arm": {URL: "https://example/echod-arm", SHA256: strings.Repeat("a", 64), Size: 22 << 20}},
	}
	good := Binary{URL: "https://example/techo5-spot-rescue.tar", SHA256: strings.Repeat("b", 64), Size: 4096}

	base.Assets = map[string]Binary{"techo5-spot-rescue.tar": good}
	if err := base.Valid(); err != nil {
		t.Fatalf("a complete asset entry was refused: %v", err)
	}

	for what, b := range map[string]Binary{
		"no url":    {SHA256: good.SHA256, Size: good.Size},
		"no sha256": {URL: good.URL, Size: good.Size},
		"no size":   {URL: good.URL, SHA256: good.SHA256},
	} {
		base.Assets = map[string]Binary{"techo5-spot-rescue.tar": b}
		if err := base.Valid(); err == nil {
			t.Errorf("an asset entry with %s was accepted", what)
		} else if !strings.Contains(err.Error(), "techo5-spot-rescue.tar") {
			t.Errorf("%s: refused without naming the file: %v", what, err)
		}
	}
}
