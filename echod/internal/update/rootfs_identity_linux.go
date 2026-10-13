//go:build linux

package update

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"os"
)

const rootfsIdentityPath = "etc/jarvis-show-release.json"

type rootfsIdentity struct {
	Product string   `json:"product"`
	Boards  []string `json:"boards"`
	// MoreBoards are boards added after the first release. Shows from before them ignore this field and
	// would refuse the rootfs if they were in Boards.
	MoreBoards []string `json:"more_boards"`
	Version    string   `json:"version"`
}

// verifyRootfsIdentity checks the identity inside a fully downloaded, already hash-verified rootfs
// before slotctl can unpack it into the inactive slot. This catches release-tooling mistakes where a
// correctly signed manifest accidentally points at a rootfs for another product/board/version.
func verifyRootfsIdentity(path string, m Manifest) error {
	f, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("update: opening downloaded rootfs identity: %w", err)
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("update: rootfs is not a gzip archive: %w", err)
	}
	defer gz.Close()

	tr := tar.NewReader(gz)
	var identity *rootfsIdentity
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("update: reading rootfs archive: %w", err)
		}
		name := h.Name
		for len(name) >= 2 && name[:2] == "./" {
			name = name[2:]
		}
		if name != rootfsIdentityPath {
			continue
		}
		if identity != nil {
			return fmt.Errorf("update: rootfs contains %s more than once", rootfsIdentityPath)
		}
		if h.Typeflag != tar.TypeReg || h.Size <= 0 || h.Size > 4096 {
			return fmt.Errorf("update: %s is not a small regular file", rootfsIdentityPath)
		}
		b, err := io.ReadAll(io.LimitReader(tr, 4097))
		if err != nil {
			return fmt.Errorf("update: reading %s: %w", rootfsIdentityPath, err)
		}
		var id rootfsIdentity
		if err := json.Unmarshal(b, &id); err != nil {
			return fmt.Errorf("update: invalid %s: %w", rootfsIdentityPath, err)
		}
		identity = &id
	}
	if identity == nil {
		return fmt.Errorf("update: rootfs has no %s identity marker", rootfsIdentityPath)
	}
	identityManifest := Manifest{
		Product: identity.Product,
		Boards:  append(append([]string(nil), identity.Boards...), identity.MoreBoards...),
		Version: m.Version,
	}
	if err := identityManifest.ValidFor(JarvisShowProduct, deviceBoard); err != nil {
		return fmt.Errorf("update: rootfs identity rejected: %w", err)
	}
	if identity.Version != m.Version {
		return fmt.Errorf("update: rootfs version %q does not match signed manifest %q", identity.Version, m.Version)
	}
	return nil
}
