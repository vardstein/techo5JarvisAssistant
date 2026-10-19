package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// The browser is signed in as the token's user, and a device drives it by touch. Left alone, anyone
// with the key could open Settings or Developer Tools and work them through the page. So a device is
// shown dashboards and nothing else: the page it asks for has to be one, and the page is kept on
// dashboards after that, however it is tapped.
//
// What counts as a dashboard is what Home Assistant says its panels are, not a guess from the path:
// a dashboard can be called anything.

// shown are the kinds of panel a device may be shown: the dashboards, and Home Assistant's own pages
// that only show things.
var shown = map[string]bool{
	"lovelace": true, "energy": true, "history": true, "logbook": true, "map": true,
	"light": true, "climate": true, "security": true, "home": true, "maintenance": true,
	"media-browser": true, "calendar": true, "todo": true,
}

// Webpage dashboards (iframe panels) are not shown at all: they frame whatever they were set to,
// and people set them to their tools - a code editor, the ESPHome dashboard - which would then be
// worked by touch by anyone with the key.

// guard knows which paths are dashboards, asking Home Assistant again now and then.
type guard struct {
	cfg config

	mu      sync.Mutex
	allowed map[string]bool // a path's first part
	music   string          // Music Assistant's panel, "" when its user is not shown one
	at      time.Time
}

const guardFresh = time.Minute

// panels is the first part of every path a device may be shown.
func (g *guard) panels(ctx context.Context) (map[string]bool, error) {
	g.mu.Lock()
	if g.allowed != nil && time.Since(g.at) < guardFresh {
		defer g.mu.Unlock()
		return g.allowed, nil
	}
	g.mu.Unlock()

	got, err := listPanels(ctx, g.cfg)
	if err != nil {
		g.mu.Lock()
		defer g.mu.Unlock()
		if g.allowed != nil {
			return g.allowed, nil // the last answer, rather than nothing, while Home Assistant is away
		}
		return nil, err
	}
	allowed := map[string]bool{}
	for path, p := range got {
		if shown[p.component] {
			allowed[path] = true
		}
	}
	music := musicPanel(got)
	g.mu.Lock()
	g.allowed, g.music, g.at = allowed, music, time.Now()
	g.mu.Unlock()
	return allowed, nil
}

// allows is whether a device may be shown path.
func (g *guard) allows(ctx context.Context, path string) (bool, error) {
	first, ok := firstPart(path)
	if !ok {
		return false, nil
	}
	allowed, err := g.panels(ctx)
	if err != nil {
		return false, err
	}
	return allowed[first], nil
}

// musicPath is Music Assistant's panel, its path's first part, or "" when Home Assistant shows the
// token's user none.
func (g *guard) musicPath(ctx context.Context) (string, error) {
	if _, err := g.panels(ctx); err != nil {
		return "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.music, nil
}

// musicPanel is which of the panels is Music Assistant's, "" for none. With two - the app and its
// beta both installed - it is the same one each time.
func musicPanel(panels map[string]panel) string {
	music := ""
	for path, p := range panels {
		if p.isMusicAssistant() && (music == "" || path < music) {
			music = path
		}
	}
	return music
}

// only is a guard for one panel and nothing else, which is what a Music Assistant session gets: its
// pages, not the dashboards around them.
func only(panel string) func(context.Context, string) (bool, error) {
	return func(_ context.Context, path string) (bool, error) {
		first, ok := firstPart(path)
		return ok && first == panel, nil
	}
}

// firstPart is a path's first part, the panel; false for a path that is not a plain one. Plain is
// letters, digits, '-', '_' and '/' and nothing else: no '%' to be decoded into something else, no
// dots, no whitespace a browser would drop - nothing a browser could read as another path than the
// one checked here.
func firstPart(path string) (string, bool) {
	if path == "" || strings.Contains(path, "//") {
		return "", false
	}
	for _, c := range path {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '/') {
			return "", false
		}
	}
	first, _, _ := strings.Cut(strings.TrimPrefix(path, "/"), "/")
	return first, first != ""
}

type panel struct {
	component, url string
	addon          string // the app's slug, for an app's panel
	admin          bool   // Home Assistant shows it to administrators only
}

// isMusicAssistant is whether the panel is Music Assistant's own pages: the app's panel, under
// whatever slug its repository gave it (music_assistant, or prefixed with the repository's hash), and
// not one for administrators. Home Assistant leaves those out of the list for a user who is not one,
// so a token that can see the panel is allowed to; and the README says dashcast's user is not one.
func (p panel) isMusicAssistant() bool {
	if p.component != "app" || p.admin {
		return false
	}
	return p.addon == "music_assistant" || strings.HasSuffix(p.addon, "_music_assistant") ||
		strings.HasSuffix(p.addon, "_music_assistant_beta")
}

// listPanels asks Home Assistant for its panels: path to what it is.
func listPanels(ctx context.Context, cfg config) (map[string]panel, error) {
	raw, err := haCall(ctx, cfg, "get_panels")
	if err != nil {
		return nil, err
	}
	var panels map[string]struct {
		URLPath   string `json:"url_path"`
		Component string `json:"component_name"`
		Admin     bool   `json:"require_admin"`
		Config    struct {
			URL   string `json:"url"`
			Addon string `json:"addon"`
		} `json:"config"`
	}
	if err := json.Unmarshal(raw, &panels); err != nil {
		return nil, err
	}
	out := map[string]panel{}
	for key, p := range panels {
		path := p.URLPath
		if path == "" {
			path = key
		}
		out[path] = panel{component: p.Component, url: p.Config.URL, addon: p.Config.Addon, admin: p.Admin}
	}
	return out, nil
}

// warnIfAdmin says so in the log when the token is an administrator's, which the README advises
// against.
func warnIfAdmin(ctx context.Context, cfg config) {
	name, admin, err := currentUser(ctx, cfg)
	if err != nil {
		slog.Warn("could not ask Home Assistant whose token this is", "err", err)
		return
	}
	if admin {
		slog.Warn("HA_TOKEN belongs to an administrator: make a user that is not one for dashcast (README, Security)", "user", name)
	}
}

// currentUser is whose cfg.token is, and whether they are an administrator.
func currentUser(ctx context.Context, cfg config) (string, bool, error) {
	raw, err := haCall(ctx, cfg, "auth/current_user")
	if err != nil {
		return "", false, err
	}
	var u struct {
		Name    string `json:"name"`
		IsAdmin bool   `json:"is_admin"`
	}
	if err := json.Unmarshal(raw, &u); err != nil {
		return "", false, err
	}
	return u.Name, u.IsAdmin, nil
}

// A Show can have its page signed in as its own Home Assistant user rather than dashcast's: it sends
// that user's token in its hello, inside the encrypted connection. What is a dashboard is what Home
// Assistant shows the user, so each such user has a guard of its own.
type users struct {
	mu     sync.Mutex
	guards map[string]*guard // by tokenID
	ask    func(context.Context, config) (string, bool, error)
}

var showUsers = &users{ask: currentUser}

// maxUsers is how many Show users dashcast keeps a guard for: a house has a few Shows, not dozens.
const maxUsers = 16

// guardFor is the guard for token's user and the user's name. Home Assistant is asked whose the
// token is every time, so a token taken back since is noticed. An administrator's token gets none:
// the Show is then shown dashcast's own user's page, which it would have been without a token at all.
// A token Home Assistant refuses gets none either, and an error that is errTokenRefused: that Show
// gets no page at all (GENBU-261).
func (u *users) guardFor(ctx context.Context, cfg config, token string) (*guard, string, error) {
	cfg.token = token
	name, admin, err := u.ask(ctx, cfg)
	if err != nil {
		return nil, "", err
	}
	if admin {
		return nil, name, errors.New("the Show's token belongs to an administrator, and a screen anyone can touch is not signed in as one")
	}
	id := tokenID(token)
	u.mu.Lock()
	defer u.mu.Unlock()
	if g := u.guards[id]; g != nil {
		return g, name, nil
	}
	if len(u.guards) >= maxUsers {
		return nil, name, fmt.Errorf("dashcast already has %d Show users", maxUsers)
	}
	if u.guards == nil {
		u.guards = map[string]*guard{}
	}
	g := &guard{cfg: cfg}
	u.guards[id] = g
	return g, name, nil
}

// tokenID names a token without being it, for the parked tabs' keys and the log: the first bytes of
// its hash.
func tokenID(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:6])
}

// errTokenRefused is Home Assistant turning a token away. For a Show's own token that is on purpose:
// its key was deleted, or its user switched off, which Genbu does while the Show is disabled in Home
// Assistant (GENBU-261).
var errTokenRefused = errors.New("home assistant refused the token")

// haCall runs one command on Home Assistant's websocket.
func haCall(ctx context.Context, cfg config, command string) (json.RawMessage, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	url := "ws" + strings.TrimPrefix(cfg.ha, "http") + "/api/websocket"
	c, resp, err := websocket.DefaultDialer.DialContext(ctx, url, http.Header{})
	if resp != nil && resp.Body != nil {
		resp.Body.Close()
	}
	if err != nil {
		return nil, fmt.Errorf("home assistant: %w", err)
	}
	defer c.Close()
	if dl, ok := ctx.Deadline(); ok {
		_ = c.SetReadDeadline(dl)
	}
	var msg struct {
		Type    string          `json:"type"`
		Success bool            `json:"success"`
		Result  json.RawMessage `json:"result"`
	}
	if err := c.ReadJSON(&msg); err != nil {
		return nil, err
	}
	if err := c.WriteJSON(map[string]string{"type": "auth", "access_token": cfg.token}); err != nil {
		return nil, err
	}
	if err := c.ReadJSON(&msg); err != nil {
		return nil, err
	}
	if msg.Type != "auth_ok" {
		return nil, errTokenRefused
	}
	if err := c.WriteJSON(map[string]any{"id": 1, "type": command}); err != nil {
		return nil, err
	}
	if err := c.ReadJSON(&msg); err != nil {
		return nil, err
	}
	if !msg.Success {
		return nil, errors.New("home assistant refused " + command)
	}
	return msg.Result, nil
}
