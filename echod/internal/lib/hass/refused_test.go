package hass

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/gorilla/websocket"
)

// fakeAuth is a Home Assistant that knows one token: a 401 on REST and auth_invalid on the websocket
// for any other. calls counts every request that reached it, the websocket's included.
func fakeAuth(t *testing.T, good string, calls *atomic.Int32) *httptest.Server {
	up := websocket.Upgrader{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/api/websocket" {
			if r.Header.Get("Authorization") != "Bearer "+good {
				http.Error(w, "401: Unauthorized", http.StatusUnauthorized)
				return
			}
			_, _ = w.Write([]byte(`{"state":"on","attributes":{}}`))
			return
		}
		c, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		_ = c.WriteJSON(map[string]string{"type": "auth_required"})
		var auth map[string]string
		if c.ReadJSON(&auth) != nil {
			return
		}
		if auth["access_token"] != good {
			_ = c.WriteJSON(map[string]string{"type": "auth_invalid", "message": "Invalid access token or password"})
			return
		}
		_ = c.WriteJSON(map[string]string{"type": "auth_ok"})
	}))
	t.Cleanup(srv.Close)
	return srv
}

// TestATurnedAwayTokenIsNotSentAgain is GENBU-158: once Home Assistant answers 401, nothing more goes
// out with that token, not after a restart either, until a new one is given.
func TestATurnedAwayTokenIsNotSentAgain(t *testing.T) {
	defer func(was string) { Path = was }(Path)
	Path = filepath.Join(t.TempDir(), "hass.json")
	var calls atomic.Int32
	srv := fakeAuth(t, "new", &calls)

	c := &Client{http: srv.Client()}
	if err := c.Set(srv.URL, "old"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.State("light.hall"); err == nil {
		t.Fatal("a token Home Assistant does not know was taken")
	}
	if !c.Refused() || c.Ready() || c.Token() != "" {
		t.Fatalf("after a 401: refused %v, ready %v, token %q", c.Refused(), c.Ready(), c.Token())
	}
	if _, err := c.State("light.hall"); !errors.Is(err, ErrRefused) {
		t.Fatalf("second call: %v, want ErrRefused", err)
	}
	if _, err := c.Stream(context.Background(), "/api/camera_proxy_stream/camera.door"); !errors.Is(err, ErrRefused) {
		t.Fatalf("stream: %v, want ErrRefused", err)
	}
	if _, err := c.Render("{{ 1 }}"); !errors.Is(err, ErrRefused) {
		t.Fatalf("websocket: %v, want ErrRefused", err)
	}
	if n := calls.Load(); n != 1 {
		t.Fatalf("%d requests reached Home Assistant, want the one that was turned away", n)
	}

	// A restart reads the refusal back and still sends nothing.
	b, err := os.ReadFile(Path)
	if err != nil {
		t.Fatal(err)
	}
	var kept access
	if err := json.Unmarshal(b, &kept); err != nil || !kept.refused() {
		t.Fatalf("the refusal was not kept: %s (%v)", b, err)
	}

	// The same token handed over again stays unused.
	if err := c.Set(srv.URL, "old"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.State("light.hall"); !errors.Is(err, ErrRefused) || calls.Load() != 1 {
		t.Fatalf("the same token again: %v after %d requests", err, calls.Load())
	}

	// A new token is used at once, and a late answer about the old one changes nothing.
	if err := c.Set(srv.URL, "new"); err != nil {
		t.Fatal(err)
	}
	c.refuse("old")
	if !c.Ready() || c.Refused() || c.Token() != "new" {
		t.Fatalf("with a new token: ready %v, refused %v", c.Ready(), c.Refused())
	}
	if _, err := c.State("light.hall"); err != nil {
		t.Fatal(err)
	}
	// The fake has no render_template and closes after auth_ok; what matters is that it was reached.
	before := calls.Load()
	if _, err := c.Render("{{ 1 }}"); errors.Is(err, ErrRefused) || calls.Load() == before || c.Refused() {
		t.Fatalf("websocket with a new token: %v", err)
	}
}

// TestAWebsocketTurnAwayCounts: auth_invalid on the websocket is the same answer as a 401.
func TestAWebsocketTurnAwayCounts(t *testing.T) {
	defer func(was string) { Path = was }(Path)
	Path = filepath.Join(t.TempDir(), "hass.json")
	var calls atomic.Int32
	srv := fakeAuth(t, "new", &calls)
	c := &Client{http: srv.Client()}
	if err := c.Set(srv.URL, "old"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Render("{{ 1 }}"); err == nil {
		t.Fatal("a token Home Assistant does not know was taken")
	}
	if !c.Refused() {
		t.Fatal("auth_invalid did not count as a refusal")
	}
	if _, err := c.State("light.hall"); !errors.Is(err, ErrRefused) || calls.Load() != 1 {
		t.Fatalf("after auth_invalid: %v after %d requests", err, calls.Load())
	}
}

// TestOtherErrorsAreNotARefusal: a 403 (an admin-only path, or an address Home Assistant banned) or
// a 500 says nothing about the token, and must not stop the device for good.
func TestOtherErrorsAreNotARefusal(t *testing.T) {
	defer func(was string) { Path = was }(Path)
	Path = filepath.Join(t.TempDir(), "hass.json")
	for _, code := range []int{http.StatusForbidden, http.StatusInternalServerError, http.StatusNotFound} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, http.StatusText(code), code)
		}))
		c := &Client{http: srv.Client()}
		if err := c.Set(srv.URL, "tok"); err != nil {
			t.Fatal(err)
		}
		_, _ = c.State("light.hall")
		if c.Refused() {
			t.Errorf("a %d counted as a refusal", code)
		}
		srv.Close()
	}
}
