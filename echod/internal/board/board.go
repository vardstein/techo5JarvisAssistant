// Package board reads board.json, what a Show keeps of its own board: written at install from the
// board catalogue (tools/jarvis_crown/catalogue.py, on_show), next to the profile, and kept by a
// factory reset. boards/ holds every board's as the catalogue makes it; a Python test holds the two
// equal (tests/test_board_json.py), so regenerate them rather than edit them.
//
// echod goes by it (GENBU-193): layout.Board is the board it names, and updates go by nothing else.
//
// A unit installed before board.json existed gets one from Migrate (GENBU-191), but only when the
// kernel command line names exactly one board by a key proven on running units. Anything less is
// logged and left alone, so the worst a unit can come to is "no board.json": it keeps running and
// doesn't update.
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
	"path/filepath"
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

// file is the board.json this build carries for board id, byte for byte as the installer writes it.
func file(id string) ([]byte, error) {
	return files.ReadFile("boards/" + id + ".json")
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

// Where echod took its board from (layout.BoardFrom).
const (
	FromFile    = "board.json"
	FromCmdline = "androidboot.product"
)

// What Note found.
const (
	Match      = "match"
	Differs    = "differs"
	None       = "none"
	Unreadable = "unreadable"
	Unknown    = "unknown"
)

// Note logs which board echod goes by, id, and where it took it from, from: FromFile for the file at
// p, FromCmdline for the kernel command line cmdline, "" for nowhere. It returns how the
// file stood: match (the file, and the command line names the same board or none), differs (the
// file, while the command line names another board: the file wins, a person should look), none or
// unreadable (the command line, because the file is missing or no use), or unknown (no board). It
// changes nothing.
func Note(p, cmdline, id, from string) string {
	product := Product(cmdline)
	switch from {
	case "":
		slog.Error("board", "state", Unknown, "android_product", product,
			"note", "no board.json and no board this build carries on the kernel command line: running on base values, no updates")
		return Unknown
	case FromFile:
		known, _ := Known()
		if b, ok := ByProduct(known, product); ok && b.ID != id {
			slog.Warn("board", "state", Differs, "board", id, "android_product", product)
			return Differs
		}
		slog.Info("board", "state", Match, "board", id)
		return Match
	}
	state, err := None, error(nil)
	if _, err = Load(p); err != nil && !errors.Is(err, fs.ErrNotExist) {
		state = Unreadable
	}
	slog.Warn("board", "state", state, "board", id, "from", from, "err", err,
		"note", "no usable board.json: no updates until there is one")
	return state
}

// Product is androidboot.product on the kernel command line cmdline, or "" when it is missing, empty,
// or given twice with two values.
func Product(cmdline string) string {
	product := ""
	for _, f := range strings.Fields(cmdline) {
		v, ok := strings.CutPrefix(f, "androidboot.product=")
		switch {
		case !ok:
		case v == "" || product != "" && v != product:
			return ""
		default:
			product = v
		}
	}
	return product
}

// ByProduct is the one board of known whose android_product is exactly product. Not one, or more than
// one, is no answer.
func ByProduct(known map[string]Board, product string) (Board, bool) {
	var found []Board
	for _, b := range known {
		if product != "" && b.Identity.AndroidProduct == product {
			found = append(found, b)
		}
	}
	if len(found) != 1 {
		return Board{}, false
	}
	return found[0], true
}

// What Migrate did.
const (
	Kept         = "kept"
	Written      = "written"
	Unidentified = "unidentified"
	Failed       = "failed"
)

// Migrate gives a unit with no board.json at p the one its kernel command line names, and returns
// what it did. A file already there, readable or not, is kept as it is: that is the installer's, or a
// person's. A unit that names no board, or one this build doesn't know, gets nothing and is logged
// for a person to identify. It never guesses and never stops echod.
func Migrate(p, cmdline string) string {
	if _, err := os.Lstat(p); !errors.Is(err, fs.ErrNotExist) {
		return Kept
	}
	product := Product(cmdline)
	known, err := Known()
	if err != nil {
		slog.Error("board.json migration", "state", Failed, "err", err)
		return Failed
	}
	b, ok := ByProduct(known, product)
	if !ok {
		slog.Warn("board.json migration", "state", Unidentified, "android_product", product)
		return Unidentified
	}
	data, err := file(b.ID)
	if err == nil {
		err = replace(p, data)
	}
	if err != nil {
		slog.Error("board.json migration", "state", Failed, "board", b.ID, "err", err)
		return Failed
	}
	slog.Info("board.json migration", "state", Written, "board", b.ID)
	return Written
}

// replace puts data at p whole or not at all, as the installer does: beside it first, then renamed over.
func replace(p string, data []byte) error {
	tmp := p + ".new"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(tmp, p)
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	if d, err := os.Open(filepath.Dir(p)); err == nil {
		d.Sync()
		d.Close()
	}
	return nil
}
