//go:build !dot && !spot

package layout

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/board"
)

// The command lines are the bootloaders' own, cut down to what matters, from a 1st gen Show 5 and a
// Show 8 running LineageOS (read 2026-09-22); androidboot.product is proven on a running unit of each
// board (evidence ledger C5, K5, R7).
func TestShowBoard(t *testing.T) {
	known, err := board.Known()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	file := func(name, data string) string {
		p := filepath.Join(dir, name)
		if data != "" {
			if err := os.WriteFile(p, []byte(data), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		return p
	}
	crown := file("crown.json", `{"id":"crown","model":"Echo Show 8 1st gen / C7H6N3","profile_id":"genbu-crown-v1",`+
		`"identity":{"android_product":"crown"},"display":{"width":1280,"height":800}}`)
	stranger := file("stranger.json", `{"id":"rhodes","model":"Echo Show 8 2nd gen","profile_id":"genbu-rhodes-v1",`+
		`"display":{"width":1280,"height":800}}`)
	broken := file("broken.json", `{"id":"crown","mod`)
	missing := file("missing.json", "")
	for _, c := range []struct {
		name, p, cmdline, want, from string
	}{
		{"the file decides", crown, "console=tty0 androidboot.product=crown lcm=1-jd936x_wxga_dsi_vdo_crown_st_kd_hsd", "crown", board.FromFile},
		{"the file decides over the command line", crown, "androidboot.product=checkers", "crown", board.FromFile},
		{"the file with nothing on the command line", crown, "", "crown", board.FromFile},
		{"no file, the product", missing, "console=tty0 androidboot.product=checkers lcm=1-st7701s_wsvga_dsi_vdo_checkers_st_kd_hsd", "checkers", board.FromCmdline},
		{"a broken file, the product", broken, "androidboot.product=cronos", "cronos", board.FromCmdline},
		{"a board this build doesn't carry, the product", stranger, "androidboot.product=crown", "crown", board.FromCmdline},
		{"a panel name alone is not gone by", missing, "lcm=1-jd936x_wxga_dsi_vdo_crown_st_kd_hsd", "", ""},
		{"an unknown product", missing, "androidboot.product=rhodes", "", ""},
		{"two products", missing, "androidboot.product=crown androidboot.product=checkers", "", ""},
		{"nothing to go on", missing, "", "", ""},
	} {
		if got, from := showBoard(known, c.p, c.cmdline); got != c.want || from != c.from {
			t.Errorf("%s: %q from %q, want %q from %q", c.name, got, from, c.want, c.from)
		}
	}
}

// Every name and size is the catalogue's own (internal/board), and a unit with no board says so.
func TestNamesAreTheCatalogues(t *testing.T) {
	for id, b := range known {
		if got := hardwareOf(id); got != b.Hardware() {
			t.Errorf("%s: %q, the catalogue %q", id, got, b.Hardware())
		}
		if got, want := modelOf(id), b.Hardware()+" ("+id+")"; got != want {
			t.Errorf("%s: %q, want %q", id, got, want)
		}
	}
	if len(known) != 3 {
		t.Errorf("%d boards", len(known))
	}
	if got := modelOf(""); got != "Echo Show (unknown board)" {
		t.Errorf("no board: %q", got)
	}
}

func TestDisplayIsTheBoards(t *testing.T) {
	was := Board
	t.Cleanup(func() { Board = was })
	for id, b := range known {
		Board = id
		if w, h := Display(); w != b.Display.Width || h != b.Display.Height {
			t.Errorf("%s: %dx%d, the catalogue %dx%d", id, w, h, b.Display.Width, b.Display.Height)
		}
	}
	Board = ""
	if w, h := Display(); w != 960 || h != 480 {
		t.Errorf("no board: %dx%d", w, h)
	}
}

// Only board.json lets a unit update.
func TestOTABoardIsTheFilesAlone(t *testing.T) {
	for _, c := range []struct{ id, from, want string }{
		{"crown", board.FromFile, "crown"},
		{"crown", board.FromCmdline, ""},
		{"", "", ""},
	} {
		if got := otaBoard(c.id, c.from); got != c.want {
			t.Errorf("%q from %q: %q, want %q", c.id, c.from, got, c.want)
		}
	}
}
