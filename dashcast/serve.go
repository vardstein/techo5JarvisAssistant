package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/draw"
	"image/jpeg"
	"image/png"
	"log/slog"
	"net"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/chromedp/cdproto/input"
	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

// The conversation with a device, inside the encrypted connection secure.go makes: the device opens
// with one line of JSON, a hello; after that it sends a line per touch. What comes back is messages framed as a 4-byte big-endian length, then a
// kind byte and its payload:
//
//	kindPicture  2-byte x, 2-byte y, then a JPEG to draw with its top left there
//	kindProblem  a sentence for the screen to show: the key was wrong, the page would not load
//	kindHalf     2-byte x, 2-byte y, then a JPEG at half size, to draw doubled with its top left there
//	kindWhose    whose Home Assistant user the page is signed in as, before the first picture: "own"
//	             (the Show's, from its token) or "dashcast"; a device that knows no kindWhose skips it
//
// The first picture is the whole screen; after it, only what changed. While most of the screen is
// changing - a page scrolling - the pictures go at half size, a quarter of the work for a device to
// decode, and once it stops the same places follow at full size.
const (
	kindPicture = 1
	kindProblem = 2
	kindHalf    = 3
	kindWhose   = 4
)

// soft is how much of the screen has to change in one frame before it goes at half size, and
// settle how long after the last such frame the full-size pictures follow.
const (
	soft   = 0.35
	settle = 250 * time.Millisecond
)

type hello struct {
	Name string `json:"name"` // for the log
	W    int    `json:"w"`
	H    int    `json:"h"`
	Path string `json:"path"` // the dashboard, as in Home Assistant's own address bar: /lovelace/0

	// Kiosk is the page without Home Assistant's top bar (browser.go, kioskScript).
	Kiosk bool `json:"kiosk,omitempty"`

	// Cold asks Dashcast to discard a parked matching tab and create a brand-new browser page.
	Cold bool `json:"cold,omitempty"`

	// Page is a page asked for by name rather than by path, which dashcast finds for itself:
	// pageMusic. Path is then ignored. A device too old to know of it never sends it.
	Page string `json:"page,omitempty"`

	// Token is a long-lived token of the Show's own Home Assistant user, for its page to be signed
	// in as that user rather than dashcast's (guard.go, users). It is never logged.
	Token string `json:"token,omitempty"`
}

// pageMusic is Music Assistant's own pages - its library, queue and groups - in its Home Assistant
// panel. A session showing it is kept on that panel alone, and out of Music Assistant's settings
// (browser.go, musicScript): a screen anyone can touch is not where its providers and players are set up.
const pageMusic = "music"

// showBoards is every Jarvis Show board dashcast serves, with its screen size. main.go checks
// JARVIS_SHOW_BOARD against it and applyApplianceProfile takes the size from it, so a board is either
// here for both or for neither. tests/test_show_board_lists.py keeps it in step with echod's update
// boards and the installer's board profiles.
var showBoards = map[string]struct{ w, h int }{
	"crown":    {1280, 800},
	"checkers": {960, 480},
	"cronos":   {960, 480},
}

// showBoardNames is showBoards' names in order, for messages.
func showBoardNames() []string {
	names := make([]string, 0, len(showBoards))
	for name := range showBoards {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func applyApplianceProfile(h *hello, board string) bool {
	if board == "" {
		return true
	}
	// Jarvis Show is an appliance, not a generic browser client. The server owns the viewport,
	// path and browser chrome contract so stale/misconfigured device state cannot reintroduce the
	// old header/black-bar problems. Board geometry is fixed server-side.
	size, ok := showBoards[board]
	if !ok {
		return false
	}
	h.W, h.H = size.w, size.h
	h.Path = "/jarvis-display"
	h.Kiosk = true
	return true
}

type touchMsg struct {
	T string `json:"t"` // tap, down, move, up
	X int    `json:"x"`
	Y int    `json:"y"`
}

// quality is the pictures' JPEG quality: text on a dashboard wants it high, and the pictures are
// small, since mostly only what changed is sent.
const quality = 85

// lineMax is the longest line a device may send, and maxSessions how many screens are served at once.
const (
	lineMax     = 4096
	maxSessions = 8
)

// sessions is how many screens are being served.
var sessions atomic.Int32

// unproven is how many connections may be in their handshake at once. Before the key has been shown
// a connection is anybody on the network, each holding memory and time for up to ten seconds, so a
// few at a time is plenty and the rest are closed at once rather than let pile up.
var unproven = make(chan struct{}, maxSessions)

// failures keeps the log from filling with one line per failed handshake when something keeps trying.
var failures struct {
	sync.Mutex
	last  time.Time
	count int
}

func failedHandshake(from net.Addr, err error) {
	failures.Lock()
	defer failures.Unlock()
	failures.count++
	if time.Since(failures.last) < time.Minute {
		return
	}
	slog.Warn("a device failed the handshake: a wrong key, or not a TECHO5 device", "from", from, "err", err, "failed", failures.count)
	failures.last, failures.count = time.Now(), 0
}

func serve(ctx context.Context, b *browser, g *guard, cfg config, raw net.Conn) {
	defer raw.Close()
	select {
	case unproven <- struct{}{}:
	default:
		failedHandshake(raw.RemoteAddr(), errors.New("too many connections in their handshake"))
		return
	}
	_ = raw.SetReadDeadline(time.Now().Add(10 * time.Second))
	// The key is proved by the handshake, so a device that gets past it has the key.
	c, err := serverHandshake(raw, cfg.key)
	<-unproven
	if err != nil {
		failedHandshake(raw.RemoteAddr(), err)
		return
	}
	// Lines are small - a hello, a touch - and are read with a small limit, so a device cannot send
	// one that never ends.
	lines := bufio.NewScanner(c)
	lines.Buffer(make([]byte, 0, 1024), lineMax)
	if !lines.Scan() {
		return
	}
	var h hello
	if json.Unmarshal(lines.Bytes(), &h) != nil {
		return
	}
	_ = c.SetReadDeadline(time.Time{})
	out := &sender{c: c}

	if !applyApplianceProfile(&h, cfg.board) {
		return
	}

	if h.W <= 0 || h.H <= 0 || h.W > 4096 || h.H > 4096 {
		return
	}
	if !strings.HasPrefix(h.Path, "/") {
		h.Path = "/" + h.Path
	}
	// Whose page it is: the Show's own user's when it sent a token that will do, dashcast's otherwise.
	// A token Home Assistant turns away gets nothing: that Show is locked out on purpose (GENBU-261).
	token, user := "", ""
	if h.Token != "" {
		if ug, name, err := showUsers.guardFor(ctx, cfg, h.Token); errors.Is(err, errTokenRefused) {
			slog.Warn("Home Assistant turned the Show's own key away, so it gets no page", "name", h.Name)
			out.problem(keyTurnedAway)
			return
		} else if err != nil {
			slog.Warn("the Show's own Home Assistant user can't be used, so it gets dashcast's", "name", h.Name, "user", name, "err", err)
		} else {
			g, token, user = ug, h.Token, name
		}
	}
	h.Token = ""

	// Dashboards only (guard.go); or, for Music Assistant, its panel only.
	allows, music := g.allows, false
	switch h.Page {
	case "":
	case pageMusic:
		panel, err := g.musicPath(ctx)
		if err != nil {
			slog.Warn("could not ask Home Assistant where Music Assistant is", "err", err)
			out.problem("Can't reach Home Assistant to find Music Assistant.")
			return
		}
		if panel == "" {
			slog.Warn("a device asked for Music Assistant, and Home Assistant shows dashcast's user no panel of it", "name", h.Name)
			out.problem("Music Assistant isn't available to dashcast's user.")
			return
		}
		h.Path, h.Kiosk, allows, music = "/"+panel, true, only(panel), true
	default:
		slog.Warn("a device asked for a page dashcast does not know", "name", h.Name, "page", h.Page)
		out.problem("That page is not one dashcast shows.")
		return
	}
	switch ok, err := allows(ctx, h.Path); {
	case err != nil:
		slog.Warn("could not ask Home Assistant what its dashboards are", "err", err)
		out.problem("Can't reach Home Assistant to check the dashboard.")
		return
	case !ok:
		slog.Warn("a device asked for a page that is not a dashboard", "name", h.Name, "path", h.Path)
		out.problem("That page is not a dashboard, so it is not shown.")
		return
	}
	allowed, _ := g.panels(ctx)
	if music {
		first, _ := firstPart(h.Path)
		allowed = map[string]bool{first: true}
	}
	// Each screen is a browser tab of a couple of hundred megabytes, so there is a limit to them.
	if n := sessions.Add(1); n > maxSessions {
		sessions.Add(-1)
		slog.Warn("too many screens at once", "name", h.Name, "most", maxSessions)
		out.problem("The dashboard server is showing as many screens as it can.")
		return
	}
	defer sessions.Add(-1)
	slog.Info("device connected", "name", h.Name, "from", c.RemoteAddr(), "size", [2]int{h.W, h.H}, "path", h.Path, "user", user)
	defer slog.Info("device gone", "name", h.Name)
	// The Show's log says whose page it got, so one that sent its token can tell it was not used
	// (GENBU-251): a dashcast too old for own users signs every screen in as its own user.
	_ = out.send(kindWhose, []byte(whose(token)))

	sctx, cancel := context.WithCancel(ctx)
	defer cancel()

	// The tab: this screen's parked one if it left a moment ago (warm.go), or a new one. It outlives
	// the session, so it is opened against the server's context, not this connection's.
	key := fmt.Sprintf("%s|%dx%d|%s|%t|%t|g=%s", h.Name, h.W, h.H, h.Path, h.Kiosk, music, cfg.generation)
	if token != "" {
		key += "|u=" + tokenID(token) // another user's tab is never handed over
	}
	if h.Cold {
		warm.discard(key)
	}
	w := warm.take(key)
	reused := w != nil
	if !reused {
		tab, closeTab, err := b.open(ctx, token, h.Path, h.W, h.H, allowed, h.Kiosk, music)
		if err != nil {
			slog.Warn("opening the dashboard failed", "name", h.Name, "err", err)
			out.problem("The dashboard would not open: " + err.Error())
			return
		}
		w = newWarmTab(tab, closeTab, key)
	} else {
		slog.Info("dashboard picked up where it was left", "name", h.Name)
	}
	// Parked when this session ends, for the screen to come back to; closed when its key was turned
	// away, so nothing of it is handed out again.
	var turnedAway atomic.Bool
	defer func() {
		if turnedAway.Load() {
			w.watch(nil)
			w.close()
			return
		}
		warm.park(w)
	}()
	tab := w.ctx

	d := &differ{}
	w.watch(func(f *page.EventScreencastFrame) {
		go func() {
			// Acknowledged once handled, which is what paces Chrome: it sends the next frame only
			// after this one is acknowledged, so a slow device holds frames back rather than queuing
			// them - and the acknowledgment waits longer the longer nothing has changed (idle),
			// unless the screen is touched.
			changed := false
			defer func() {
				d.wait(changed)
				_ = chromedp.Run(tab, page.ScreencastFrameAck(f.SessionID))
			}()
			raw, err := base64.StdEncoding.DecodeString(f.Data)
			if err != nil {
				return
			}
			// A page that redraws without changing sends the same picture again and again, byte for
			// byte: that is found without decoding it.
			if d.same(raw) {
				return
			}
			img, err := png.Decode(bytes.NewReader(raw))
			if err != nil {
				return
			}
			patches := d.changes(img)
			changed = len(patches) > 0
			if err := d.send(patches, out); err != nil {
				cancel()
			}
		}()
	})
	// A tab picked up again shows what it already shows at once: Chrome only sends a frame when
	// something is drawn, and a page left standing may draw nothing new for a while.
	if reused {
		var shot []byte
		if err := chromedp.Run(tab, chromedp.ActionFunc(func(ctx context.Context) error {
			var err error
			shot, err = page.CaptureScreenshot().WithFormat(page.CaptureScreenshotFormatPng).Do(ctx)
			return err
		})); err == nil {
			if img, err := png.Decode(bytes.NewReader(shot)); err == nil {
				_ = d.send(d.changes(img), out)
			}
		}
	}
	// PNG from Chrome rather than JPEG: lossless, so two frames of an unchanged page are the same
	// bytes and what changed can be found exactly. The JPEG is made here, of the changed part only.
	if err := chromedp.Run(tab, page.StartScreencast().WithFormat(page.ScreencastFormatPng).
		WithMaxWidth(int64(h.W)).WithMaxHeight(int64(h.H))); err != nil {
		out.problem("The dashboard would not stream: " + err.Error())
		return
	}

	go func() {
		<-sctx.Done()
		c.Close()
	}()
	go keepOnDashboards(sctx, tab, b.cfg.ha+h.Path, haOrigin(g.cfg.ha), allows, music, h.Name)
	if token != "" {
		mine := cfg
		mine.token = token
		go watchKey(sctx, keyWatch, func(ctx context.Context) error {
			_, _, err := showUsers.ask(ctx, mine)
			return err
		}, func() {
			turnedAway.Store(true)
			slog.Warn("Home Assistant turned the Show's own key away; its screen is closed", "name", h.Name)
			out.problem(keyTurnedAway)
			cancel()
		})
	}
	for lines.Scan() {
		var t touchMsg
		if json.Unmarshal(lines.Bytes(), &t) != nil {
			continue
		}
		if err := chromedp.Run(tab, chromedp.ActionFunc(func(ctx context.Context) error { return touch(ctx, t) })); err != nil {
			slog.Warn("touch", "err", err)
		}
		d.touched()
	}
}

// touch replays one of the device's touches on the page.
// keyTurnedAway is what a Show whose own key Home Assistant turns away is told instead of a page.
const keyTurnedAway = "Home Assistant turned this Show's key away, so it gets no dashboard. Is the Show disabled there?"

// keyWatch is how often a screen signed in as a Show's own user has Home Assistant asked whether its
// key still lets it in.
const keyWatch = time.Minute

// watchKey asks every so often whether a Show's key still lets it in, and calls gone once Home
// Assistant turns it away: a Show disabled in Home Assistant has its user switched off, which
// deletes its key, and its open screen goes too (GENBU-261). A Home Assistant that doesn't answer
// is no answer.
func watchKey(ctx context.Context, every time.Duration, ask func(context.Context) error, gone func()) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := ask(ctx); errors.Is(err, errTokenRefused) {
				gone()
				return
			}
		}
	}
}

func touch(ctx context.Context, t touchMsg) error {
	at := []*input.TouchPoint{{X: float64(t.X), Y: float64(t.Y)}}
	switch t.T {
	case "tap":
		if err := input.DispatchTouchEvent(input.TouchStart, at).Do(ctx); err != nil {
			return err
		}
		return input.DispatchTouchEvent(input.TouchEnd, []*input.TouchPoint{}).Do(ctx)
	case "down":
		return input.DispatchTouchEvent(input.TouchStart, at).Do(ctx)
	case "move":
		return input.DispatchTouchEvent(input.TouchMove, at).Do(ctx)
	case "up":
		return input.DispatchTouchEvent(input.TouchEnd, []*input.TouchPoint{}).Do(ctx)
	}
	return nil
}

// sender writes messages to one device; frames arrive from several goroutines.
type sender struct {
	mu sync.Mutex
	c  net.Conn
}

func (s *sender) send(kind byte, payload ...[]byte) error {
	n := 1
	for _, p := range payload {
		n += len(p)
	}
	msg := make([]byte, 4, 4+n)
	binary.BigEndian.PutUint32(msg, uint32(n))
	msg = append(msg, kind)
	for _, p := range payload {
		msg = append(msg, p...)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	_ = s.c.SetWriteDeadline(time.Now().Add(10 * time.Second))
	_, err := s.c.Write(msg)
	return err
}

// whose is what kindWhose says for a page signed in with token: "" is dashcast's own.
func whose(token string) string {
	if token != "" {
		return "own"
	}
	return "dashcast"
}

func (s *sender) problem(text string) { _ = s.send(kindProblem, []byte(text)) }

func (s *sender) picture(at image.Point, img image.Image) error {
	return s.jpeg(kindPicture, at, img)
}

// half sends img at half its size, to be drawn doubled.
func (s *sender) half(at image.Point, img image.Image) error {
	return s.jpeg(kindHalf, at, shrink(img))
}

func (s *sender) jpeg(kind byte, at image.Point, img image.Image) error {
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: quality}); err != nil {
		return err
	}
	var pos [4]byte
	binary.BigEndian.PutUint16(pos[0:], uint16(at.X))
	binary.BigEndian.PutUint16(pos[2:], uint16(at.Y))
	return s.send(kind, pos[:], buf.Bytes())
}

// shrink is img at half size, each pixel the average of the four it stands for.
func shrink(img image.Image) *image.RGBA {
	b := img.Bounds()
	src := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(src, src.Rect, img, b.Min, draw.Src)
	w, h := b.Dx()/2, b.Dy()/2
	out := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			i, j := src.PixOffset(2*x, 2*y), src.PixOffset(2*x, 2*y+1)
			o := out.PixOffset(x, y)
			for c := 0; c < 4; c++ {
				sum := int(src.Pix[i+c]) + int(src.Pix[i+4+c]) + int(src.Pix[j+c]) + int(src.Pix[j+4+c])
				out.Pix[o+c] = uint8((sum + 2) / 4)
			}
		}
	}
	return out
}

// differ remembers the last frame and says what in the next one is different.
type differ struct {
	mu   sync.Mutex
	last *image.RGBA

	// lastRaw is the last frame as Chrome sent it, and still how many frames in a row have changed
	// nothing on the screen.
	lastRaw []byte
	still   int

	// blurred is what went at half size and is still owed at full size, and sharpen the timer that
	// will send it once the screen settles.
	blurred []image.Rectangle
	sharpen *time.Timer

	// poke cuts the wait before the next frame short: a touch wants its answer on the screen now,
	// not after a still page's second.
	poke chan struct{}
}

// Pacing: after a frame that changed something, the next is asked for almost at once, so a page
// that moves - scrolling, a card opening - moves smoothly. After each frame that changed nothing the
// wait doubles, up to idlePace: a page that only thinks it is changing (a chart redrawing itself,
// an animation off the screen) costs next to nothing, and a real change still shows within a second.
const (
	busyPace = 40 * time.Millisecond
	idlePace = time.Second
)

// pace is how long to wait before asking for the next frame.
func (d *differ) pace(changed bool) time.Duration {
	d.mu.Lock()
	defer d.mu.Unlock()
	if changed {
		d.still = 0
		return busyPace
	}
	d.still = min(d.still+1, 16)
	return min(busyPace<<d.still, idlePace)
}

// wait is pace's wait, cut short by a touch (touched).
func (d *differ) wait(changed bool) {
	pause := d.pace(changed)
	d.mu.Lock()
	if d.poke == nil {
		d.poke = make(chan struct{}, 1)
	}
	poke := d.poke
	d.mu.Unlock()
	t := time.NewTimer(pause)
	defer t.Stop()
	select {
	case <-t.C:
	case <-poke:
	}
}

// touched is a touch on the page: the frame waiting goes at once, and the ones after it at the busy
// pace, so what the touch does shows as it happens.
func (d *differ) touched() {
	d.mu.Lock()
	d.still = 0
	if d.poke == nil {
		d.poke = make(chan struct{}, 1)
	}
	poke := d.poke
	d.mu.Unlock()
	select {
	case poke <- struct{}{}:
	default:
	}
}

// same is whether a frame is the last one again, byte for byte, keeping it for the next if not.
func (d *differ) same(raw []byte) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if bytes.Equal(raw, d.lastRaw) {
		return true
	}
	d.lastRaw = raw
	return false
}

// send sends a frame's changes: at full size, or at half size while most of the screen is moving.
func (d *differ) send(patches []patch, out *sender) error {
	d.mu.Lock()
	full := d.last.Rect
	d.mu.Unlock()
	area := 0
	for _, p := range patches {
		area += p.img.Bounds().Dx() * p.img.Bounds().Dy()
	}
	moving := len(patches) > 0 && float64(area) > soft*float64(full.Dx()*full.Dy())
	for _, p := range patches {
		if !moving {
			if err := out.picture(p.at, p.img); err != nil {
				return err
			}
			continue
		}
		// Even edges, so the doubled picture lands on the pixels it came from.
		r := image.Rectangle{Min: p.at, Max: p.at.Add(p.img.Bounds().Size())}
		r = image.Rect(r.Min.X&^1, r.Min.Y&^1, min((r.Max.X+1)&^1, full.Max.X), min((r.Max.Y+1)&^1, full.Max.Y))
		d.mu.Lock()
		img := d.last.SubImage(r)
		d.blurred = merge(d.blurred, r)
		d.mu.Unlock()
		if err := out.half(r.Min, img); err != nil {
			return err
		}
	}
	if moving {
		d.mu.Lock()
		if d.sharpen != nil {
			d.sharpen.Stop()
		}
		d.sharpen = time.AfterFunc(settle, func() { d.sharpenNow(out) })
		d.mu.Unlock()
	}
	return nil
}

// sharpenNow sends at full size what went at half, now the screen has stopped moving.
func (d *differ) sharpenNow(out *sender) {
	d.mu.Lock()
	rects := d.blurred
	d.blurred = nil
	last := d.last
	d.mu.Unlock()
	for _, r := range rects {
		if out.picture(r.Min, last.SubImage(r)) != nil {
			return
		}
	}
}

type patch struct {
	at  image.Point
	img image.Image
}

// tile is the grain changes are found at: fine enough that a clock ticking is a small picture,
// coarse enough that comparing is cheap.
const tile = 16

// changes is the parts of img that differ from the last frame, as few rectangles as cover them:
// the whole frame the first time, and nothing when nothing changed. Changes far apart - the clock
// at the top, a sensor at the bottom - go as separate pictures rather than one that spans both.
func (d *differ) changes(img image.Image) []patch {
	b := img.Bounds()
	cur := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(cur, cur.Rect, img, b.Min, draw.Src)

	d.mu.Lock()
	defer d.mu.Unlock()
	prev := d.last
	d.last = cur
	if prev == nil || prev.Rect != cur.Rect {
		return []patch{{at: image.Point{}, img: cur}}
	}

	var rects []image.Rectangle
	for ty := 0; ty < cur.Rect.Dy(); ty += tile {
		for tx := 0; tx < cur.Rect.Dx(); tx += tile {
			r := image.Rect(tx, ty, min(tx+tile, cur.Rect.Dx()), min(ty+tile, cur.Rect.Dy()))
			if !same(prev, cur, r) {
				rects = merge(rects, r)
			}
		}
	}
	out := make([]patch, 0, len(rects))
	for _, r := range rects {
		out = append(out, patch{at: r.Min, img: cur.SubImage(r)})
	}
	return out
}

func same(a, b *image.RGBA, r image.Rectangle) bool {
	for y := r.Min.Y; y < r.Max.Y; y++ {
		i := a.PixOffset(r.Min.X, y)
		j := i + r.Dx()*4
		if !bytes.Equal(a.Pix[i:j], b.Pix[i:j]) {
			return false
		}
	}
	return true
}

// merge adds r to the rectangles, joining it to any it touches or comes near, so a changed region
// arrives as one picture and unrelated ones stay apart.
func merge(rects []image.Rectangle, r image.Rectangle) []image.Rectangle {
	for {
		joined := false
		for i, have := range rects {
			if have.Inset(-tile).Overlaps(r) {
				r = r.Union(have)
				rects = append(rects[:i], rects[i+1:]...)
				joined = true
				break
			}
		}
		if !joined {
			return append(rects, r)
		}
	}
}

// keepOnDashboards looks at where the page is every second, and takes it back to its dashboard if it
// has got anywhere else: the page's own guard (browser.go) stops the frontend going there, and this
// is for whatever gets past it. On Music Assistant's panel it also takes Music Assistant's own page,
// in its frame, back out of the settings. It keeps the page connected to Home Assistant (see lost).
func keepOnDashboards(ctx context.Context, tab context.Context, home, origin string,
	allows func(context.Context, string) (bool, error), music bool, name string) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	var down lost
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		var where struct {
			Origin    string `json:"origin"`
			Path      string `json:"path"`
			Connected *bool  `json:"connected"`
		}
		if err := chromedp.Run(tab, chromedp.Evaluate(`({origin: location.origin, path: location.pathname,
			connected: document.querySelector("home-assistant")?.hass?.connected})`, &where)); err != nil {
			continue
		}
		switch down.next(where.Connected != nil && !*where.Connected) {
		case nudge:
			slog.Info("the page lost Home Assistant; reconnecting it", "name", name)
			_ = chromedp.Run(tab, chromedp.Evaluate(`document.querySelector("home-assistant")?.hass?.connection?.reconnect(true)`, nil))
		case reload:
			slog.Warn("the page stayed cut off from Home Assistant; reloading it", "name", name)
			_ = chromedp.Run(tab, chromedp.Reload())
		}
		if music {
			var out bool
			if chromedp.Run(tab, chromedp.Evaluate(musicSettingsOut, &out)) == nil && out {
				slog.Warn("Music Assistant's settings came up; taking it back to its library", "name", name)
			}
		}
		if where.Origin == origin {
			if ok, err := allows(ctx, where.Path); err != nil || ok {
				continue
			}
		}
		path := where.Origin + where.Path
		slog.Warn("the page left the dashboards; taking it back", "name", name, "was", path)
		_ = chromedp.Run(tab, chromedp.Navigate(home))
	}
}

// musicSettingsOut finds Music Assistant's frame - inside the panel's shadow roots, where a plain
// query does not reach - and, if it is on a settings page, sends it to its home page. It is the same
// origin as Home Assistant (ingress), so the top page can see and move it. True when it moved it.
const musicSettingsOut = `(() => {
  const frames = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.tagName === "IFRAME") frames.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  for (const f of frames) {
    try {
      const l = f.contentWindow.location;
      if (/^#\/settings/.test(l.hash)) { l.replace(l.pathname + l.search + "#/"); return true; }
    } catch (e) {}
  }
  return false;
})()`

// lost counts the seconds a page has been cut off from Home Assistant. The frontend reconnects by
// itself, mostly; but it can sit on "Connection lost. Reconnecting..." for good, as tabs that
// were frozen while parked (warm.go) used to. So after a few seconds it is told
// to reconnect at once, and a page still cut off after half a minute is loaded again. While Home
// Assistant itself is down that reload comes back every half minute, which is all a wait is.
type lost struct{ secs int }

type remedy int

const (
	wait remedy = iota
	nudge
	reload
)

const (
	nudgeAfter  = 3
	reloadAfter = 30
)

// next is what to do after one more second, cut off or not.
func (l *lost) next(cutOff bool) remedy {
	if !cutOff {
		l.secs = 0
		return wait
	}
	l.secs++
	switch l.secs {
	case nudgeAfter:
		return nudge
	case reloadAfter:
		l.secs = 0
		return reload
	}
	return wait
}
