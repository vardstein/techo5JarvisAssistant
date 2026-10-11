//go:build !dot && !spot

package display

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
)

// Bluetooth is one row in Connections on the Show, whatever the speaker is doing, and that row opens
// the Bluetooth page; there is no separate Pair row any more.
func TestBluetoothIsOneRow(t *testing.T) {
	for name, bt := range map[string]btaudio.State{
		"none":       {Available: true},
		"remembered": {Available: true, Remembered: "Speaker"},
		"connected":  {Available: true, Remembered: "Speaker", Connected: "Speaker"},
	} {
		s := sheetScene(catConnections)
		s.bt = bt
		var ids []string
		for _, row := range connectionRows(s.view()) {
			if row.id != "wifi" && row.id != "btproxy" {
				ids = append(ids, row.id)
			}
		}
		if len(ids) != 1 || ids[0] != "bluetooth" {
			t.Errorf("%s: the Bluetooth rows are %q, want just \"bluetooth\"", name, ids)
		}
	}
	if rows := bluetoothRows(btaudio.State{}); len(rows) != 1 || rows[0].id != "" {
		t.Errorf("without Bluetooth the row should only say so: %+v", rows)
	}
}

// The page lists the device connected first, then the paired ones, then what the scan heard, and a
// tap lands on the device drawn there.
func TestBluetoothListOrder(t *testing.T) {
	heard := []btaudio.Device{
		{Address: "a", Name: "Loud stranger", RSSI: -40},
		{Address: "b", Name: "Old earbuds", Paired: true, RSSI: -60},
		{Address: "c", Name: "Quiet stranger", RSSI: -80},
		{Address: "d", Name: "Speaker", Paired: true, Connected: true, RSSI: -70},
	}
	var got []string
	for _, d := range btList(heard) {
		got = append(got, d.Address)
	}
	if want := "dbac"; strings.Join(got, "") != want {
		t.Errorf("order %q, want %q", strings.Join(got, ""), want)
	}
	if heard[0].Address != "a" {
		t.Error("btList changed the state it was given")
	}
}

// With SHOW_PREVIEW set, the page is written there in English and German.
func TestBluetoothPagePreview(t *testing.T) {
	dir := os.Getenv("SHOW_PREVIEW")
	if dir == "" {
		t.Skip("SHOW_PREVIEW not set")
	}
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	t.Cleanup(i18n.Changed)
	s := scene{now: time.Date(2026, 9, 18, 15, 0, 0, 0, time.Local), phase: "idle"}
	s.bt = btaudio.State{Available: true, Pairing: true, Status: "Put your earbuds in pairing mode",
		Remembered: "Speaker", Connected: "Speaker", Devices: []btaudio.Device{
			{Name: "Loud stranger", RSSI: -40},
			{Name: "Old earbuds", Paired: true, RSSI: -60},
			{Name: "Speaker", Paired: true, Connected: true, RSSI: -70},
		}}
	for _, lang := range []string{"", "de"} {
		if err := config.Set().Screen().Language(lang); err != nil {
			t.Fatal(err)
		}
		i18n.Changed()
		writePNG(t, filepath.Join(dir, "bluetooth-page"+map[string]string{"": "", "de": "-de"}[lang]+".png"), frame(s))
	}
}
