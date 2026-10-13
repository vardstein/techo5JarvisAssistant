//go:build linux

package update

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type rootfsEntry struct {
	name     string
	body     []byte
	typeflag byte
}

func testRootfs(t *testing.T, entries ...rootfsEntry) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "rootfs.tar.gz")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for _, entry := range entries {
		typeflag := entry.typeflag
		if typeflag == 0 {
			typeflag = tar.TypeReg
		}
		h := &tar.Header{Name: entry.name, Mode: 0o644, Size: int64(len(entry.body)), Typeflag: typeflag}
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if typeflag == tar.TypeReg {
			if _, err := tw.Write(entry.body); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	return path
}

func marker(t *testing.T, product string, boards []string, version string) []byte {
	t.Helper()
	b, err := json.Marshal(rootfsIdentity{Product: product, Boards: boards, Version: version})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestRootfsIdentityMatchesSignedManifestAndBoard(t *testing.T) {
	restore := deviceBoard
	t.Cleanup(func() { deviceBoard = restore })
	deviceBoard = "crown"
	m := Manifest{Product: JarvisShowProduct, Boards: []string{"crown", "checkers"}, Version: "v1.2.3"}
	path := testRootfs(t,
		rootfsEntry{name: "etc/issue", body: []byte("jarvis")},
		rootfsEntry{name: "./" + rootfsIdentityPath, body: marker(t, JarvisShowProduct, []string{"crown", "checkers"}, "v1.2.3")},
	)
	if err := verifyRootfsIdentity(path, m); err != nil {
		t.Fatalf("valid rootfs refused: %v", err)
	}
}

// Newer boards sit in more_boards, which Shows from before them ignore.
func TestRootfsIdentityReadsMoreBoards(t *testing.T) {
	restore := deviceBoard
	t.Cleanup(func() { deviceBoard = restore })
	body, err := json.Marshal(rootfsIdentity{
		Product: JarvisShowProduct, Boards: []string{"crown", "checkers"}, MoreBoards: []string{"cronos"}, Version: "v1.3.0",
	})
	if err != nil {
		t.Fatal(err)
	}
	path := testRootfs(t, rootfsEntry{name: "./" + rootfsIdentityPath, body: body})
	m := Manifest{Product: JarvisShowProduct, Boards: []string{"cronos"}, Version: "v1.3.0"}
	for _, board := range []string{"crown", "checkers", "cronos"} {
		deviceBoard = board
		if err := verifyRootfsIdentity(path, m); err != nil {
			t.Fatalf("%s refused a rootfs listing it: %v", board, err)
		}
	}
	older := testRootfs(t, rootfsEntry{name: "./" + rootfsIdentityPath, body: marker(t, JarvisShowProduct, []string{"crown", "checkers"}, "v1.3.0")})
	deviceBoard = "cronos"
	if err := verifyRootfsIdentity(older, m); err == nil || !strings.Contains(err.Error(), "does not support this cronos board") {
		t.Fatalf("cronos took a rootfs that doesn't list it: %v", err)
	}
}

func TestRootfsIdentityRefusesConflictingOrAmbiguousArchive(t *testing.T) {
	restore := deviceBoard
	t.Cleanup(func() { deviceBoard = restore })
	deviceBoard = "crown"
	m := Manifest{Product: JarvisShowProduct, Boards: []string{"crown", "checkers"}, Version: "v1.2.3"}
	good := marker(t, JarvisShowProduct, []string{"crown", "checkers"}, "v1.2.3")

	for name, entries := range map[string][]rootfsEntry{
		"missing marker": {{name: "etc/issue", body: []byte("jarvis")}},
		"wrong product":  {{name: rootfsIdentityPath, body: marker(t, "techo5", []string{"crown"}, "v1.2.3")}},
		"wrong board":    {{name: rootfsIdentityPath, body: marker(t, JarvisShowProduct, []string{"checkers"}, "v1.2.3")}},
		"wrong version":  {{name: rootfsIdentityPath, body: marker(t, JarvisShowProduct, []string{"crown"}, "v9.9.9")}},
		"duplicate marker": {
			{name: rootfsIdentityPath, body: good},
			{name: "./" + rootfsIdentityPath, body: good},
		},
		"marker is directory": {{name: rootfsIdentityPath, typeflag: tar.TypeDir}},
	} {
		path := testRootfs(t, entries...)
		if err := verifyRootfsIdentity(path, m); err == nil {
			t.Errorf("%s: invalid rootfs accepted", name)
		}
	}
}

func TestRootfsIdentityRejectsOversizeMarker(t *testing.T) {
	restore := deviceBoard
	t.Cleanup(func() { deviceBoard = restore })
	deviceBoard = "crown"
	m := Manifest{Product: JarvisShowProduct, Boards: []string{"crown"}, Version: "v1.2.3"}
	path := testRootfs(t, rootfsEntry{name: rootfsIdentityPath, body: []byte(strings.Repeat("x", 4097))})
	if err := verifyRootfsIdentity(path, m); err == nil {
		t.Fatal("oversize rootfs identity accepted")
	}
}
