package api

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"
)

// adoptFiles points the window at a temporary folder with a device key in it.
func adoptFiles(t *testing.T) esphome.PSK {
	t.Helper()
	dir := t.TempDir()
	oldKey, oldAdopt, oldSetup := keyFile, adoptPath, setupPath
	keyFile, adoptPath = filepath.Join(dir, "psk"), filepath.Join(dir, "adopt-until")
	setupPath = filepath.Join(dir, "setup-mode")
	t.Cleanup(func() { keyFile, adoptPath, setupPath = oldKey, oldAdopt, oldSetup })
	var k esphome.PSK
	k[0] = 9
	if err := writePSK(keyFile, k); err != nil {
		t.Fatal(err)
	}
	return k
}

func newTestAPI() *API { return &API{reconnect: make(chan struct{}, 1)} }

// Opening the window takes the key away and serves with none, until the time it says.
func TestOpeningTheWindowServesWithNoKey(t *testing.T) {
	adoptFiles(t)
	a := newTestAPI()
	until, err := a.OpenAdoption()
	if err != nil {
		t.Fatal(err)
	}
	if d := time.Until(until); d < adoptWindow-time.Minute || d > adoptWindow {
		t.Errorf("the window shuts in %v, want about %v", d, adoptWindow)
	}
	if k, err := loadPSK(keyFile); err != nil || !k.IsZero() {
		t.Errorf("after opening, the key read back is %v (%v), want none", k, err)
	}
	if k := a.takeNextPSK(); k == nil || !k.IsZero() {
		t.Errorf("the server was given %v, want the zero key", k)
	}
	if a.AdoptionOpenUntil().IsZero() {
		t.Error("the window does not say it is open")
	}
}

// A Home Assistant setting the key in the window: kept, and the window is over.
func TestAKeySetInTheWindowIsKept(t *testing.T) {
	adoptFiles(t)
	a := newTestAPI()
	if _, err := a.OpenAdoption(); err != nil {
		t.Fatal(err)
	}
	a.takeNextPSK() // the server has taken the zero key
	var theirs esphome.PSK
	theirs[5] = 42
	if err := a.keySet(theirs); err != nil {
		t.Fatal(err)
	}
	if k, err := loadPSK(keyFile); err != nil || *k != theirs {
		t.Errorf("the key kept is %v (%v), want the one Home Assistant set", k, err)
	}
	if !a.AdoptionOpenUntil().IsZero() {
		t.Error("the window is still open after a key was set")
	}
}

// A restart: a window past its time is shut with a new key, one still open stays open, and a key set
// before the restart leaves nothing to shut. A device with no window keeps what it has.
func TestARestartShutsAWindowPastItsTime(t *testing.T) {
	adoptFiles(t)
	a := newTestAPI()
	zero := esphome.Unprovisioned()

	if got := a.resumeAdoption(zero); got != zero {
		t.Error("a device with no window open had its key changed")
	}

	write := func(at time.Time) {
		if err := os.WriteFile(adoptPath, []byte(at.UTC().Format(time.RFC3339)+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(time.Now().Add(-time.Minute))
	got := a.resumeAdoption(zero)
	if got.IsZero() {
		t.Fatal("a window past its time was left open")
	}
	if k, err := loadPSK(keyFile); err != nil || *k != *got {
		t.Errorf("the new key was not kept (%v)", err)
	}
	if _, err := os.Stat(adoptPath); !os.IsNotExist(err) {
		t.Error("the window's time was left behind")
	}

	write(time.Now().Add(time.Hour))
	if got := a.resumeAdoption(zero); !got.IsZero() {
		t.Error("a window still open was shut early")
	}

	var set esphome.PSK
	set[1] = 3
	if got := a.resumeAdoption(&set); *got != set {
		t.Error("a key set before the restart was replaced")
	}
	if _, err := os.Stat(adoptPath); !os.IsNotExist(err) {
		t.Error("a window a key was set in was kept")
	}
}

// Setup mode: a window puts the device in it, the key Home Assistant sets takes it out, and a client
// that gets as far as subscribing (only one with the key can) takes it out as well.
func TestSetupModeLastsUntilAHomeAssistantHasTheKey(t *testing.T) {
	adoptFiles(t)
	a := newTestAPI()
	if a.InSetup() {
		t.Fatal("a device with a key and no mark is in setup mode")
	}
	if _, err := a.OpenAdoption(); err != nil {
		t.Fatal(err)
	}
	if !a.InSetup() {
		t.Error("opening the window did not put the device in setup mode")
	}
	a.takeNextPSK()
	var theirs esphome.PSK
	theirs[1] = 7
	if err := a.keySet(theirs); err != nil {
		t.Fatal(err)
	}
	if a.InSetup() {
		t.Error("still in setup mode after Home Assistant set the key")
	}

	if err := markSetup(); err != nil {
		t.Fatal(err)
	}
	clearSetup()
	clearSetup() // and again, with nothing to clear
	if a.InSetup() {
		t.Error("clearing the mark left the device in setup mode")
	}
}

// In setup mode with a key nobody holds, the device keeps off mDNS; with a window open it is
// advertised as one that takes a key, and outside setup mode as an ordinary encrypted device.
func TestTheAdvertKeepsQuietUntilTheDeviceCanBeAdded(t *testing.T) {
	adoptFiles(t)
	a := newTestAPI()
	if show, unkeyed := a.advertMode(); !show || unkeyed {
		t.Errorf("a set-up device: show %v unkeyed %v, want shown and keyed", show, unkeyed)
	}
	if err := markSetup(); err != nil {
		t.Fatal(err)
	}
	if show, _ := a.advertMode(); show {
		t.Error("a device in setup mode with a key nobody holds is advertised")
	}
	if _, err := a.OpenAdoption(); err != nil {
		t.Fatal(err)
	}
	a.takeNextPSK()
	if show, unkeyed := a.advertMode(); !show || !unkeyed {
		t.Errorf("with the window open: show %v unkeyed %v, want shown and unkeyed", show, unkeyed)
	}
}
