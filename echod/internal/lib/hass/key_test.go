package hass

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
)

// GENBU-196: a refusal says why, so the screen and Genbu can do the right thing about it.

func TestEveryReasonHomeAssistantGives(t *testing.T) {
	for msg, want := range map[string]string{
		"Invalid access token or password":              KeyGone,
		"User cannot authenticate remotely":             KeyRemote,
		"User is local only":                            KeyRemote,
		"Invalid remote IP":                             KeyRemote,
		"No request available to validate local access": KeyRemote,
		"User is not active":                            KeyOff,
		"":                                              KeyGone,
		"Something new one day":                         KeyGone,
	} {
		if got := reason(msg); got != want {
			t.Errorf("%q: %s, want %s", msg, got, want)
		}
	}
}

func usePath(t *testing.T) {
	t.Helper()
	was := Path
	Path = filepath.Join(t.TempDir(), "hass.json")
	t.Cleanup(func() { Path = was })
}

func kept(t *testing.T) access {
	t.Helper()
	b, err := os.ReadFile(Path)
	if err != nil {
		t.Fatal(err)
	}
	var acc access
	if err := json.Unmarshal(b, &acc); err != nil {
		t.Fatal(err)
	}
	return acc
}

// A REST 401 is followed by one websocket sign-in, which hears that the user is home-only; the
// reason is kept with the refusal, and nothing more goes out.
func TestA401IsAskedWhyOnce(t *testing.T) {
	usePath(t)
	var calls atomic.Int32
	srv := fakeTurnAway(t, func() string { return "other" }, "User cannot authenticate remotely", &calls)
	c := &Client{http: srv.Client()}
	if err := c.Set(srv.URL, "tok"); err != nil {
		t.Fatal(err)
	}
	if got := c.KeyState(); got != KeyOK {
		t.Fatalf("before any call: %s", got)
	}
	var told atomic.Int32
	c.Changed.Listen(func(struct{}) { told.Add(1) })
	_, _ = c.State("light.hall")
	_, _ = c.State("light.hall")
	if got := c.KeyState(); got != KeyRemote {
		t.Fatalf("state %s, want remote", got)
	}
	if n := calls.Load(); n != 2 {
		t.Fatalf("%d requests, want the 401 and one sign-in", n)
	}
	if acc := kept(t); acc.Reason != KeyRemote || !acc.refused() {
		t.Fatalf("kept %+v", acc)
	}
	if told.Load() == 0 {
		t.Fatal("nobody was told")
	}
}

// A 401 the websocket does not back up (an admin-only path) does not stop the token.
func TestA401ThatIsNotAboutTheToken(t *testing.T) {
	usePath(t)
	var calls atomic.Int32
	ws := fakeAuth(t, "tok", &calls)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/websocket" {
			ws.Config.Handler.ServeHTTP(w, r)
			return
		}
		http.Error(w, "401: Unauthorized", http.StatusUnauthorized)
	}))
	t.Cleanup(srv.Close)
	c := &Client{http: srv.Client()}
	if err := c.Set(srv.URL, "tok"); err != nil {
		t.Fatal(err)
	}
	_, _ = c.State("light.hall")
	if !c.Ready() || c.KeyState() != KeyOK {
		t.Fatalf("after a 401 the websocket let in: %s", c.KeyState())
	}
	if kept(t).Refused != "" {
		t.Fatal("a refusal was kept")
	}
}

// A refusal kept before there were reasons is a gone key.
func TestAnOldRefusalIsGone(t *testing.T) {
	c := &Client{acc: access{URL: "http://ha", Token: "tok", Refused: sum("tok")}}
	if got := c.KeyState(); got != KeyGone {
		t.Fatalf("%s", got)
	}
	if got := (&Client{}).KeyState(); got != KeyNone {
		t.Fatalf("no token: %s", got)
	}
}

// Retry signs in once more for a home-only refusal only; a gone key stays where it is, unsent.
func TestRetryIsForHomeOnly(t *testing.T) {
	usePath(t)
	var calls atomic.Int32
	good := "other"
	var takes atomic.Value
	takes.Store(good)
	srv := fakeTurnAway(t, func() string { return takes.Load().(string) }, "User cannot authenticate remotely", &calls)
	c := &Client{http: srv.Client()}
	if err := c.Set(srv.URL, "tok"); err != nil {
		t.Fatal(err)
	}
	c.refuse("tok", KeyGone)
	if got := c.Retry(); got != KeyGone || calls.Load() != 0 {
		t.Fatalf("a gone key: %s after %d requests", got, calls.Load())
	}
	c.explain("tok", KeyRemote)
	if got := c.Retry(); got != KeyRemote || calls.Load() != 1 {
		t.Fatalf("still not from home: %s after %d requests", got, calls.Load())
	}
	takes.Store("tok") // the route was fixed
	if got := c.Retry(); got != KeyOK || calls.Load() != 2 {
		t.Fatalf("back home: %s after %d requests", got, calls.Load())
	}
	if kept(t).Refused != "" {
		t.Fatal("the lifted refusal was kept")
	}
}

// The same token handed over again keeps its reason; a late 401 does not overwrite it.
func TestTheReasonStays(t *testing.T) {
	usePath(t)
	c := &Client{}
	if err := c.Set("http://ha", "tok"); err != nil {
		t.Fatal(err)
	}
	c.refuse("tok", KeyRemote)
	c.refuse("tok", KeyGone)
	if err := c.Set("http://ha", "tok"); err != nil {
		t.Fatal(err)
	}
	if got := c.KeyState(); got != KeyRemote {
		t.Fatalf("%s", got)
	}
}

// Genbu hands a new key over without an address; the saved one stays.
func TestANewKeyWithoutAnAddress(t *testing.T) {
	usePath(t)
	c := &Client{}
	if err := c.Set("", "tok"); err == nil {
		t.Fatal("a key without any address was taken")
	}
	if err := c.Set("http://ha:8123/", "old"); err != nil {
		t.Fatal(err)
	}
	c.refuse("old", KeyGone)
	if err := c.Set("", "new"); err != nil {
		t.Fatal(err)
	}
	if acc := kept(t); acc.URL != "http://ha:8123" || acc.Token != "new" || acc.refused() {
		t.Fatalf("kept %+v", acc)
	}
}

func TestKeyIDNamesTheRefreshToken(t *testing.T) {
	part := func(v any) string {
		b, _ := json.Marshal(v)
		return base64.RawURLEncoding.EncodeToString(b)
	}
	jwt := part(map[string]string{"alg": "HS256"}) + "." + part(map[string]any{"iss": "rt-id", "exp": 1}) + ".sig"
	if got := keyID(jwt); got != sum("rt-id") {
		t.Fatalf("%q", got)
	}
	for _, bad := range []string{"", "tok", "a.b.c", "a." + part(map[string]int{"exp": 1}) + ".c"} {
		if got := keyID(bad); got != "" {
			t.Errorf("%q: %q", bad, got)
		}
	}
}
