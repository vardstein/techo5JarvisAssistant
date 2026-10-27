//go:build !dot && !spot

package home

import "github.com/HuskerMinion/techo5/echod/internal/layout"

// The panel's own size, which is what camera frames and slideshow photos are scaled to fit. Both
// Show 5 generations are 960x480; the Show 8 is 1280x800, and a photo fetched at the Show 5's size
// and drawn on it would be soft and letterboxed.
//
// Variables rather than constants because one build serves all three screens and the board is only
// known at run time. That is also why this file no longer covers the Dot: the board predicates only
// exist where there is a board to tell apart, and the Dot keeps its own copy in panel_dot.go.
var (
	// cameraFrameW and H are the size frames are scaled to fit.
	cameraFrameW, cameraFrameH = panelSize()

	// slideshowW and H are the panel's own size, the same thing by a different name.
	slideshowW, slideshowH = panelSize()

	// radarW and H are the size the rain map is composited at. The radar page draws it one to one,
	// so this has to be the panel or the map does not fill the screen.
	radarW, radarH = panelSize()

	// artW and artH are the size a now-playing background picture is fitted to. The page draws it at
	// one to one, so this has to be the panel.
	artW, artH = panelSize()

	// thumbSide is the cover's square on the now-playing page, which draws it at one to one: as tall
	// as the panel allows with a margin round it, but never so wide that the column of words and
	// buttons beside it gets narrower than the Show 5's 440. The display works it out the same way.
	thumbSide = coverSide()
)

func coverSide() int {
	w, h := panelSize()
	m := 40 * w / 960
	return min(h-2*m, w-3*m-440*w/960)
}

// panelSize is the board's display from its board.json (GENBU-193).
func panelSize() (w, h int) { return layout.Display() }

// localCameraName is the device's own camera on the list, and what "show …" matches.
const localCameraName = "This device"
