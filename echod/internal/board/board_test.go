package board

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestKnownHasEveryBoard(t *testing.T) {
	known, err := Known()
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"checkers", "cronos", "crown"} {
		b, ok := known[id]
		if !ok {
			t.Fatalf("no %s in %v", id, known)
		}
		// Today every running unit's androidboot.product is its board's id (ledger C5, K5, R7).
		if b.Identity.AndroidProduct != id || b.ProfileID != "genbu-"+id+"-v1" {
			t.Errorf("%s: %+v", id, b)
		}
	}
	if len(known) != 3 {
		t.Errorf("%d boards", len(known))
	}
}

func TestHardware(t *testing.T) {
	if h := (Board{Model: "Echo Show 5 2nd gen / C76N8S"}).Hardware(); h != "Echo Show 5 2nd gen" {
		t.Errorf("%q", h)
	}
	if h := (Board{Model: "Echo Show"}).Hardware(); h != "Echo Show" {
		t.Errorf("%q", h)
	}
}

const good = `{"id":"cronos","model":"Echo Show 5 2nd gen / C76N8S","profile_id":"genbu-cronos-v1",` +
	`"identity":{"android_product":"cronos"},"display":{"width":960,"height":480}}`

func TestParseRefuses(t *testing.T) {
	if _, err := Parse([]byte(good + "\n")); err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string]string{
		"unknown key":          strings.Replace(good, `"id"`, `"camera":true,"id"`, 1),
		"unknown identity key": strings.Replace(good, `"android_product"`, `"dt_compatible"`, 1),
		"two values":           good + good,
		"trailing":             good + " x",
		"no id":                strings.Replace(good, `"id":"cronos",`, "", 1),
		"no model":             strings.Replace(good, `"model":"Echo Show 5 2nd gen / C76N8S",`, "", 1),
		"no profile":           strings.Replace(good, `"profile_id":"genbu-cronos-v1",`, "", 1),
		"no display":           strings.Replace(good, `"width":960`, `"width":0`, 1),
		"half":                 good[:40],
		"empty":                "",
		"array":                "[]",
	} {
		if _, err := Parse([]byte(data)); err == nil {
			t.Errorf("%s: taken", name)
		}
	}
}

func TestNote(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "board.json")
	if got := Note(p, "cronos", "Echo Show 5 2nd gen"); got != None {
		t.Errorf("missing: %s", got)
	}
	write(t, p, good+"\n")
	if got := Note(p, "cronos", "Echo Show 5 2nd gen"); got != Match {
		t.Errorf("same: %s", got)
	}
	if got := Note(p, "checkers", "Echo Show 5 1st gen"); got != Differs {
		t.Errorf("other board: %s", got)
	}
	if got := Note(p, "cronos", "Echo Show 5 1st gen"); got != Differs {
		t.Errorf("other hardware: %s", got)
	}
	write(t, p, good[:40])
	if got := Note(p, "cronos", "Echo Show 5 2nd gen"); got != Unreadable {
		t.Errorf("half written: %s", got)
	}
}

func write(t *testing.T, p, s string) {
	t.Helper()
	if err := os.WriteFile(p, []byte(s), 0o600); err != nil {
		t.Fatal(err)
	}
}

// cmdline is a unit's kernel command line, cut to the keys that matter, as on the units read 2026-10-19.
func cmdline(product string) string {
	return "console=tty0 androidboot.hardware=mt8163 androidboot.product=" + product + " lcm=1-panel rw"
}

func TestProduct(t *testing.T) {
	for in, want := range map[string]string{
		cmdline("crown"): "crown",
		"androidboot.product=checkers androidboot.product=checkers": "checkers",
		"":                            "",
		"androidboot.hardware=mt8163": "",
		"androidboot.product=":        "",
		"androidboot.product=checkers androidboot.product=cronos": "",
		"xandroidboot.product=crown":                              "",
	} {
		if got := Product(in); got != want {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
}

func TestByProduct(t *testing.T) {
	known, err := Known()
	if err != nil {
		t.Fatal(err)
	}
	for id := range known {
		if b, ok := ByProduct(known, id); !ok || b.ID != id {
			t.Errorf("%s: %v %v", id, b.ID, ok)
		}
	}
	for _, p := range []string{"", "Crown", "crown ", "cro", "mt8163", "rhodes"} {
		if b, ok := ByProduct(known, p); ok {
			t.Errorf("%q: %s", p, b.ID)
		}
	}
	// Two boards claiming one product is no answer either.
	twice := map[string]Board{"a": {ID: "a", Identity: Identity{AndroidProduct: "x"}},
		"b": {ID: "b", Identity: Identity{AndroidProduct: "x"}}}
	if b, ok := ByProduct(twice, "x"); ok {
		t.Errorf("shared product: %s", b.ID)
	}
}

// A unit in use, installed before board.json: it gets the installer's file, byte for byte, and echod's
// guess then agrees with it.
func TestMigrateWritesTheInstallersFile(t *testing.T) {
	known, err := Known()
	if err != nil {
		t.Fatal(err)
	}
	for id, b := range known {
		p := filepath.Join(t.TempDir(), "board.json")
		if got := Migrate(p, cmdline(id)); got != Written {
			t.Fatalf("%s: %s", id, got)
		}
		data, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		want, _ := file(id)
		if string(data) != string(want) {
			t.Errorf("%s: wrote %q", id, data)
		}
		if _, err := os.Stat(p + ".new"); !os.IsNotExist(err) {
			t.Errorf("%s: .new left behind", id)
		}
		if got := Note(p, id, b.Hardware()); got != Match {
			t.Errorf("%s: then %s", id, got)
		}
		// Once is enough: the next start keeps it.
		if got := Migrate(p, cmdline(id)); got != Kept {
			t.Errorf("%s: again %s", id, got)
		}
	}
}

func TestMigrateLeavesTheUnknownAlone(t *testing.T) {
	for name, line := range map[string]string{
		"no product":    "console=tty0 lcm=1-st7701s_wsvga_dsi_vdo_checkers_st_kd_hsd",
		"unknown board": cmdline("rhodes"),
		"near miss":     cmdline("Crown"),
		"two products":  cmdline("crown") + " androidboot.product=cronos",
	} {
		dir := t.TempDir()
		p := filepath.Join(dir, "board.json")
		if got := Migrate(p, line); got != Unidentified {
			t.Errorf("%s: %s", name, got)
		}
		if left, _ := os.ReadDir(dir); len(left) != 0 {
			t.Errorf("%s: left %v", name, left)
		}
	}
}

func TestMigrateKeepsWhatIsThere(t *testing.T) {
	for name, s := range map[string]string{"good": good + "\n", "half": good[:40], "empty": ""} {
		p := filepath.Join(t.TempDir(), "board.json")
		write(t, p, s)
		if got := Migrate(p, cmdline("crown")); got != Kept {
			t.Errorf("%s: %s", name, got)
		}
		if data, _ := os.ReadFile(p); string(data) != s {
			t.Errorf("%s: changed to %q", name, data)
		}
	}
}

// A write that can't happen leaves the unit as it was: no board.json, nothing half written, and echod
// runs on.
func TestMigrateFailsToNothing(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "gone", "board.json")
	if got := Migrate(p, cmdline("cronos")); got != Failed {
		t.Errorf("no directory: %s", got)
	}
	// A .new left by a start that died mid-write is written over.
	p = filepath.Join(dir, "board.json")
	write(t, p+".new", "{")
	if got := Migrate(p, cmdline("cronos")); got != Written {
		t.Errorf("stale .new: %s", got)
	}
	if left, _ := os.ReadDir(dir); len(left) != 1 || left[0].Name() != "board.json" {
		t.Errorf("left %v", left)
	}
}
