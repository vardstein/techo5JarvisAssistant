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
