package home

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/media"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// Room music routing belongs to Music Assistant. Jarvis Show only decides which MA player represents
// this room right now: the preferred player while it is available (the configured one, or the one in
// this Show's area, music_room.go), otherwise this Show's own MA player. Queue transfer and grouping
// remain Music Assistant operations.
const musicRouteEvery = 5 * time.Second

type musicRouteState struct {
	sync.Mutex
	primaryWasPlaying bool
	fallbackActive    bool
	activeOutput      string
	activeGroup       string
	activeMembers     []string

	// primaryState is what the preferred speaker said at the last poll while it was online, "" while it
	// was not or before the first poll: what tells a play started on it from one it came back with.
	primaryState string
	// activeAt is when activeOutput last was the preferred speaker playing, or a play sent to it.
	activeAt time.Time
}

var musicRoute musicRouteState

// musicSettle is how long a play sent to the preferred speaker counts as its, before Music Assistant
// has said it plays: a poll in between must not take the screen off it again.
const musicSettle = 15 * time.Second

// localCarried is whether this Show's own Music Assistant stream is what the room hears. Home
// Assistant's state of the Show's player can be stale for an hour (GENBU-167), Sendspin cannot.
var localCarried = func() bool { return media.Get().Carried() }

// forgetPrimary takes the screen off the preferred speaker once it has stopped, so the page and its
// buttons go back to whatever plays here. Caller holds musicRoute.
func (r *musicRouteState) forgetPrimary(primary string, now time.Time) {
	if r.activeOutput == primary && now.Sub(r.activeAt) >= musicSettle {
		r.activeOutput = ""
	}
}

// musicGroupedWith is whether Music Assistant has the player st among the members of a group with entity.
func musicGroupedWith(st hass.State, entity string) bool {
	members, _ := st.Attributes["group_members"].([]any)
	for _, m := range members {
		if s, _ := m.(string); s == entity {
			return true
		}
	}
	return false
}

// stopLocalMusic ends this Show's own Music Assistant queue, so a play on the room's speaker is the
// only one the room hears.
func stopLocalMusic(local string) {
	if err := hass.Get().Call("media_player", "media_stop", map[string]any{"entity_id": local}); err != nil {
		slog.Warn("music route: stopping this Show's own queue through Home Assistant failed; asking Sendspin", "err", err)
		media.Get().Transport(media.TransportStop)
	}
}

// MusicPlaybackView is observational state for the native full-screen music surface. Playback keeps
// working if Home Assistant cannot provide these extras; Sendspin remains the source of track/audio.
type MusicPlaybackView struct {
	Route    string
	Entity   string
	Output   string
	Rooms    []string
	Playing  bool
	Paused   bool
	Title    string
	Artist   string
	Album    string
	Position float64
	Duration float64
	Next     string
	// Favorite is whether Music Assistant has the song among its favorites, and TrackURI its address
	// there, which taking it out again needs. Both from the queue, so a poll behind a new song.
	Favorite bool
	TrackURI string
	// Station and StationURI are the radio station Music Assistant is playing, by its queue, when it is
	// one: what lets the radio page mark the station of its list that plays.
	Station    string
	StationURI string

	// PositionAt is when Position was true; a playing song has moved on since.
	PositionAt time.Time
}

// Elapsed is how far into the song it is at now: Position, moved on by the time since while playing.
func (v MusicPlaybackView) Elapsed(now time.Time) float64 {
	p := v.Position
	if v.Playing && !v.PositionAt.IsZero() {
		p += now.Sub(v.PositionAt).Seconds()
	}
	if v.Duration > 0 && p > v.Duration {
		p = v.Duration
	}
	return max(p, 0)
}

type musicPlaybackState struct {
	sync.Mutex
	view     MusicPlaybackView
	queueAt  time.Time
	queueFor string
	picture  string

	// expectPlaying is what a tap on play or pause asked for, held on the screen until Home Assistant
	// says the same or expectUntil has passed: a poll that lands before Music Assistant caught up
	// would otherwise flip the button back for a moment.
	expectPlaying bool
	expectUntil   time.Time
}

// musicExpectFor is how long the screen keeps showing what a tap asked for while Home Assistant
// still says otherwise.
const musicExpectFor = 4 * time.Second

var musicPlayback musicPlaybackState

func musicPlayerOnline(st hass.State) bool {
	switch strings.ToLower(strings.TrimSpace(st.State)) {
	case "", "unavailable", "unknown":
		return false
	default:
		return true
	}
}

func musicAssistantState(st hass.State) bool {
	app, _ := st.Attributes["app_id"].(string)
	kind, _ := st.Attributes["mass_player_type"].(string)
	return app == "music_assistant" && (kind == "" || kind == "player")
}

// musicOutput resolves a new explicit Play request. It never migrates an existing fallback session
// back to the preferred speaker merely because that speaker came online again.
func musicOutput() (target, local string, usingFallback bool, err error) {
	local, err = musicAssistantPlayer()
	if err != nil {
		return "", "", false, err
	}
	noLocal := errors.New("no Music Assistant player for this Jarvis Show")
	primary := musicPrimary()
	if primary == "" || primary == local {
		if local == "" {
			return "", "", false, noLocal
		}
		return local, local, false, nil
	}
	st, stateErr := hass.Get().State(primary)
	if stateErr != nil || !musicPlayerOnline(st) || !musicAssistantState(st) {
		if local == "" {
			return "", "", false, noLocal
		}
		return local, local, true, nil
	}
	return primary, local, false, nil
}

// PlayMusic starts a new Music Assistant request on this room's currently preferred live output. If a
// previous request failed over to the Show and the preferred speaker has returned, the old local
// fallback is stopped first so the new request cannot play from both places.
func (f *Feature) PlayMusic(mediaID string) (string, error) {
	mediaID = strings.TrimSpace(mediaID)
	if mediaID == "" {
		return "", errors.New("music request is empty")
	}
	if err := releaseActiveMusicGroup(); err != nil {
		return "", err
	}
	target, local, fallback, err := musicOutput()
	if err != nil {
		return "", err
	}

	musicRoute.Lock()
	wasFallback := musicRoute.fallbackActive
	musicRoute.Unlock()
	// Music the Show plays itself goes too, not only a fallback of Jarvis's: whatever started it, the
	// room hears one speaker (GENBU-167).
	if (wasFallback || localCarried()) && target != local {
		if err := hass.Get().Call("media_player", "media_stop", map[string]any{"entity_id": local}); err != nil {
			return "", fmt.Errorf("stop local fallback before returning to preferred speaker: %w", err)
		}
	}
	if err := hass.Get().Call("music_assistant", "play_media", map[string]any{
		"entity_id": target,
		"media_id":  mediaID,
	}); err != nil {
		return "", err
	}

	musicRoute.Lock()
	musicRoute.fallbackActive = fallback
	musicRoute.primaryWasPlaying = !fallback && target != local
	musicRoute.activeOutput = target
	musicRoute.activeAt = time.Now()
	musicRoute.activeGroup = ""
	musicRoute.activeMembers = nil
	musicRoute.Unlock()
	return target, nil
}

// musicRouteLoop watches only for the one automatic migration Jarvis owns: a preferred room speaker
// that was actively playing becomes unavailable. It never performs the reverse migration.
func (f *Feature) musicRouteLoop(ctx context.Context) {
	ticker := time.NewTicker(musicRouteEvery)
	defer ticker.Stop()
	f.musicRouteTick()
	f.musicPlaybackTick()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			f.musicRouteTick()
			f.musicPlaybackTick()
		case <-musicKick:
			f.musicPlaybackTick()
		}
	}
}

// musicKick asks the loop for Music Assistant's state now rather than at the next poll.
var musicKick = make(chan struct{}, 1)

func musicPlaybackSoon() {
	select {
	case musicKick <- struct{}{}:
	default:
	}
}

func (f *Feature) musicRouteTick() {
	if !hass.Get().Ready() {
		return
	}
	primary := musicPrimary()
	if primary == "" {
		return
	}
	// A Show that is no Music Assistant player of its own still shows what the room's speaker plays;
	// it only has nowhere to carry the music on when that speaker goes away.
	local, err := musicAssistantPlayer()
	if err != nil || local == primary {
		return
	}
	st, err := hass.Get().State(primary)
	if err != nil {
		return
	}

	now := time.Now()
	online := musicPlayerOnline(st)
	playing := online && musicAssistantState(st) && strings.EqualFold(st.State, "playing")
	musicRoute.Lock()
	// A play started on the room's speaker while it was online and quiet, by voice, its app or Music
	// Assistant, takes the room over from whatever this Show plays of its own. Coming back already
	// playing is not that: the speaker drops out and returns on its own.
	started := playing && musicRoute.primaryState != "" && !strings.EqualFold(musicRoute.primaryState, "playing")
	musicRoute.primaryState = ""
	if online {
		musicRoute.primaryState = st.State
	}
	if started && musicRoute.activeGroup == "" && local != "" && localCarried() && !musicGroupedWith(st, local) {
		musicRoute.Unlock()
		slog.Info("music route: the room's speaker started playing; stopping this Show's own queue", "speaker", primary)
		stopLocalMusic(local)
		musicRoute.Lock()
		musicRoute.fallbackActive = false
		musicRoute.primaryWasPlaying = true
		musicRoute.activeOutput, musicRoute.activeAt = primary, now
		musicRoute.Unlock()
		f.Changed.Emit(struct{}{})
		return
	}
	if musicRoute.fallbackActive {
		musicRoute.Unlock()
		return // sticky until the next explicit Play request
	}
	if online {
		if !musicAssistantState(st) {
			musicRoute.primaryWasPlaying = false
			musicRoute.forgetPrimary(primary, now)
			musicRoute.Unlock()
			return
		}
		musicRoute.primaryWasPlaying = playing
		if playing {
			musicRoute.activeOutput, musicRoute.activeAt = primary, now
		} else if !strings.EqualFold(st.State, "paused") {
			// Stopped or idle: the screen goes back to what plays here. A pause stays the speaker's,
			// so play on the screen picks it up again.
			musicRoute.forgetPrimary(primary, now)
		}
		musicRoute.Unlock()
		return
	}
	shouldFailOver := musicRoute.primaryWasPlaying
	if !shouldFailOver {
		musicRoute.forgetPrimary(primary, now)
	}
	musicRoute.Unlock()
	if !shouldFailOver || local == "" {
		return
	}

	if err := hass.Get().Call("music_assistant", "transfer_queue", map[string]any{
		"entity_id":     local,
		"source_player": primary,
		"auto_play":     true,
	}); err != nil {
		slog.Warn("music route: preferred speaker disappeared; queue transfer to Jarvis Show failed", "err", err)
		return
	}
	musicRoute.Lock()
	musicRoute.primaryWasPlaying = false
	musicRoute.fallbackActive = true
	musicRoute.activeOutput = local
	musicRoute.Unlock()
	slog.Info("music route: preferred speaker disappeared; continuing on Jarvis Show", "player", local)
	f.Changed.Emit(struct{}{})
}

// MusicOutput reports the route for display/diagnostics. It does not trigger a route change.
func (f *Feature) MusicOutput() (preferred, active string, fallback bool) {
	preferred = musicPrimary()
	musicRoute.Lock()
	active, fallback = musicRoute.activeOutput, musicRoute.fallbackActive
	musicRoute.Unlock()
	return
}

// MusicGroupOutput reports the currently explicit named-group route for the full-screen music UI.
// It is observational only and never changes routing.
func (f *Feature) MusicGroupOutput() (group string, members []string) {
	musicRoute.Lock()
	group = musicRoute.activeGroup
	members = append([]string(nil), musicRoute.activeMembers...)
	musicRoute.Unlock()
	return
}

func musicRoomNameFor(entity string) string {
	entity = strings.TrimSpace(entity)
	if entity == "" {
		return ""
	}
	cfg := config.Get().Home
	for _, r := range cfg.MusicRooms {
		if entity == strings.TrimSpace(r.Primary) || entity == strings.TrimSpace(r.Fallback) {
			return r.Name
		}
	}
	if active, _ := musicAssistantPlayer(); entity == active && cfg.MusicRoom != "" {
		return cfg.MusicRoom
	}
	return musicAreaName(entity)
}

// musicAreas remembers the Home Assistant area of each speaker the page has named. A speaker's own
// name is often something only its setup app ever showed ("livingRoom"); its area is the room as
// the house calls it. Asked once per speaker, and an answer of none is kept too.
var musicAreas struct {
	sync.Mutex
	name map[string]string
}

func musicAreaName(entity string) string {
	musicAreas.Lock()
	name, ok := musicAreas.name[entity]
	musicAreas.Unlock()
	if ok {
		return name
	}
	out, err := hass.Get().Render("{{ area_name(" + strconv.Quote(entity) + ") or '' }}")
	if err != nil {
		return "" // not kept: Home Assistant may answer next time
	}
	if out == "None" {
		out = ""
	}
	musicAreas.Lock()
	if musicAreas.name == nil {
		musicAreas.name = map[string]string{}
	}
	musicAreas.name[entity] = out
	musicAreas.Unlock()
	return out
}

func humanMusicName(v string) string {
	v = strings.TrimSpace(v)
	if v == "" {
		return ""
	}
	v = strings.TrimPrefix(v, "media_player.")
	v = strings.ReplaceAll(v, "_", " ")
	return v
}

func floatAttr(st hass.State, name string) float64 {
	switch v := st.Attributes[name].(type) {
	case float64:
		return v
	case float32:
		return float64(v)
	case int:
		return float64(v)
	case json.Number:
		f, _ := v.Float64()
		return f
	case string:
		f, _ := strconv.ParseFloat(v, 64)
		return f
	default:
		return 0
	}
}

func (f *Feature) musicPlaybackTick() {
	if !hass.Get().Ready() {
		return
	}
	group, members := f.MusicGroupOutput()
	_, active, _ := f.MusicOutput()
	// The page draws what Sendspin carries here, so its buttons go to this Show's own player too: the
	// room's speaker may still be the route from a song it played earlier (GENBU-167). A group's leader
	// is what a group answers to, so a group keeps it.
	if group == "" && localCarried() {
		if local, _ := musicAssistantPlayer(); local != "" {
			active = local
		}
	}
	if active == "" {
		musicPlayback.Lock()
		hadPicture := musicPlayback.picture != ""
		musicPlayback.view = MusicPlaybackView{}
		musicPlayback.queueAt, musicPlayback.queueFor = time.Time{}, ""
		musicPlayback.picture = ""
		musicPlayback.Unlock()
		if hadPicture && !media.Get().Carried() {
			RemoteArt(nil, nil)
		}
		refreshLyrics(queueSong{})
		return
	}

	view := MusicPlaybackView{Route: group, Entity: active, Output: humanMusicName(active)}
	if st, err := hass.Get().State(active); err == nil {
		if friendly, _ := st.Attributes["friendly_name"].(string); strings.TrimSpace(friendly) != "" {
			view.Output = strings.TrimSpace(friendly)
		}
		switch strings.ToLower(strings.TrimSpace(st.State)) {
		case "playing":
			view.Playing = true
		case "paused":
			view.Paused = true
		}
		view.Title, _ = st.Attributes["media_title"].(string)
		view.Artist, _ = st.Attributes["media_artist"].(string)
		view.Album, _ = st.Attributes["media_album_name"].(string)
		// entity_picture is often Music Assistant's own image proxy, which a Show on another network
		// can't reach. entity_picture_local is Home Assistant serving the same picture, so the Show
		// asks for that. Its token changes every few minutes, which is why the plain picture decides
		// whether the cover changed.
		picture, _ := st.Attributes["entity_picture"].(string)
		fetch, _ := st.Attributes["entity_picture_local"].(string)
		if fetch == "" {
			fetch = picture
		}
		if !media.Get().Carried() {
			musicPlayback.Lock()
			changed := picture != musicPlayback.picture
			if changed {
				musicPlayback.picture = picture
			}
			musicPlayback.Unlock()
			if changed {
				if picture == "" {
					RemoteArt(nil, nil)
				} else if b, err := hass.Get().FetchURL(fetch); err == nil {
					RemoteArt(b, nil)
				} else {
					// The last song's cover beside this song's title would be wrong; the drawn notes are not.
					RemoteArt(nil, nil)
					slog.Info("music route: cover art unavailable", "err", err)
				}
			}
		}
		view.Position = floatAttr(st, "media_position")
		view.Duration = floatAttr(st, "media_duration")
		view.PositionAt = time.Now()
		if view.Playing && view.Duration > 0 {
			if updated, _ := st.Attributes["media_position_updated_at"].(string); updated != "" {
				if at, err := time.Parse(time.RFC3339Nano, updated); err == nil {
					view.Position += time.Since(at).Seconds()
					if view.Position > view.Duration {
						view.Position = view.Duration
					}
				}
			}
		}
	}
	seenRoom := map[string]bool{}
	for _, member := range members {
		name := musicRoomNameFor(member)
		if name == "" {
			name = humanMusicName(member)
		}
		if name != "" && !seenRoom[name] {
			seenRoom[name] = true
			view.Rooms = append(view.Rooms, name)
		}
	}
	if group == "" {
		if room := musicRoomNameFor(active); room != "" {
			view.Rooms = []string{room}
		}
	}

	musicPlayback.Lock()
	// A new song is asked about at once, so its words and what comes next are not a poll behind.
	needQueue := musicPlayback.queueFor != active || musicPlayback.view.Title != view.Title ||
		time.Since(musicPlayback.queueAt) >= 15*time.Second
	if !needQueue {
		view.Next, view.Favorite, view.TrackURI = musicPlayback.view.Next, musicPlayback.view.Favorite, musicPlayback.view.TrackURI
		view.Station, view.StationURI = musicPlayback.view.Station, musicPlayback.view.StationURI
	}
	musicPlayback.Unlock()
	var song *queueSong
	if needQueue {
		if next, cur, err := musicQueue(active); err == nil {
			view.Next, song = next, &cur
			view.Favorite, view.TrackURI = cur.Favorite, cur.URI
			view.Station, view.StationURI = cur.Station, cur.StationURI
			musicPlayback.Lock()
			musicPlayback.queueFor, musicPlayback.queueAt = active, time.Now()
			musicPlayback.Unlock()
		}
	}
	musicPlayback.Lock()
	holdExpected(&view, musicPlayback.view, &musicPlayback.expectPlaying, &musicPlayback.expectUntil, time.Now())
	musicPlayback.view = view
	musicPlayback.Unlock()
	f.Changed.Emit(struct{}{})
	if song != nil {
		refreshLyrics(*song)
	}
}

// musicQueue is the name of the song after this one, and what the queue knows of this one.
func musicQueue(entity string) (next string, cur queueSong, err error) {
	raw, err := hass.Get().CallResponse("music_assistant", "get_queue", map[string]any{"entity_id": entity})
	if err != nil {
		return "", queueSong{}, err
	}
	type item struct {
		Name      string `json:"name"`
		MediaItem *struct {
			Name      string `json:"name"`
			MediaType string `json:"media_type"`
			URI       string `json:"uri"`
			Favorite  bool   `json:"favorite"`
		} `json:"media_item"`
	}
	var response map[string]struct {
		CurrentItem *item `json:"current_item"`
		NextItem    *item `json:"next_item"`
	}
	if err := json.Unmarshal(raw, &response); err != nil {
		return "", queueSong{}, err
	}
	q, ok := response[entity]
	if !ok {
		return "", queueSong{}, nil
	}
	if q.NextItem != nil {
		next = strings.TrimSpace(q.NextItem.Name)
	}
	if c := q.CurrentItem; c != nil {
		cur.Name = strings.TrimSpace(c.Name)
		if c.MediaItem != nil {
			// The bare song name, as the player reports it in media_title; the queue's own name has
			// the artist in front.
			cur.Name = strings.TrimSpace(c.MediaItem.Name)
			if c.MediaItem.MediaType == "radio" {
				cur.Station, cur.StationURI = cur.Name, c.MediaItem.URI
			}
			if c.MediaItem.MediaType == "track" {
				cur.URI, cur.Favorite = c.MediaItem.URI, c.MediaItem.Favorite
			}
		}
	}
	return next, cur, nil
}

// MusicPlayback reports optional routing/progress/queue context for the native music page. It never
// performs network I/O; the route loop refreshes this cache in the background.
func (f *Feature) MusicPlayback() MusicPlaybackView {
	musicPlayback.Lock()
	defer musicPlayback.Unlock()
	v := musicPlayback.view
	v.Rooms = append([]string(nil), v.Rooms...)
	return v
}

// MusicTransport controls the real Music Assistant route shown by the native music page. It falls
// back to the local media transport only when no routed MA player is active.
func (f *Feature) MusicTransport(t media.Transport) error {
	v := f.MusicPlayback()
	if v.Entity == "" || (!v.Playing && !v.Paused) {
		media.Get().Transport(t)
		return nil
	}
	service := ""
	switch t {
	case media.TransportPrevious:
		service = "media_previous_track"
	case media.TransportNext:
		service = "media_next_track"
	case media.TransportPlay:
		service = "media_play"
	case media.TransportPause:
		service = "media_pause"
	case media.TransportToggle:
		if v.Paused {
			service = "media_play"
		} else {
			service = "media_pause"
		}
	case media.TransportStop:
		service = "media_stop"
	}
	if service == "" {
		return nil
	}
	return hass.Get().Call("media_player", service, map[string]any{"entity_id": v.Entity})
}

// musicTaps carries the music page's transport buttons to Music Assistant one at a time and in order,
// off the touch loop: a Home Assistant action waits on the server, and the screen must not wait with it.
var (
	musicTaps     = make(chan media.Transport, 8)
	musicTapsOnce sync.Once
)

// MusicTap is a transport button pressed on the screen. It never waits: what the tap asked for shows at
// once, play or pause and the song's position, and the action itself goes to Music Assistant behind it.
// The screen then asks for the real state straight away and once more a moment later, rather than at
// the next poll five seconds on.
func (f *Feature) MusicTap(t media.Transport) {
	musicPlayback.Lock()
	v := &musicPlayback.view
	routed := v.Entity != "" && (v.Playing || v.Paused)
	if routed {
		now := time.Now()
		if t == media.TransportToggle {
			// Settled here, against what the screen shows: the queue behind may still hold an earlier
			// tap, and a toggle read after that one would undo it.
			t = media.TransportPause
			if v.Paused {
				t = media.TransportPlay
			}
		}
		switch t {
		case media.TransportPlay, media.TransportPause:
			play := t == media.TransportPlay
			v.Position, v.PositionAt = v.Elapsed(now), now
			v.Playing, v.Paused = play, !play
			musicPlayback.expectPlaying, musicPlayback.expectUntil = play, now.Add(musicExpectFor)
		case media.TransportNext, media.TransportPrevious:
			v.Position, v.PositionAt = 0, now
		}
	}
	musicPlayback.Unlock()
	if routed {
		f.Changed.Emit(struct{}{})
	}
	musicTapsOnce.Do(func() { go f.musicTapLoop() })
	select {
	case musicTaps <- t:
	default:
		slog.Warn("music: transport taps queued up, this one dropped", "transport", t)
	}
}

func (f *Feature) musicTapLoop() {
	for t := range musicTaps {
		if err := f.MusicTransport(t); err != nil {
			slog.Warn("music: transport failed", "transport", t, "err", err)
			musicPlayback.Lock()
			musicPlayback.expectUntil = time.Time{} // what the tap showed did not happen
			musicPlayback.Unlock()
		}
		musicPlaybackSoon()
		time.AfterFunc(time.Second, musicPlaybackSoon)
	}
}

// holdExpected keeps what a tap asked for in view while Home Assistant still reports the state from
// before it, until it agrees or the wait is over; was is the view the screen showed until now.
func holdExpected(view *MusicPlaybackView, was MusicPlaybackView, playing *bool, until *time.Time, now time.Time) {
	if until.IsZero() {
		return
	}
	if !now.Before(*until) || !(view.Playing || view.Paused) || view.Playing == *playing {
		*until = time.Time{}
		return
	}
	view.Playing, view.Paused = *playing, !*playing
	view.Position, view.PositionAt = was.Position, was.PositionAt
}

func (f *Feature) stopRoutedMusic() bool {
	v := f.MusicPlayback()
	if v.Entity == "" || (!v.Playing && !v.Paused) {
		return false
	}
	if err := f.MusicTransport(media.TransportStop); err != nil {
		// Not stopped, so not routed: the stop goes through this Show as well rather than nowhere.
		slog.Warn("music route: stopping active Music Assistant output failed", "entity", v.Entity, "err", err)
		return false
	}
	if v.Route != "" {
		if err := releaseActiveMusicGroup(); err != nil {
			slog.Warn("music route: releasing stopped temporary group failed", "err", err)
		}
	}
	return true
}

// MusicPlaybackArt is the best cover art available for the current MA route. Sendspin supplies it
// when this Show is an output; an external preferred player falls back to its HA entity_picture.
func (f *Feature) MusicPlaybackArt() (art, thumb *image.RGBA) { return remoteArt() }
