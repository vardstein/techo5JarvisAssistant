package hass

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"log/slog"
	"strings"
	"time"
)

// How the key stands, for the screen, for the "Home Assistant key" sensor and for Genbu, which looks
// after a Show's key in Home Assistant (GENBU-196).
const (
	KeyOK   = "ok"   // a token, and Home Assistant has not turned it away
	KeyNone = "none" // no token given yet
	// KeyGone is a token Home Assistant does not know: deleted, its user removed, or its user switched
	// off, which Home Assistant answers with the same words. Genbu tells these apart from its own side.
	KeyGone = "gone"
	// KeyRemote is a user that may sign in from home only, and a sign-in Home Assistant did not see
	// coming from home: the Show on another network, or its traffic reaching Home Assistant through a
	// proxy or a VPN that hides where it came from. Nothing about the token is wrong.
	KeyRemote = "remote"
	KeyOff    = "off" // its user is switched off, for a Home Assistant that says so
)

// reason is the Key state a websocket auth_invalid message stands for. Home Assistant's own words
// (auth/__init__.py, components/http/auth.py, 2026.10): the token unknown or its user inactive is
// "Invalid access token or password"; the rest come from async_user_not_allowed_do_auth. Words it may
// use one day count as gone, which stops the token as before.
func reason(message string) string {
	switch strings.TrimSpace(message) {
	case "User cannot authenticate remotely", "User is local only", "Invalid remote IP",
		"No request available to validate local access":
		return KeyRemote
	case "User is not active":
		return KeyOff
	}
	return KeyGone
}

// KeyState is how the key stands: one of the Key states.
func (c *Client) KeyState() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	switch {
	case c.acc.URL == "" || c.acc.Token == "":
		return KeyNone
	case !c.acc.refused():
		return KeyOK
	case c.acc.Reason == "":
		return KeyGone
	}
	return c.acc.Reason
}

// KeyID names the token without giving it away: the sha256 of the id of the refresh token it was
// made from (the JWT's "iss"), so Genbu can tell whether the Show holds the key it should without the
// key ever leaving the Show. "" without a token, or for one that is no JWT.
func (c *Client) KeyID() string {
	c.mu.Lock()
	token := c.acc.Token
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
		Iss string `json:"iss"`
	}
	if json.Unmarshal(b, &claims) != nil || claims.Iss == "" {
		return ""
	}
	return sum(claims.Iss)
}

// turnedAway handles a 401 over REST. A 401 says no, not why: Home Assistant gives one for a token it
// does not know, for a home-only user it did not see coming from home, and for a path that is for
// admins only. The refusal stands at once, so nothing else goes out with the token, and one websocket
// sign-in then hears why. Being let in there lifts the refusal again. That sign-in is the one more
// failed login the refusal costs, and only the call that recorded the refusal makes it.
func (c *Client) turnedAway(acc access) {
	if !c.refuse(acc.Token, KeyGone) {
		return
	}
	why, err := c.hear(acc)
	switch {
	case err != nil:
		slog.Warn("hass: could not ask Home Assistant why it turned the token away", "err", err)
	case why == KeyOK:
		slog.Warn("hass: a 401 that was not about the token; it stays in use")
		c.lift(acc.Token, "")
	default:
		c.explain(acc.Token, why)
	}
}

// hear signs in over the websocket with acc's token and says how that went: KeyOK, or the reason it
// was turned away. It records nothing.
func (c *Client) hear(acc access) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	conn, answer, err := signIn(ctx, acc)
	if err != nil {
		return "", err
	}
	conn.Close()
	if answer.Type == "auth_ok" {
		return KeyOK, nil
	}
	return reason(answer.Message), nil
}

// explain puts why on the refusal of token, once the websocket said it.
func (c *Client) explain(token, why string) {
	c.mu.Lock()
	if c.acc.Token != token || !c.acc.refused() || c.acc.Reason == why {
		c.mu.Unlock()
		return
	}
	c.acc.Reason = why
	acc := c.acc
	c.mu.Unlock()
	slog.Error("hass: why Home Assistant turned the token away", "reason", why)
	c.keep(acc)
}

// lift takes the refusal of token back, when it was refused for why, or for any reason with "". It
// reports whether it did.
func (c *Client) lift(token, why string) bool {
	c.mu.Lock()
	had := c.acc.Reason
	if had == "" {
		had = KeyGone
	}
	if c.acc.Token != token || !c.acc.refused() || (why != "" && why != had) {
		c.mu.Unlock()
		return false
	}
	c.acc.Refused, c.acc.Reason = "", ""
	acc := c.acc
	c.mu.Unlock()
	c.keep(acc)
	return true
}

// Retry signs in once more after a refusal and answers with the state after it. The Show calls it
// for a home-only refusal when its addresses change; Genbu calls it (home_assistant_retry) when Home
// Assistant's own records say the key should work: it still has the key, and its user is on and may
// sign in from where the Show is. Never on a timer, as each try that fails is a failed login.
func (c *Client) Retry() string {
	c.mu.Lock()
	acc := c.acc
	c.mu.Unlock()
	if !c.lift(acc.Token, "") {
		return c.KeyState()
	}
	slog.Info("hass: trying the refused key once more", "reason", c.KeyState())
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if s, err := c.wsOpen(ctx); err == nil {
		s.conn.Close()
	} else if c.KeyState() == KeyOK {
		slog.Warn("hass: the retry did not reach Home Assistant", "err", err)
	}
	return c.KeyState()
}
