package update

import (
	"context"
	"fmt"
)

// A Show that follows its house's version (docs/update-leader.md) is told a release by name rather
// than reading a channel, and may have to go back to it: a Show installed from a newer build than the
// house runs, or one the house moved on without, is brought in line either way. Going back happens only
// here. The channel path stays newer-only.

// AlignFloor is the oldest release a Show may be taken back to by its house. Raise it in the commit that
// fixes a hole a release had: every build from then on refuses to go below it, and mkmanifest writes it
// into the signed manifest as min_align. Empty means no floor yet.
const AlignFloor = ""

// tagURL is where one release's own manifest lives, a variable so a test can serve it.
var tagURL = func(tag string) string { return releases + "/download/" + tag + "/" + manifestName() }

// FetchTag reads one release's own signed manifest. The tag comes from Home Assistant, so it has to be
// a version before it goes into a URL, and the manifest found there has to name that same version:
// a signed manifest from another release is still signed.
func FetchTag(ctx context.Context, tag string) (Manifest, error) {
	if err := ValidVersion(tag); err != nil {
		return Manifest{}, fmt.Errorf("update: the house version %q is not a release name: %w", tag, err)
	}
	m, err := fetch(ctx, tagURL(tag))
	if err != nil {
		return m, err
	}
	if m.Version != tag {
		return Manifest{}, fmt.Errorf("update: the manifest of %s names %s", tag, m.Version)
	}
	return m, nil
}

// Align installs the house's release whether it is newer or older than the one running, as long as it
// is not below the floor. Everything else is what Install checks and does.
func Align(ctx context.Context, m Manifest, progress func(float32)) error {
	return install(ctx, m, progress, func(offered, running string) error {
		return alignable(offered, running, AlignFloor)
	})
}

// alignable is the version rule for Align. Equal is let through as a repair, as notOlder does.
func alignable(offered, running, floor string) error {
	if err := ValidVersion(running); err != nil {
		return fmt.Errorf("update: running version %q cannot be ranked safely; refusing network OTA: %w", running, err)
	}
	if _, ok := compareVersions(offered, running); !ok {
		return fmt.Errorf("update: cannot compare the house's %s with running %s", offered, running)
	}
	if floor == "" {
		return nil
	}
	rank, ok := compareVersions(offered, floor)
	if !ok {
		return fmt.Errorf("update: cannot compare the house's %s with the floor %s", offered, floor)
	}
	if rank < 0 {
		return fmt.Errorf("update: %s is below %s, the oldest release this one may go back to", offered, floor)
	}
	return nil
}
