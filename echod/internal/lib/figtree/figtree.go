// Package figtree is Figtree, the typeface the Show's menus are set in: a plain geometric sans in
// the manner of the one Spotify's Car Thing used, with the same clear figures at a distance.
//
// The fonts are Figtree 2.0.3 by Erik Kennedy, under the SIL Open Font License 1.1 (OFL.txt).
// Used unmodified.
package figtree

import _ "embed"

// Regular, Medium and Bold are the three weights the menus use, TrueType.
var (
	//go:embed Figtree-Regular.ttf
	Regular []byte
	//go:embed Figtree-Medium.ttf
	Medium []byte
	//go:embed Figtree-Bold.ttf
	Bold []byte
)
