// Package board reads board.json, what a Show keeps of its own board: written at install from the
// board catalogue (tools/jarvis_crown/catalogue.py, on_show), next to the profile, and kept by a
// factory reset. boards/ holds every board's as the catalogue makes it; a Python test holds the two
// equal (tests/test_board_json.py), so regenerate them rather than edit them.
//
// Nothing decides anything by it yet (GENBU-163 stage c, step 1): echod reads it beside its own guess
// (layout.Board) and logs whether the two agree. Only once that has been seen on units in use do the
// guess and the constants go.
package board

import (
	"bytes"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"path"
	"strings"
)

// Board is board.json. A key it does not list is refused, as the catalogue refuses one.
type Board struct {
	ID        string   `json:"id"`
	Model     string   `json:"model"`
	ProfileID string   `json:"profile_id"`
	Identity  Identity `json:"identity"`
	Display   Display  `json:"display"`
}

// Identity is what the unit calls itself at each stage. A key nobody has captured is left out.
type Identity struct {
	FastbootProduct string `json:"fastboot_product,omitempty"`
	TWRPDevice      string `json:"twrp_device,omitempty"`
	// AndroidProduct is androidboot.product on the kernel command line, which the running unit
	// sees.
	AndroidProduct string `json:"android_product,omitempty"`
}

// Display is the panel's own size.
type Display struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

// Hardware is the model without its codename: "Echo Show 5 2nd gen" of "Echo Show 5 2nd gen / C76N8S".
func (b Board) Hardware() string {
	h, _, _ := strings.Cut(b.Model, " / ")
	return h
}

//go:embed boards/*.json
var files embed.FS

// Parse reads one board.json and refuses anything that is not exactly one.
func Parse(data []byte) (Board, error) {
	var b Board
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(&b); err != nil {
		return Board{}, err
	}
	if _, err := d.Token(); err != io.EOF {
		return Board{}, errors.New("more than one value")
	}
	switch {
	case b.ID == "":
		return Board{}, errors.New("no id")
	case b.Model == "":
		return Board{}, errors.New("no model")
	case b.ProfileID == "":
		return Board{}, errors.New("no profile_id")
	case b.Display.Width <= 0 || b.Display.Height <= 0:
		return Board{}, errors.New("no display size")
	}
	return b, nil
}

// Load reads board.json at p.
func Load(p string) (Board, error) {
	data, err := os.ReadFile(p)
	if err != nil {
		return Board{}, err
	}
	b, err := Parse(data)
	if err != nil {
		return Board{}, fmt.Errorf("%s: %w", p, err)
	}
	return b, nil
}

// Known is every board this build carries, by id.
func Known() (map[string]Board, error) {
	names, err := fs.Glob(files, "boards/*.json")
	if err != nil {
		return nil, err
	}
	out := make(map[string]Board, len(names))
	for _, n := range names {
		data, err := files.ReadFile(n)
		if err != nil {
			return nil, err
		}
		b, err := Parse(data)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", n, err)
		}
		if b.ID+".json" != path.Base(n) {
			return nil, fmt.Errorf("%s holds board %q", n, b.ID)
		}
		out[b.ID] = b
	}
	return out, nil
}

// What Note found.
const (
	Match      = "match"
	Differs    = "differs"
	None       = "none"
	Unreadable = "unreadable"
)

// Note reads board.json at p, logs how it stands against echod's own guess of the board and what the
// guess calls the hardware, and returns which of the four it was. It changes nothing.
func Note(p, guess, hardware string) string {
	b, err := Load(p)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		slog.Info("board.json", "state", None, "guess", guess)
		return None
	case err != nil:
		slog.Warn("board.json", "state", Unreadable, "guess", guess, "err", err)
		return Unreadable
	case b.ID != guess || b.Hardware() != hardware:
		slog.Warn("board.json", "state", Differs, "board", b.ID, "guess", guess,
			"hardware", b.Hardware(), "guess_hardware", hardware)
		return Differs
	}
	slog.Info("board.json", "state", Match, "board", b.ID)
	return Match
}
