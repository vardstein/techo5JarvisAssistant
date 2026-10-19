// Package mass is Music Assistant's own API, for what Home Assistant's integration does not pass on:
// a song's lyrics. It needs a long-lived token of a Music Assistant user made for this Show, handed to
// the device once through an action and kept on userdata. The server is the one Sendspin is paired
// with, so the address is not kept here.
package mass

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hook"
)

// Port is Music Assistant's web server, where its API answers.
const Port = "8095"

// Path is where the token lives: next to the PSK, readable by root only.
var Path = filepath.Join(filepath.Dir(layout.KeyPath), "music-assistant.json")

// How the token stands, for the "Music Assistant key" sensor and for the installer, which keeps a
// token that still works rather than making a new one on every run (GENBU-137).
const (
	KeyOK   = "ok"   // a token, and Music Assistant has not turned it away
	KeyNone = "none" // no token given yet
	// KeyGone is a token Music Assistant answered with "Authentication required": revoked, a year old,
	// its user removed or switched off, or made by a server that has since got a new secret.
	KeyGone = "gone"
)

type access struct {
	Token string `json:"token"`
}

type Client struct {
	mu    sync.Mutex
	token string
	// refused is set when Music Assistant turned the token away; it is no longer sent then. Not kept
	// on userdata: after a restart the Show asks once more, so a refusal that was wrong does not stick.
	refused bool
	http    *http.Client
	// port is Port except in tests, whose server listens where it likes.
	port string

	// Changed fires when the key's state changes: a new token, or a refusal. Listeners must not block.
	Changed hook.Hook[struct{}]
}

// ErrRefused is a call not made because Music Assistant turned the token away.
var ErrRefused = errors.New("mass: Music Assistant no longer accepts this Show's token; run the installer again")

var (
	once   sync.Once
	shared *Client
)

// Get is the client; it reads the saved token the first time.
func Get() *Client {
	once.Do(func() {
		shared = &Client{http: &http.Client{Timeout: 15 * time.Second}, port: Port}
		b, err := os.ReadFile(Path)
		if err != nil {
			return
		}
		if err := os.Chmod(Path, 0o600); err != nil {
			slog.Error("mass: refusing a saved token whose permissions cannot be secured", "err", err)
			return
		}
		var a access
		_ = json.Unmarshal(b, &a)
		shared.token = clean(a.Token)
	})
	return shared
}

// SetToken stores the token; an empty one removes it.
func (c *Client) SetToken(token string) error {
	token = clean(token)
	c.mu.Lock()
	c.token, c.refused = token, false
	c.mu.Unlock()
	c.Changed.Emit(struct{}{})
	if token == "" {
		if err := os.Remove(Path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		return nil
	}
	b, _ := json.Marshal(access{Token: token})
	if err := os.MkdirAll(filepath.Dir(Path), 0o700); err != nil {
		return err
	}
	if err := os.WriteFile(Path, b, 0o600); err != nil {
		return err
	}
	return os.Chmod(Path, 0o600)
}

// Ready reports whether there is a token to use: one, and not turned away.
func (c *Client) Ready() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.token != "" && !c.refused
}

// KeyState is how the token stands: one of the Key states.
func (c *Client) KeyState() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	switch {
	case c.token == "":
		return KeyNone
	case c.refused:
		return KeyGone
	}
	return KeyOK
}

// KeyID names the token without giving it away: the sha256 of its id (the JWT's "jti"), which Music
// Assistant lists with the user's tokens, so the installer can tell whether the Show holds the one it
// should. "" without a token, or for one that is no JWT.
func (c *Client) KeyID() string {
	c.mu.Lock()
	token := c.token
	c.mu.Unlock()
	return keyID(token)
}

func keyID(token string) string {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return ""
	}
	b, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return ""
	}
	var claims struct {
		Jti string `json:"jti"`
	}
	if json.Unmarshal(b, &claims) != nil || claims.Jti == "" {
		return ""
	}
	h := sha256.Sum256([]byte(claims.Jti))
	return hex.EncodeToString(h[:])
}

// refuse records that Music Assistant turned token away, unless a new one came in the meantime.
func (c *Client) refuse(token string) {
	c.mu.Lock()
	if c.token != token || c.refused {
		c.mu.Unlock()
		return
	}
	c.refused = true
	c.mu.Unlock()
	slog.Error("mass: Music Assistant turned the token away; lyrics stop until the installer runs again")
	c.Changed.Emit(struct{}{})
}

// StatusError is an answer Music Assistant gave with an HTTP error status: 401 for a token it does
// not know (revoked, or a year old), 403 for a command the Show's user may not use.
type StatusError struct {
	Command string
	Code    int
	Text    string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("mass: %s: HTTP %d %s", e.Command, e.Code, e.Text)
}

// Call runs one command on the server at host and returns its result as Music Assistant wrote it.
func (c *Client) Call(ctx context.Context, host, command string, args any) (json.RawMessage, error) {
	c.mu.Lock()
	token, port, refused := c.token, c.port, c.refused
	c.mu.Unlock()
	if token == "" {
		return nil, errors.New("mass: no token")
	}
	if refused {
		return nil, ErrRefused
	}
	if host == "" {
		return nil, errors.New("mass: no server")
	}
	body, err := json.Marshal(map[string]any{"message_id": "1", "command": command, "args": args})
	if err != nil {
		return nil, err
	}
	url := "http://" + net.JoinHostPort(host, port) + "/api"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode/100 != 2 {
		text := strings.TrimSpace(string(out))
		if len(text) > 200 {
			text = text[:200]
		}
		// A 401 is the token turned away, but for "Authentication failed": Music Assistant's own check
		// broke (webserver/controller.py, 2.10.6), which says nothing about the token.
		if resp.StatusCode == http.StatusUnauthorized && text != "Authentication failed" {
			c.refuse(token)
		}
		return nil, &StatusError{Command: command, Code: resp.StatusCode, Text: text}
	}
	return out, nil
}

// clean takes out what a copy and paste carries along without anyone seeing it, as hass does: no
// token has a space or an invisible character in it.
func clean(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) || unicode.Is(unicode.Cf, r) || !unicode.IsPrint(r) {
			return -1
		}
		return r
	}, s)
}
