//go:build !dot && !spot

package display

import (
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"strings"

	"golang.org/x/image/font"
)

// Text helpers the overlays share, and the strip's place along the bottom (render_turnstrip.go).
// The announcement and the reminder themselves are modals now (render_modal.go).

const (
	// announceBar is how tall the voice turn's strip is, and announceInset how far in from the edges it
	// sits.
	announceBar   = 132
	announceInset = 28

	// ellipsis marks a message that did not fit, so a sentence that stops reads as one that was cut
	// rather than one that ended there.
	ellipsis = "…"
)

// clipText is as much of s as fits in width, with an ellipsis when that is not all of it.
//
// A voice announcement carries its own message and the words are the footnote, so one line is the
// right amount of room. An automation sending a paragraph gets the start of it and a mark saying
// there was more, which beats both a wall of text and a sentence that simply stops.
func clipText(r *renderer, f font.Face, s string, width int) string {
	s = i18n.T(s)
	if r.width(f, s) <= width {
		return s
	}
	run := []rune(s)
	for len(run) > 0 {
		run = run[:len(run)-1]
		// Cut back to a space where there is one near the end, so the last word is whole.
		cut := string(run)
		if r.width(f, cut+ellipsis) <= width {
			if i := strings.LastIndex(strings.TrimRight(cut, " "), " "); i > len(cut)-12 && i > 0 {
				cut = cut[:i]
			}
			return strings.TrimRight(cut, " ") + ellipsis
		}
	}
	return ellipsis
}
