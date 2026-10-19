//go:build !dot && !spot

package display

import (
	"fmt"
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

// At every menu size, on both panels, the keyboard has every key, each inside the panel and none on
// top of another, in letters and in symbols.
func TestKeyboardHasEveryKey(t *testing.T) {
	t.Cleanup(func() { menuZoom.Store(100) })
	net := wifi.Network{SSID: "Neighbours", Secured: true}
	for _, m := range menuSizes {
		useMenuSize(menuSizeIndexOf(m.percent))
		for _, panel := range []image.Point{{showWide, showHigh}, {show8Wide, show8High}} {
			for _, symbols := range []bool{false, true} {
				img := image.NewRGBA(image.Rect(0, 0, panel.X, panel.Y))
				r := newRenderer(img)
				r.draw(scene{phase: "idle", showWifi: true, wifi: wifiState{pick: &net, symbols: symbols}})
				what := fmt.Sprintf("%d%% %v symbols=%v", m.percent, panel, symbols)
				keys := map[string]image.Rectangle{}
				for _, z := range r.zones {
					if k, ok := strings.CutPrefix(z.id, "key:"); ok {
						if !z.r.In(img.Rect) {
							t.Errorf("%s: key %q is off the panel: %v", what, k, z.r)
						}
						keys[k] = z.r
					}
				}
				rows := keyRowsLetters
				want := []string{"shift", "backspace", "symbols", "space", "join"}
				if symbols {
					rows, want = keyRowsSymbols, want[1:]
				}
				for _, k := range append(strings.Split(strings.Join(rows, ""), ""), want...) {
					if _, ok := keys[k]; !ok {
						t.Errorf("%s: no key %q", what, k)
					}
				}
				for a, ra := range keys {
					for b, rb := range keys {
						if a < b && ra.Inset(1).Overlaps(rb.Inset(1)) {
							t.Errorf("%s: keys %q and %q overlap", what, a, b)
						}
					}
				}
			}
		}
	}
}

// A tap lands on the device or network drawn there, found by its address or name rather than its
// place, and the arrow is the way back.
func TestPageTapsFollowTheDrawing(t *testing.T) {
	devices := []btaudio.Device{{Address: "a", Name: "Stranger"}, {Address: "d", Name: "Speaker", Paired: true, Connected: true}}
	nets := []wifi.Network{{SSID: "HomeWiFi", Signal: -48, Secured: true}, {SSID: "Cafe guest", Signal: -78}}
	for name, c := range map[string]struct {
		s    scene
		want []string
	}{
		"bluetooth": {scene{phase: "idle", bt: btaudio.State{Available: true, Pairing: true, Devices: devices}}, []string{"d", "a"}},
		"wifi":      {scene{phase: "idle", showWifi: true, wifi: wifiState{nets: nets}}, []string{"HomeWiFi", "Cafe guest"}},
	} {
		r := testRenderer()
		r.draw(c.s)
		var got []string
		for _, z := range r.zones {
			if z.kind == zoneRow {
				hit, ok := r.zoneAt((z.r.Min.X+z.r.Max.X)/2, (z.r.Min.Y+z.r.Max.Y)/2)
				if !ok || hit.id != z.id {
					t.Errorf("%s: a tap on %q found %+v", name, z.id, hit)
				}
				got = append(got, z.id)
			}
		}
		if strings.Join(got, ",") != strings.Join(c.want, ",") {
			t.Errorf("%s: rows %q, want %q", name, got, c.want)
		}
		if z, ok := r.zoneAt(r.cz(60), r.cz(carHeader)/2); !ok || z.kind != zoneBack {
			t.Errorf("%s: the arrow is %+v", name, z)
		}
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
		"wifi-saved": {now: at, phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: nets, scroll: 1 << 20,
			saved: []string{"HomeWiFi", "Grandma's", "Office"}, forget: "Office", forgetAt: at}},
		"wifi-saved-in-use": {now: at, phase: "idle", showWifi: true, wifi: wifiState{status: home, nets: nets, scroll: 1 << 20,
			saved: []string{"HomeWiFi", "Grandma's", "Office"}, forget: "HomeWiFi", forgetAt: at}},
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
