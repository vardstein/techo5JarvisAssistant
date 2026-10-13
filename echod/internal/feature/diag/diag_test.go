package diag

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCertificatesCannotBeDisabled(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("hello"))
	}))
	defer srv.Close()

	if _, err := http.Get(srv.URL); err == nil {
		t.Fatal("a certificate nothing trusted signed was accepted")
	}
}

// A temperature sensor is only listed where the board has its thermal zone, so a Show without the
// radio zone doesn't get a sensor that stays unknown.
func TestTemperaturesFollowThermalZones(t *testing.T) {
	d := Get()
	old := thermalZones
	defer func() { thermalZones = old }()
	has := func(e any) bool {
		for _, x := range d.Entities() {
			if x == e {
				return true
			}
		}
		return false
	}

	thermalZones = func() map[string]float64 { return map[string]float64{cpuZone: 40} }
	if !has(d.temperature) || has(d.radioTemp) {
		t.Error("cpu zone only: want the CPU temperature and no radio temperature")
	}
	thermalZones = func() map[string]float64 { return nil }
	if has(d.temperature) || has(d.radioTemp) {
		t.Error("no zones: want no temperature sensors")
	}
	thermalZones = func() map[string]float64 { return map[string]float64{cpuZone: 40, radioZone: 50} }
	if !has(d.temperature) || !has(d.radioTemp) {
		t.Error("both zones: want both temperatures")
	}
}
