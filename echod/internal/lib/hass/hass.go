// Package hass is Home Assistant's REST API, for what the ESPHome link cannot carry: a forecast
// (a service that answers), pictures, cameras. It needs a long-lived token, handed to the device
// once through an action and kept on userdata.
package hass

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	neturl "net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hook"
)

// Path is where the URL and token live: next to the PSK, readable by root only.
var Path = filepath.Join(filepath.Dir(layout.KeyPath), "hass.json")

type access struct {
	URL   string `json:"url"`
	Token string `json:"token"`
	// Refused is the sha256 of a token Home Assistant turned away, kept so that a restart does not
	// try it again. Only a new token clears it, or, for a home-only refusal, a new try (Retry).
	Refused string `json:"refused,omitempty"`
	// Reason is why, one of the Key states (key.go); "" was kept before there were reasons, and is
	// KeyGone.
	Reason string `json:"reason,omitempty"`
}

// ErrRefused is what every call gets, without going out, once Home Assistant has turned the token away.
//
// A token Home Assistant no longer knows - deleted, its user removed, the installer run again - used
// to be sent on every request for good: a 401 each time, which Home Assistant counts as a failed
// login, so a "Login attempt failed" notification after another, and with IP banning on, the Show's
// address banned, which also takes the voice link with it. Nothing tries again on a timer either,
// since a failed login never resets the ban count; only a new token, from the installer, does.
var ErrRefused = errors.New("hass: Home Assistant no longer accepts this device's token; run the installer again")

// refused is whether acc's token is the one Home Assistant turned away.
func (a access) refused() bool { return a.Token != "" && a.Refused == sum(a.Token) }

func sum(token string) string {
	h := sha256.Sum256([]byte(token))
	return hex.EncodeToString(h[:])
}

type Client struct {
	mu   sync.Mutex
	acc  access
	http *http.Client

	// Changed fires when the key's state changes: a new token, a refusal, its reason, a retry.
	// Listeners must not block.
	Changed hook.Hook[struct{}]
}

// statusError is an answer Home Assistant gave with an HTTP error status.
type statusError struct {
	method, path, status string
	code                 int
}

func (e *statusError) Error() string {
	return fmt.Sprintf("hass: %s %s: %s", e.method, e.path, e.status)
}

// ServerFailed is whether err is Home Assistant answering with a 5xx: it was reached and the action
// itself failed, as against no answer at all or a request it turned down.
func ServerFailed(err error) bool {
	var se *statusError
	return errors.As(err, &se) && se.code/100 == 5
}

var (
	once   sync.Once
	shared *Client
)

// Get is the client; it reads the saved access the first time.
func Get() *Client {
	once.Do(func() {
		shared = &Client{http: &http.Client{Timeout: 15 * time.Second}}
		if b, err := os.ReadFile(Path); err == nil {
			if err := os.Chmod(filepath.Dir(Path), 0o700); err != nil {
				slog.Error("hass: refusing saved access whose directory permissions cannot be secured", "err", err)
				return
			}
			if err := os.Chmod(Path, 0o600); err != nil {
				slog.Error("hass: refusing saved access whose permissions cannot be secured", "err", err)
				return
			}
			_ = json.Unmarshal(b, &shared.acc)
			// Kept before the cleaning below existed, an address can carry a character nobody can see.
			shared.acc.URL, shared.acc.Token = cleanURL(shared.acc.URL), clean(shared.acc.Token)
			if u, err := neturl.Parse(shared.acc.URL); err == nil && u.Host != "" {
				shared.acc.URL = normalURL(u) // an upper-case scheme saved before Set wrote it lower
			}
		}
	})
	return shared
}

// Set stores the URL (like http://192.168.1.20:8123) and token. An empty URL keeps the one saved: Genbu
// hands over a new key that way (GENBU-196) and never says where the Show goes.
func (c *Client) Set(url, token string) error {
	url, token = cleanURL(url), clean(token)
	if url == "" {
		c.mu.Lock()
		url = c.acc.URL
		c.mu.Unlock()
	}
	if url == "" || token == "" {
		return errors.New("hass: url and token are both needed")
	}
	u, err := neturl.Parse(url)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return fmt.Errorf("hass: %q is not an address like http://homeassistant.local:8123", url)
	}
	// As it was parsed: "HTTP://ha:8123" is a working address, and the websocket, which swaps the
	// scheme's "http" for "ws", needs it written the way url.Parse writes it.
	url = normalURL(u)
	c.mu.Lock()
	acc := access{URL: url, Token: token}
	if sum(token) == c.acc.Refused {
		// The same token again is still the one Home Assistant turned away, for the same reason.
		acc.Refused, acc.Reason = c.acc.Refused, c.acc.Reason
	}
	c.acc = acc
	c.mu.Unlock()
	if acc.refused() {
		slog.Warn("hass: given the token Home Assistant already turned away; it stays unused")
	}
	defer c.Changed.Emit(struct{}{})
	return save(acc)
}

// save writes acc to Path, readable by root only.
func save(acc access) error {
	b, _ := json.Marshal(acc)
	if err := os.MkdirAll(filepath.Dir(Path), 0o700); err != nil {
		return err
	}
	if err := os.Chmod(filepath.Dir(Path), 0o700); err != nil {
		return err
	}
	if err := os.WriteFile(Path, b, 0o600); err != nil {
		return err
	}
	return os.Chmod(Path, 0o600)
}

// clean takes out what a copy and paste carries along without anyone seeing it: spaces and line ends,
// and the invisible ones - a zero-width space, a byte order mark - that no trim removes. Neither an
// address nor a token has any of them, and one in front of "http" made every request fail with
// "first path segment in URL cannot contain colon", which says nothing about why (techo5#24).
func clean(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) || unicode.Is(unicode.Cf, r) || !unicode.IsPrint(r) {
			return -1
		}
		return r
	}, s)
}

// normalURL is an address as url.Parse wrote it: the scheme lower case, which the websocket needs
// ("http" swapped for "ws"), and the path as it was escaped. Nothing else belongs in it.
func normalURL(u *neturl.URL) string {
	return strings.TrimRight(u.Scheme+"://"+u.Host+u.EscapedPath(), "/")
}

// cleanURL is an address cleaned, without the trailing slash the paths are joined to.
func cleanURL(s string) string { return strings.TrimRight(clean(s), "/") }

// Ready reports whether there is an access to use: one given, and not turned away.
func (c *Client) Ready() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.acc.URL != "" && c.acc.Token != "" && !c.acc.refused()
}

// Refused reports whether Home Assistant turned the token away, for the screen to say so.
func (c *Client) Refused() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.acc.refused()
}

// Token is the token itself, for the one place it is handed on: the dashcast server, inside the
// encrypted connection, when the dashboard is to be signed in as this device's own user. "" when
// there is none, or Home Assistant turned it away.
func (c *Client) Token() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.acc.refused() {
		return ""
	}
	return c.acc.Token
}

// access is the access to make a call with, or why there is none.
func (c *Client) access() (access, error) {
	c.mu.Lock()
	acc := c.acc
	c.mu.Unlock()
	switch {
	case acc.URL == "" || acc.Token == "":
		return acc, errors.New("hass: no access configured")
	case acc.refused():
		return acc, ErrRefused
	}
	return acc, nil
}

// refuse records that Home Assistant turned token away, and why, and keeps it across a restart. Only
// while token is still the one in use and not refused yet: an answer to a call made before a new token
// came says nothing about the new one, and a late 401 must not undo the reason heard since. It
// reports whether it recorded anything.
func (c *Client) refuse(token, why string) bool {
	c.mu.Lock()
	if c.acc.Token != token || c.acc.refused() {
		c.mu.Unlock()
		return false
	}
	c.acc.Refused, c.acc.Reason = sum(token), why
	acc := c.acc
	c.mu.Unlock()
	slog.Error("hass: Home Assistant turned this device's token away; no more calls with it", "reason", why)
	c.keep(acc)
	return true
}

// keep saves acc after its refusal changed, and tells the listeners.
func (c *Client) keep(acc access) {
	if err := save(acc); err != nil {
		slog.Warn("hass: could not keep the refusal; the token is tried once more after a restart", "err", err)
	}
	c.Changed.Emit(struct{}{})
}

func (c *Client) do(method, path string, body any) ([]byte, error) {
	acc, err := c.access()
	if err != nil {
		return nil, err
	}
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, acc.URL+path, rd)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+acc.Token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized {
		c.turnedAway(acc)
	}
	if resp.StatusCode/100 != 2 {
		return nil, &statusError{method: method, path: path, status: resp.Status, code: resp.StatusCode}
	}
	return out, nil
}

// Day is one day of a forecast.
type Day struct {
	When      time.Time
	Condition string
	High      float64
	Low       float64
	Rain      int // precipitation probability, percent, -1 when not given
}

// Forecast is the daily forecast for a weather entity, as many days as it gives.
func (c *Client) Forecast(entity string) ([]Day, error) {
	out, err := c.do("POST", "/api/services/weather/get_forecasts?return_response",
		map[string]any{"entity_id": entity, "type": "daily"})
	if err != nil {
		return nil, err
	}
	var resp struct {
		ServiceResponse map[string]struct {
			Forecast []struct {
				Datetime      string   `json:"datetime"`
				Condition     string   `json:"condition"`
				Temperature   *float64 `json:"temperature"`
				Templow       *float64 `json:"templow"`
				Precipitation *float64 `json:"precipitation_probability"`
			} `json:"forecast"`
		} `json:"service_response"`
	}
	if err := json.Unmarshal(out, &resp); err != nil {
		return nil, err
	}
	fc, ok := resp.ServiceResponse[entity]
	if !ok {
		return nil, fmt.Errorf("hass: no forecast for %s", entity)
	}
	days := make([]Day, 0, len(fc.Forecast))
	for _, f := range fc.Forecast {
		d := Day{Condition: f.Condition, Rain: -1}
		if t, err := time.Parse(time.RFC3339, f.Datetime); err == nil {
			d.When = t.Local()
		}
		if f.Temperature != nil {
			d.High = *f.Temperature
		}
		if f.Templow != nil {
			d.Low = *f.Templow
		}
		if f.Precipitation != nil {
			d.Rain = int(*f.Precipitation)
		}
		days = append(days, d)
	}
	return days, nil
}

// State is an entity's state and attributes.
type State struct {
	State      string         `json:"state"`
	Attributes map[string]any `json:"attributes"`
}

func (c *Client) State(entity string) (State, error) {
	out, err := c.do("GET", "/api/states/"+entity, nil)
	if err != nil {
		return State{}, err
	}
	var s State
	return s, json.Unmarshal(out, &s)
}

// Fetch gets bytes from a path on Home Assistant (an entity_picture, a camera_proxy image).
func (c *Client) Fetch(path string) ([]byte, error) {
	return c.do("GET", path, nil)
}

// FetchURL gets bytes from a URL a resolve returned: a path relative to Home Assistant, one of its
// own absolute URLs, or an absolute URL an external source already signed. Only the first two get
// the token. The third is a host the user does not run — Jellyfin, Plex, Synology Photos, a radio
// directory, a cloud photo service — and the token is a long-lived one that opens the whole Home
// Assistant API, so putting it on that request would hand the house to whoever answers it. Those
// fetches go out bare, which is all they need: the source signed the URL itself.
func (c *Client) FetchURL(url string) ([]byte, error) {
	if strings.HasPrefix(url, "/") {
		return c.do("GET", url, nil)
	}
	if rest, ok := ownURL(c.baseURL(), url); ok {
		return c.do("GET", rest, nil)
	}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("hass: GET %s: %s", url, resp.Status)
	}
	return out, nil
}

// ownURL reports whether raw addresses the configured Home Assistant, and if so returns the part
// after the base for do to send along with the token. It parses both sides instead of comparing
// the strings: a prefix test has no boundary, so with a base of http://ha:8123 a URL like
// http://ha:8123.evil.example/x reads as one of ours and the token walks out to a stranger's host.
// Scheme and host compared as net/url splits them cannot be misread that way, and the path still
// needs a / after the base so that a Home Assistant living under /ha does not lend the token to
// /hacked.
func ownURL(base, raw string) (string, bool) {
	if base == "" {
		return "", false
	}
	b, err := neturl.Parse(base)
	if err != nil || b.Host == "" {
		return "", false
	}
	u, err := neturl.Parse(raw)
	if err != nil || !strings.EqualFold(u.Scheme, b.Scheme) ||
		!strings.EqualFold(hostPort(u), hostPort(b)) {
		return "", false
	}
	rest, prefix := u.EscapedPath(), strings.TrimRight(b.EscapedPath(), "/")
	if prefix != "" {
		if rest != prefix && !strings.HasPrefix(rest, prefix+"/") {
			return "", false
		}
		rest = strings.TrimPrefix(rest, prefix)
	}
	if rest == "" {
		rest = "/"
	}
	if u.RawQuery != "" {
		rest += "?" + u.RawQuery
	}
	return rest, true
}

// hostPort is a URL's host with the port its scheme implies when the URL leaves it out, so that a
// Home Assistant configured as http://ha and a URL of http://ha:80/... are recognized as the one host
// they are; written as strings they are not equal, and the token would stay behind on a request that
// then fails. It does not loosen the boundary ownURL exists for: host and port still come from the
// parser and are compared whole, so a name that only starts like ours is still another host, and any
// port other than the scheme's default is still another port.
func hostPort(u *neturl.URL) string {
	host, port := u.Hostname(), u.Port()
	if port == "" {
		switch strings.ToLower(u.Scheme) {
		case "http":
			port = "80"
		case "https":
			port = "443"
		}
	}
	return host + ":" + port
}

// baseURL is the configured Home Assistant URL, empty when none is set.
func (c *Client) baseURL() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.acc.URL
}

// Entity is an entity's id and the name Home Assistant shows for it.
type Entity struct {
	ID, Name string
}

// Entities lists the entities of a domain ("weather"), in Home Assistant's order.
func (c *Client) Entities(domain string) ([]Entity, error) {
	out, err := c.do("GET", "/api/states", nil)
	if err != nil {
		return nil, err
	}
	var states []struct {
		EntityID   string         `json:"entity_id"`
		Attributes map[string]any `json:"attributes"`
	}
	if err := json.Unmarshal(out, &states); err != nil {
		return nil, err
	}
	var list []Entity
	for _, s := range states {
		if !strings.HasPrefix(s.EntityID, domain+".") {
			continue
		}
		name, _ := s.Attributes["friendly_name"].(string)
		list = append(list, Entity{ID: s.EntityID, Name: name})
	}
	return list, nil
}

// MusicAssistantFor finds Music Assistant's own player for this device: the one it plays through the
// media player it owns here. own is this device's media player entity, and name is what this device calls
// itself - which is what it announces to Music Assistant over Sendspin, and so what Music Assistant names
// its player. Empty when there is none.
//
// Two ways, because what Music Assistant puts in active_queue changed: it used to be the entity id of this
// device's own player, and it is now the queue's own id (up<id>, which is the entity's unique_id as
// well) - so that test alone finds nothing, on every device running it. The name is what holds, and
// mass_player_type tells a player from a queue when both are named for the device.
func (c *Client) MusicAssistantFor(own, name string) (string, error) {
	out, err := c.do("GET", "/api/states", nil)
	if err != nil {
		return "", err
	}
	var states []struct {
		EntityID   string         `json:"entity_id"`
		Attributes map[string]any `json:"attributes"`
	}
	if err := json.Unmarshal(out, &states); err != nil {
		return "", err
	}
	named := ""
	for _, s := range states {
		if !strings.HasPrefix(s.EntityID, "media_player.") || s.EntityID == own {
			continue
		}
		if app, _ := s.Attributes["app_id"].(string); app != "music_assistant" {
			continue
		}
		if queue, _ := s.Attributes["active_queue"].(string); queue == own {
			return s.EntityID, nil
		}
		if name == "" {
			continue
		}
		friendly, _ := s.Attributes["friendly_name"].(string)
		if !strings.EqualFold(strings.TrimSpace(friendly), strings.TrimSpace(name)) {
			continue
		}
		// A queue named for the device as well is not the thing to resume or unjoin, so it is not the
		// answer of last resort either: only a player is, whatever the server calls it.
		if kind, _ := s.Attributes["mass_player_type"].(string); kind == "player" {
			return s.EntityID, nil
		} else if kind == "queue" {
			continue
		}
		named = s.EntityID
	}
	return named, nil
}

// Render has Home Assistant render a template and returns the text. Always over the websocket: REST's
// /api/template is for admins only, and a Show is its own, ordinary user. Trying REST first cost a 401
// on every start, which Home Assistant counts as a failed login: a "Login attempt failed" notification
// each time a Show booted, and with IP banning on, a Show locked out after a few restarts.
func (c *Client) Render(template string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	s, err := c.wsOpen(ctx)
	if err != nil {
		return "", err
	}
	defer s.conn.Close()
	out, err := s.render(template)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(out), nil
}

// Call runs a Home Assistant action.
func (c *Client) Call(domain, service string, data map[string]any) error {
	_, err := c.do("POST", "/api/services/"+domain+"/"+service, data)
	return err
}

// CallResponse runs a Home Assistant action that is defined to return response data. Home Assistant's
// REST API wraps that response beside changed_states when ?return_response is requested.
func (c *Client) CallResponse(domain, service string, data map[string]any) (json.RawMessage, error) {
	out, err := c.do("POST", "/api/services/"+domain+"/"+service+"?return_response", data)
	if err != nil {
		return nil, err
	}
	var envelope struct {
		ServiceResponse json.RawMessage `json:"service_response"`
	}
	if err := json.Unmarshal(out, &envelope); err != nil {
		return nil, err
	}
	if len(envelope.ServiceResponse) == 0 || string(envelope.ServiceResponse) == "null" {
		return nil, errors.New("Home Assistant action returned no response data")
	}
	return envelope.ServiceResponse, nil
}

// MediaPlay asks a media player to play again what it was playing.
func (c *Client) MediaPlay(player string) error {
	_, err := c.do("POST", "/api/services/media_player/media_play", map[string]any{"entity_id": player})
	return err
}

// PlayMedia asks a media player to play something: a URL, or a media-source:// id that Home Assistant
// resolves (and converts for the player) itself.
func (c *Client) PlayMedia(player, id, kind string) error {
	if kind == "" {
		kind = "music"
	}
	_, err := c.do("POST", "/api/services/media_player/play_media", map[string]any{
		"entity_id": player, "media_content_id": id, "media_content_type": kind,
	})
	return err
}

// Config is the part of Home Assistant's configuration the device uses.
type Config struct {
	Latitude  float64 `json:"latitude"`
	Longitude float64 `json:"longitude"`
	Country   string  `json:"country"`
}

func (c *Client) Config() (Config, error) {
	out, err := c.do("GET", "/api/config", nil)
	if err != nil {
		return Config{}, err
	}
	var cfg Config
	return cfg, json.Unmarshal(out, &cfg)
}
