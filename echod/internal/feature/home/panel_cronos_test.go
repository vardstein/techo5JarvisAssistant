//go:build !dot && !spot

package home

import (
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/board"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

// The panel sizes are the catalogue's displays (internal/board), board by board.
func TestPanelSizeIsTheCatalogues(t *testing.T) {
	known, err := board.Known()
	if err != nil {
		t.Fatal(err)
	}
	was := layout.Board
	t.Cleanup(func() { layout.Board = was })
	for id, b := range known {
		layout.Board = id
		if w, h := panelSize(); w != b.Display.Width || h != b.Display.Height {
			t.Errorf("%s: %dx%d, the catalogue %dx%d", id, w, h, b.Display.Width, b.Display.Height)
		}
	}
}
