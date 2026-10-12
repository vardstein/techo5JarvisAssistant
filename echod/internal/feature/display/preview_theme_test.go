//go:build !dot && !spot

package display

import "os"

// SHOW_THEME names a preset to draw the previews in (SHOW_PREVIEW), so a look can be judged in the
// theme a Show actually starts with rather than the package's own colors.
func init() {
	if name := os.Getenv("SHOW_THEME"); name != "" {
		applyTheme(themes[themeIndex(name)])
	}
}
