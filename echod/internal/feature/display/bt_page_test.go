//go:build !dot && !spot

package display

import (
	"image"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wifi"
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

// With SHOW_PREVIEW set, the Bluetooth and Wi-Fi pages are written there, on both panels, in
// English and German.
func TestBluetoothPagePreview(t *testing.T) {
	dir := os.Getenv("SHOW_PREVIEW")
	if dir == "" {
		t.Skip("SHOW_PREVIEW not set")
	}
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	t.Cleanup(i18n.Changed)
	at := time.Date(2026, 9, 18, 15, 0, 0, 0, time.Local)
	bt := btaudio.State{Available: true, Pairing: true, Status: "Put your earbuds in pairing mode",
		Remembered: "Speaker", Connected: "Speaker", Devices: []btaudio.Device{
			{Name: "Loud stranger", RSSI: -40},
			{Name: "Old earbuds", Paired: true, Busy: true, RSSI: -60},
			{Name: "Speaker", Paired: true, Connected: true, RSSI: -70},
		}}
	nets := []wifi.Network{{SSID: "HomeWiFi", Signal: -48, Secured: true}, {SSID: "Neighbours", Signal: -66, Secured: true},
		{SSID: "Cafe guest", Signal: -78}, {SSID: "Printer-3F", Signal: -84, Secured: true}}
	home := wifi.Status{SSID: "HomeWiFi", Connected: true, Address: "10.0.0.50", State: "COMPLETED"}
	scenes := map[string]scene{
		"bluetooth-page":   {now: at, phase: "idle", bt: bt},
		"bluetooth-empty":  {now: at, phase: "idle", bt: btaudio.State{Available: true, Pairing: true}},
		"wifi-page":        {now: at, phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: nets}},
		"wifi-page-more":   {now: at, phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: append(append([]wifi.Network{}, nets...), nets...)}},
		"wifi-keyboard":    {now: at, phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: nets, pick: &nets[1], text: "hunter2"}},
		"wifi-keyboard-up": {now: at, phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: nets, pick: &nets[1], shift: true, err: "wrong password"}},
	}
	for _, lang := range []string{"", "de"} {
		if err := config.Set().Screen().Language(lang); err != nil {
			t.Fatal(err)
		}
		i18n.Changed()
		for name, s := range scenes {
			for _, panel := range []struct {
				name       string
				wide, high int
			}{{"", showWide, showHigh}, {"-show8", show8Wide, show8High}} {
				img := image.NewRGBA(image.Rect(0, 0, panel.wide, panel.high))
				newRenderer(img).draw(s)
				writePNG(t, filepath.Join(dir, name+panel.name+map[string]string{"": "", "de": "-de"}[lang]+".png"), img)
			}
		}
	}
}
