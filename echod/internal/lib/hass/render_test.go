package hass

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/gorilla/websocket"
)

// fakeTemplates is Home Assistant rendering templates the way it does for an admin (REST and the
// websocket) or for anyone else (the websocket only).
func fakeTemplates(t *testing.T, admin bool, rest, ws *atomic.Int32) *httptest.Server {
	up := websocket.Upgrader{}
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/template" {
			rest.Add(1)
		}
		if r.Header.Get("Authorization") != "" && r.Header.Get("Authorization") != "Bearer secret" {
			http.Error(w, "401: Unauthorized", http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case "/api/template":
			if !admin {
				http.Error(w, "401: Unauthorized", http.StatusUnauthorized)
				return
			}
			_, _ = w.Write([]byte("Küche\n"))
		case "/api/websocket":
			ws.Add(1)
			c, err := up.Upgrade(w, r, nil)
			if err != nil {
				t.Error(err)
				return
			}
			defer c.Close()
			_ = c.WriteJSON(map[string]string{"type": "auth_required"})
			var auth map[string]string
			if c.ReadJSON(&auth) != nil || auth["access_token"] != "secret" {
				_ = c.WriteJSON(map[string]string{"type": "auth_invalid"})
				return
			}
			_ = c.WriteJSON(map[string]string{"type": "auth_ok"})
			var cmd map[string]any
			if c.ReadJSON(&cmd) != nil || cmd["type"] != "render_template" {
				t.Errorf("command %v", cmd)
				return
			}
			_ = c.WriteJSON(map[string]any{"id": cmd["id"], "type": "result", "success": true, "result": nil})
			_ = c.WriteJSON(map[string]any{"id": 77, "type": "event", "event": map[string]any{"result": "other"}})
			_ = c.WriteJSON(map[string]any{"id": cmd["id"], "type": "event",
				"event": map[string]any{"result": "Küche", "listeners": map[string]any{}}})
		default:
			http.NotFound(w, r)
		}
	}))
}

// Never over REST, for an admin either: a Show's 401 there was a failed login in Home Assistant's eyes,
// with a "Login attempt failed" notification on every boot (seen 2026-10-13).
func TestRenderGoesOverTheWebsocketOnly(t *testing.T) {
	for _, admin := range []bool{false, true} {
		var rest, ws atomic.Int32
		srv := fakeTemplates(t, admin, &rest, &ws)
		c := &Client{acc: access{URL: srv.URL, Token: "secret"}, http: srv.Client()}
		for i := 0; i < 2; i++ {
			got, err := c.Render("{{ area_name('media_player.x') }}")
			if err != nil || got != "Küche" {
				t.Fatalf("admin=%v: Render = %q, %v", admin, got, err)
			}
		}
		if rest.Load() != 0 || ws.Load() != 2 {
			t.Fatalf("admin=%v: REST asked %d times, websocket %d; want 0 and 2", admin, rest.Load(), ws.Load())
		}
		c.acc.Token = "wrong"
		if _, err := c.Render("x"); err == nil {
			t.Fatalf("admin=%v: a token Home Assistant refuses rendered", admin)
		}
		srv.Close()
	}
}

func TestTemplateText(t *testing.T) {
	for in, want := range map[any]string{nil: "", "a": "a", 3.5: "3.5", true: "true"} {
		if got := templateText(in); got != want {
			t.Errorf("templateText(%v) = %q, want %q", in, got, want)
		}
	}
}
