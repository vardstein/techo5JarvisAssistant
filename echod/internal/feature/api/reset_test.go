package api

import (
	"os"
	"path/filepath"
	"testing"
)

// fakeLib writes a boot script that looks for its mark at dir/factory_reset, with the lines given.
func fakeLib(t *testing.T, dir string, lines string) (lib, mark string) {
	t.Helper()
	lib, mark = filepath.Join(dir, "techo5-lib.sh"), filepath.Join(dir, "factory_reset")
	if err := os.WriteFile(lib, []byte("#!/bin/sh\nT5_RESET="+mark+"\n"+lines), 0o644); err != nil {
		t.Fatal(err)
	}
	return lib, mark
}

// The mark goes where the boot script looks, says who asked, and keep-wifi only when asked for.
func TestResetLeavesTheMarkTheBootScriptReads(t *testing.T) {
	for _, keep := range []bool{false, true} {
		lib, mark := fakeLib(t, t.TempDir(), "T5_RESET_KEEPS=wifi\n")
		if err := markReset(lib, keep); err != nil {
			t.Fatal(err)
		}
		got, err := os.ReadFile(mark)
		if err != nil {
			t.Fatal(err)
		}
		want := "home-assistant\n"
		if keep {
			want += "keep-wifi\n"
		}
		if string(got) != want {
			t.Errorf("keep_wifi %v: the mark says %q, want %q", keep, got, want)
		}
		if fi, _ := os.Stat(mark); fi.Mode().Perm() != 0o600 {
			t.Errorf("the mark is %v, want 0600", fi.Mode().Perm())
		}
	}
}

// A system that would not do what was asked is left as it is: no boot script, one without the mark,
// and one that can't keep the Wi-Fi when that was asked for.
func TestResetChangesNothingWhenTheSystemCantDoIt(t *testing.T) {
	dir := t.TempDir()
	if err := markReset(filepath.Join(dir, "gone.sh"), false); err == nil {
		t.Error("no boot script, and the reset went ahead")
	}

	noMark := filepath.Join(dir, "old.sh")
	if err := os.WriteFile(noMark, []byte("#!/bin/sh\nT5_NAME=/data/misc/techo5/name\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := markReset(noMark, false); err == nil {
		t.Error("a boot script with no reset mark, and the reset went ahead")
	}

	lib, mark := fakeLib(t, dir, "")
	if err := markReset(lib, true); err == nil {
		t.Error("keep_wifi on a system that erases the Wi-Fi anyway, and the reset went ahead")
	}
	if _, err := os.Stat(mark); !os.IsNotExist(err) {
		t.Errorf("a mark was left although nothing should have changed (%v)", err)
	}
	if err := markReset(lib, false); err != nil {
		t.Errorf("the same system without keep_wifi: %v", err)
	}
}

// Home Assistant finds the action next to leaving, and gets an answer from it.
func TestResetIsOfferedWithAnAnswer(t *testing.T) {
	for _, a := range (&API{}).Actions() {
		if a.Name == "reset_to_setup" {
			if !a.Answers {
				t.Error("reset_to_setup gives Home Assistant no answer")
			}
			return
		}
	}
	t.Error("reset_to_setup isn't offered")
}
