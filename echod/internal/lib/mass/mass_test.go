package mass

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testClient(t *testing.T, h http.HandlerFunc) (*Client, string) {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	host, port, err := net.SplitHostPort(srv.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	Path = filepath.Join(t.TempDir(), "music-assistant.json")
	return &Client{http: &http.Client{Timeout: 5 * time.Second}, port: port}, host
}

func TestCallSendsTheCommandWithTheToken(t *testing.T) {
	c, host := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api" {
			http.Error(w, "wrong place", http.StatusNotFound)
			return
		}
		if r.Header.Get("Authorization") != "Bearer secret-token" {
			http.Error(w, "Authentication required", http.StatusUnauthorized)
			return
		}
		var msg struct {
			Command string         `json:"command"`
			Args    map[string]any `json:"args"`
		}
		b, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(b, &msg); err != nil || msg.Command != "music/item_by_uri" || msg.Args["uri"] != "library://track/1" {
			http.Error(w, "bad body "+string(b), http.StatusBadRequest)
			return
		}
		_, _ = w.Write([]byte(`{"item_id": "1", "provider": "library"}`))
	})
	if _, err := c.Call(context.Background(), host, "x", nil); err == nil {
		t.Fatal("a call without a token must fail before it is sent")
	}
	if err := c.SetToken(" secret-token​\n"); err != nil {
		t.Fatal(err)
	}
	out, err := c.Call(context.Background(), host, "music/item_by_uri", map[string]any{"uri": "library://track/1"})
	if err != nil || string(out) != `{"item_id": "1", "provider": "library"}` {
		t.Fatalf("got %s, %v", out, err)
	}
}

func TestCallReportsTheStatus(t *testing.T) {
	c, host := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "This command requires the library.read scope", http.StatusForbidden)
	})
	_ = c.SetToken("t")
	_, err := c.Call(context.Background(), host, "metadata/get_track_lyrics", nil)
	var se *StatusError
	if !errors.As(err, &se) || se.Code != http.StatusForbidden || se.Command != "metadata/get_track_lyrics" {
		t.Fatalf("got %v", err)
	}
	if _, err := c.Call(context.Background(), "", "x", nil); err == nil {
		t.Fatal("no server is an error")
	}
}

func TestTokenIsKeptPrivateAndRemoved(t *testing.T) {
	c, _ := testClient(t, nil)
	if err := c.SetToken("abc"); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(Path)
	if err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("token file: %v, %v", st, err)
	}
	b, _ := os.ReadFile(Path)
	if string(b) != `{"token":"abc"}` || !c.Ready() {
		t.Fatalf("saved %s", b)
	}
	if err := c.SetToken(""); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(Path); !os.IsNotExist(err) || c.Ready() {
		t.Fatalf("the token is still there: %v", err)
	}
	if err := c.SetToken(""); err != nil {
		t.Fatalf("removing no token: %v", err)
	}
}

// A 401 stops the token, as Music Assistant will not take it again; "Authentication failed" is its own
// check breaking, which says nothing about the token. A new token starts over (GENBU-137).
func TestA401TurnsTheTokenAway(t *testing.T) {
	answer := "Authentication required"
	calls := 0
	c, host := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		http.Error(w, answer, http.StatusUnauthorized)
	})
	changed := 0
	c.Changed.Listen(func(struct{}) { changed++ })
	if c.KeyState() != KeyNone {
		t.Fatalf("no token: %s", c.KeyState())
	}
	_ = c.SetToken("t1")
	if c.KeyState() != KeyOK || !c.Ready() {
		t.Fatalf("new token: %s", c.KeyState())
	}

	answer = "Authentication failed"
	if _, err := c.Call(context.Background(), host, "x", nil); err == nil {
		t.Fatal("a 401 is an error")
	}
	if c.KeyState() != KeyOK || !c.Ready() {
		t.Fatalf("Music Assistant's own failure stopped the token: %s", c.KeyState())
	}

	answer = "Authentication required"
	var se *StatusError
	if _, err := c.Call(context.Background(), host, "x", nil); !errors.As(err, &se) || se.Code != http.StatusUnauthorized {
		t.Fatalf("got %v", err)
	}
	if c.KeyState() != KeyGone || c.Ready() {
		t.Fatalf("turned away: %s", c.KeyState())
	}
	if _, err := c.Call(context.Background(), host, "x", nil); !errors.Is(err, ErrRefused) || calls != 2 {
		t.Fatalf("a refused token was sent again: %v, %d calls", err, calls)
	}

	_ = c.SetToken("t2")
	if c.KeyState() != KeyOK || !c.Ready() {
		t.Fatalf("a new token is not refused: %s", c.KeyState())
	}
	if changed != 3 { // t1, the refusal, t2
		t.Fatalf("changed %d times", changed)
	}
	b, _ := os.ReadFile(Path)
	if string(b) != `{"token":"t2"}` {
		t.Fatalf("saved %s", b)
	}
}

func jwt(claims string) string {
	enc := base64.RawURLEncoding.EncodeToString
	return enc([]byte(`{"alg":"HS256","typ":"JWT"}`)) + "." + enc([]byte(claims)) + ".c2ln"
}

// The name is the sha256 of the token's id, as the installer works it out from Music Assistant's
// list of the user's tokens; never the token.
func TestKeyIDIsTheHashOfTheTokenID(t *testing.T) {
	h := sha256.Sum256([]byte("tok-id"))
	want := hex.EncodeToString(h[:])
	if got := keyID(jwt(`{"sub":"u","jti":"tok-id","exp":1}`)); got != want {
		t.Fatalf("got %q", got)
	}
	for _, token := range []string{"", "plain", "a.b", "a.!!.c", jwt(`{"sub":"u"}`), jwt(`not json`)} {
		if got := keyID(token); got != "" {
			t.Errorf("%q named %q", token, got)
		}
	}
	c, _ := testClient(t, nil)
	_ = c.SetToken(jwt(`{"jti":"tok-id"}`))
	if c.KeyID() != want {
		t.Fatalf("client named %q", c.KeyID())
	}
}
