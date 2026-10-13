package hass

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

// Media is one entry of a media browser listing: a station, or a folder of them.
type Media struct {
	Title     string  `json:"title"`
	ID        string  `json:"media_content_id"`
	Kind      string  `json:"media_content_type"`
	CanPlay   bool    `json:"can_play"`
	CanExpand bool    `json:"can_expand"` // a folder
	Thumb     string  `json:"thumbnail"`
	Children  []Media `json:"children"`
}

// wsSession is one authenticated websocket connection to Home Assistant, for one or several
// commands. Home Assistant only offers media browsing and resolving over this API, so a connection
// is opened for a question (or a walk of a folder tree) rather than kept around for the rare use
// these get.
type wsSession struct {
	conn *websocket.Conn
	next int
	// deadline is the caller's own deadline, if it gave one, and the zero time otherwise. Every command is
	// bounded by it as well as by its own thirty seconds: a caller that gave the connection five seconds
	// does not get thirty out of the next one, which is the difference between a lookup that gives up and
	// a caller that waits. It is the caller's, not this handshake's, so a walk that may run for minutes is
	// not cut off thirty seconds in.
	deadline time.Time
}

// wsOpen dials and authenticates. The caller closes the connection.
func (c *Client) wsOpen(ctx context.Context) (*wsSession, error) {
	acc, err := c.access()
	if err != nil {
		return nil, err
	}
	// What the caller gave, read before the wrap below: kept for the commands that follow, so their bound
	// is the caller's own and each still gets its own thirty seconds. Reading it after the wrap would make
	// every session thirty seconds long, and a caller that wanted longer would find every later command
	// failing once those thirty were up.
	caller, _ := ctx.Deadline()
	// The dial and the handshake get their own thirty seconds, or the caller's if that is shorter.
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	conn, answer, err := signIn(ctx, acc)
	if err != nil {
		return nil, err
	}
	if answer.Type == "auth_invalid" {
		c.refuse(acc.Token, reason(answer.Message))
	}
	if answer.Type != "auth_ok" {
		conn.Close()
		return nil, fmt.Errorf("hass: websocket auth: %s %s", answer.Type, answer.Message)
	}
	return &wsSession{conn: conn, deadline: caller}, nil
}

// authAnswer is Home Assistant's answer to a websocket sign-in: auth_ok, or auth_invalid and why.
type authAnswer struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// signIn dials the websocket and signs in with acc's token, within ctx. The connection comes back open
// whatever the answer was, for the caller to use or close.
func signIn(ctx context.Context, acc access) (*websocket.Conn, authAnswer, error) {
	var answer authAnswer
	url := "ws" + strings.TrimPrefix(acc.URL, "http") + "/api/websocket"
	conn, resp, err := websocket.DefaultDialer.DialContext(ctx, url, http.Header{})
	if resp != nil && resp.Body != nil {
		resp.Body.Close()
	}
	if err != nil {
		return nil, answer, fmt.Errorf("hass: websocket: %w", err)
	}
	if dl, ok := ctx.Deadline(); ok {
		_ = conn.SetReadDeadline(dl)
		_ = conn.SetWriteDeadline(dl)
	}
	if err := conn.ReadJSON(&answer); err != nil {
		conn.Close()
		return nil, answer, err
	}
	if err := conn.WriteJSON(map[string]string{"type": "auth", "access_token": acc.Token}); err != nil {
		conn.Close()
		return nil, answer, err
	}
	if err := conn.ReadJSON(&answer); err != nil {
		conn.Close()
		return nil, answer, err
	}
	return conn, answer, nil
}

// call sends one command and returns its raw result, allowing it 30 seconds or what is left of the
// context the connection was opened with, whichever ends first.
func (s *wsSession) call(cmd map[string]any) (json.RawMessage, error) {
	s.next++
	id := s.next
	cmd["id"] = id
	dl := time.Now().Add(30 * time.Second)
	if !s.deadline.IsZero() && s.deadline.Before(dl) {
		dl = s.deadline
	}
	_ = s.conn.SetReadDeadline(dl)
	_ = s.conn.SetWriteDeadline(dl)
	if err := s.conn.WriteJSON(cmd); err != nil {
		return nil, err
	}
	for {
		var res struct {
			ID      int             `json:"id"`
			Type    string          `json:"type"`
			Success bool            `json:"success"`
			Result  json.RawMessage `json:"result"`
			Error   struct {
				Code    string `json:"code"`
				Message string `json:"message"`
			} `json:"error"`
		}
		if err := s.conn.ReadJSON(&res); err != nil {
			return nil, err
		}
		if res.ID != id || res.Type != "result" {
			continue
		}
		if !res.Success {
			return nil, fmt.Errorf("hass: %v: %s %s", cmd["type"], res.Error.Code, res.Error.Message)
		}
		return res.Result, nil
	}
}

// render has Home Assistant render a template once: render_template answers with a subscription,
// and its first event carries the text.
func (s *wsSession) render(template string) (string, error) {
	if _, err := s.call(map[string]any{"type": "render_template", "template": template}); err != nil {
		return "", err
	}
	id := s.next
	for {
		var ev struct {
			ID    int    `json:"id"`
			Type  string `json:"type"`
			Event struct {
				Result any    `json:"result"`
				Error  string `json:"error"`
			} `json:"event"`
		}
		if err := s.conn.ReadJSON(&ev); err != nil {
			return "", err
		}
		if ev.ID != id || ev.Type != "event" {
			continue
		}
		if ev.Event.Error != "" {
			return "", fmt.Errorf("hass: render_template: %s", ev.Event.Error)
		}
		return templateText(ev.Event.Result), nil
	}
}

// templateText is a rendered template's result as text: the websocket hands back what the text
// reads as (a number, a list), where REST hands back the text itself.
func templateText(v any) string {
	switch v := v.(type) {
	case nil:
		return ""
	case string:
		return v
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	default:
		b, _ := json.Marshal(v)
		return string(b)
	}
}

// wsCall opens a connection for one command and returns its raw result.
func (c *Client) wsCall(ctx context.Context, cmd map[string]any) (json.RawMessage, error) {
	s, err := c.wsOpen(ctx)
	if err != nil {
		return nil, err
	}
	defer s.conn.Close()
	return s.call(cmd)
}

// BrowseTree lists root and the folders under it, breadth first, over one connection, handing each
// listing to visit, which returns false to stop the walk. At most maxFolders are listed. A folder
// that fails to list is passed over; only the root failing is an error.
func (c *Client) BrowseTree(ctx context.Context, root string, maxFolders int, visit func(Media) bool) error {
	s, err := c.wsOpen(ctx)
	if err != nil {
		return err
	}
	defer s.conn.Close()
	queue := []string{root}
	for n := 0; len(queue) > 0 && n < maxFolders; n++ {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		id := queue[0]
		queue = queue[1:]
		out, err := s.call(map[string]any{"type": "media_source/browse_media", "media_content_id": id})
		if err != nil {
			if id == root {
				return err
			}
			continue
		}
		var m Media
		if json.Unmarshal(out, &m) != nil {
			continue
		}
		if !visit(m) {
			return nil
		}
		for _, ch := range m.Children {
			if ch.CanExpand {
				queue = append(queue, ch.ID)
			}
		}
	}
	return nil
}

// Browse lists a media source folder, like media-source://radio_browser/local.
func (c *Client) Browse(ctx context.Context, id string) (Media, error) {
	out, err := c.wsCall(ctx, map[string]any{"type": "media_source/browse_media", "media_content_id": id})
	if err != nil {
		return Media{}, err
	}
	var m Media
	return m, json.Unmarshal(out, &m)
}

// Resolved is a media source item resolved to something fetchable: a URL (Home Assistant's own,
// or an absolute one an external source handed back) and its MIME type.
type Resolved struct {
	URL  string `json:"url"`
	MIME string `json:"mime_type"`
}

// ResolveMedia turns a media source id — usually one of Browse's playable children — into a
// fetchable URL. The URL may be relative to Home Assistant or, for some sources, already absolute.
func (c *Client) ResolveMedia(ctx context.Context, id string) (Resolved, error) {
	out, err := c.wsCall(ctx, map[string]any{"type": "media_source/resolve_media", "media_content_id": id})
	if err != nil {
		return Resolved{}, err
	}
	var r Resolved
	return r, json.Unmarshal(out, &r)
}
