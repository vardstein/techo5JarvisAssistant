package home

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

const (
	testLocalMA = "media_player.living_room_jarvis"
	testPrimary = "media_player.living_room_sonos"
)

type musicServiceCall struct {
	domain  string
	service string
	data    map[string]any
}

type musicHAFake struct {
	mu     sync.Mutex
	states map[string]hass.State
	calls  []musicServiceCall
	queue  map[string]any // what get_queue answers for the player, when a test sets it
}

func newMusicHAFake() *musicHAFake {
	return &musicHAFake{states: map[string]hass.State{}}
}

func (f *musicHAFake) setState(entity, state, friendly string) {
	attrs := map[string]any{
		"app_id":           "music_assistant",
		"mass_player_type": "player",
	}
	if friendly != "" {
		attrs["friendly_name"] = friendly
	}
	f.mu.Lock()
	f.states[entity] = hass.State{State: state, Attributes: attrs}
	f.mu.Unlock()
}

func (f *musicHAFake) clearCalls() {
	f.mu.Lock()
	f.calls = nil
	f.mu.Unlock()
}

func (f *musicHAFake) serviceCalls() []musicServiceCall {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]musicServiceCall(nil), f.calls...)
}

func (f *musicHAFake) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.Method == http.MethodGet && r.URL.Path == "/api/states" {
		f.mu.Lock()
		states := make([]map[string]any, 0, len(f.states))
		for entity, st := range f.states {
			states = append(states, map[string]any{
				"entity_id":  entity,
				"state":      st.State,
				"attributes": st.Attributes,
			})
		}
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(states)
		return
	}
	if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/api/states/") {
		entity := strings.TrimPrefix(r.URL.Path, "/api/states/")
		f.mu.Lock()
		st, ok := f.states[entity]
		f.mu.Unlock()
		if !ok {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(st)
		return
	}
	if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/api/services/") {
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/services/"), "/")
		if len(parts) != 2 {
			http.Error(w, "bad service path", http.StatusBadRequest)
			return
		}
		var data map[string]any
		if err := json.NewDecoder(r.Body).Decode(&data); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		f.mu.Lock()
		f.calls = append(f.calls, musicServiceCall{domain: parts[0], service: parts[1], data: data})
		f.mu.Unlock()

		if parts[0] == "music_assistant" && parts[1] == "get_queue" {
			entity, _ := data["entity_id"].(string)
			f.mu.Lock()
			queue := f.queue
			f.mu.Unlock()
			if queue != nil {
				_ = json.NewEncoder(w).Encode(map[string]any{"service_response": map[string]any{entity: queue}})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"service_response": map[string]any{
					entity: map[string]any{"next_item": map[string]any{"name": "Next Track"}},
				},
			})
			return
		}
		_, _ = w.Write([]byte(`{}`))
		return
	}
	http.NotFound(w, r)
}

func setupMusicFailoverTest(t *testing.T) (*Feature, *musicHAFake) {
	t.Helper()
	fake := newMusicHAFake()
	fake.setState(testLocalMA, "idle", "Living Room Jarvis")
	srv := httptest.NewServer(fake)
	t.Cleanup(srv.Close)

	config.Use(filepath.Join(t.TempDir(), "state.json"))
	config.Started(config.Device{Name: "Living Room Jarvis"})
	if err := config.Set().Home().Radio(config.Radio{Speaker: "media_player.living_room_speaker"}); err != nil {
		t.Fatal(err)
	}

	oldPath := hass.Path
	hass.Path = filepath.Join(t.TempDir(), "hass.json")
	t.Cleanup(func() { hass.Path = oldPath })
	if err := hass.Get().Set(srv.URL, "test-token"); err != nil {
		t.Fatal(err)
	}

	musicRoute = musicRouteState{}
	musicPlayback = musicPlaybackState{}
	oldRoom := roomPlayer
	roomPlayer = func() string { return "" }
	t.Cleanup(func() { roomPlayer = oldRoom })
	return &Feature{}, fake
}

func requireCall(t *testing.T, got musicServiceCall, domain, service, entity string) {
	t.Helper()
	if got.domain != domain || got.service != service {
		t.Fatalf("service call = %s.%s, want %s.%s", got.domain, got.service, domain, service)
	}
	if value, _ := got.data["entity_id"].(string); value != entity {
		t.Fatalf("%s.%s entity = %q, want %q", domain, service, value, entity)
	}
}

func TestMusicPrimaryOfflineAtStartUsesJarvisFallback(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	fake.setState(testPrimary, "unavailable", "Living Room Sonos")
	if err := config.Set().Home().MusicPrimary(testPrimary); err != nil {
		t.Fatal(err)
	}

	target, err := f.PlayMusic("library://album/offline-start")
	if err != nil {
		t.Fatal(err)
	}
	if target != testLocalMA {
		t.Fatalf("target = %q, want local fallback %q", target, testLocalMA)
	}
	_, active, fallback := f.MusicOutput()
	if active != testLocalMA || !fallback {
		t.Fatalf("route = active %q fallback %v, want local fallback", active, fallback)
	}
	calls := fake.serviceCalls()
	if len(calls) != 1 {
		t.Fatalf("service calls = %d, want 1: %#v", len(calls), calls)
	}
	requireCall(t, calls[0], "music_assistant", "play_media", testLocalMA)

	fake.setState(testLocalMA, "playing", "Living Room Jarvis")
	f.musicPlaybackTick()
	view := f.MusicPlayback()
	if view.Entity != testLocalMA || view.Output != "Living Room Jarvis" {
		t.Fatalf("Now Playing reports entity=%q output=%q, want active Jarvis fallback", view.Entity, view.Output)
	}
	if view.Entity == testPrimary {
		t.Fatal("Now Playing claimed the unavailable preferred speaker")
	}
}

func TestMusicPrimaryLossMidPlaybackTransfersAndStaysFallback(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	fake.setState(testPrimary, "playing", "Living Room Sonos")
	if err := config.Set().Home().MusicPrimary(testPrimary); err != nil {
		t.Fatal(err)
	}
	if target, err := f.PlayMusic("library://track/start-primary"); err != nil || target != testPrimary {
		t.Fatalf("initial PlayMusic = %q, %v; want %q", target, err, testPrimary)
	}

	fake.clearCalls()
	fake.setState(testPrimary, "unavailable", "Living Room Sonos")
	f.musicRouteTick()
	calls := fake.serviceCalls()
	if len(calls) != 1 {
		t.Fatalf("failover calls = %d, want one transfer_queue: %#v", len(calls), calls)
	}
	requireCall(t, calls[0], "music_assistant", "transfer_queue", testLocalMA)
	if source, _ := calls[0].data["source_player"].(string); source != testPrimary {
		t.Fatalf("transfer source = %q, want %q", source, testPrimary)
	}
	if autoplay, _ := calls[0].data["auto_play"].(bool); !autoplay {
		t.Fatal("failover transfer did not request autoplay")
	}
	_, active, fallback := f.MusicOutput()
	if active != testLocalMA || !fallback {
		t.Fatalf("after loss route = active %q fallback %v, want local sticky fallback", active, fallback)
	}

	fake.clearCalls()
	fake.setState(testPrimary, "playing", "Living Room Sonos")
	f.musicRouteTick()
	if calls := fake.serviceCalls(); len(calls) != 0 {
		t.Fatalf("primary recovery forced a mid-session migration: %#v", calls)
	}
	_, active, fallback = f.MusicOutput()
	if active != testLocalMA || !fallback {
		t.Fatalf("recovered primary changed sticky route = active %q fallback %v", active, fallback)
	}
}

func TestNextExplicitPlayRestoresRecoveredPrimary(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	fake.setState(testPrimary, "idle", "Living Room Sonos")
	if err := config.Set().Home().MusicPrimary(testPrimary); err != nil {
		t.Fatal(err)
	}
	musicRoute.fallbackActive = true
	musicRoute.activeOutput = testLocalMA

	fake.clearCalls()
	target, err := f.PlayMusic("library://track/next-command")
	if err != nil {
		t.Fatal(err)
	}
	if target != testPrimary {
		t.Fatalf("target = %q, want recovered primary %q", target, testPrimary)
	}
	calls := fake.serviceCalls()
	if len(calls) != 2 {
		t.Fatalf("service calls = %d, want stop-local then play-primary: %#v", len(calls), calls)
	}
	requireCall(t, calls[0], "media_player", "media_stop", testLocalMA)
	requireCall(t, calls[1], "music_assistant", "play_media", testPrimary)
	_, active, fallback := f.MusicOutput()
	if active != testPrimary || fallback {
		t.Fatalf("route = active %q fallback %v, want recovered primary", active, fallback)
	}
}

func TestRoomWithoutPreferredSpeakerUsesJarvisAsPrimary(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	if err := config.Set().Home().MusicPrimary(""); err != nil {
		t.Fatal(err)
	}

	target, err := f.PlayMusic("library://track/local-primary")
	if err != nil {
		t.Fatal(err)
	}
	if target != testLocalMA {
		t.Fatalf("target = %q, want local MA player %q", target, testLocalMA)
	}
	_, active, fallback := f.MusicOutput()
	if active != testLocalMA || fallback {
		t.Fatalf("route = active %q fallback %v, local player should be primary rather than failure fallback", active, fallback)
	}
	calls := fake.serviceCalls()
	if len(calls) != 1 {
		t.Fatalf("service calls = %d, want 1: %#v", len(calls), calls)
	}
	requireCall(t, calls[0], "music_assistant", "play_media", testLocalMA)
}

func TestNamedGroupSubstitutesOfflineRoomAndReportsRealMembers(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	bedroomPrimary := "media_player.bedroom_sonos"
	bedroomFallback := "media_player.bedroom_jarvis"
	fake.setState(testPrimary, "unavailable", "Living Room Sonos")
	fake.setState(testLocalMA, "playing", "Living Room Jarvis")
	fake.setState(bedroomPrimary, "idle", "Bedroom Sonos")
	fake.setState(bedroomFallback, "idle", "Bedroom Jarvis")

	rooms := []config.MusicRoomRoute{
		{Name: "living", Primary: testPrimary, Fallback: testLocalMA},
		{Name: "bedroom", Primary: bedroomPrimary, Fallback: bedroomFallback},
	}
	groups := []config.MusicGroup{{Name: "wohnung", Aliases: []string{"ganze wohnung"}, Rooms: []string{"living", "bedroom"}}}
	if err := config.Set().Home().MusicRouting("living", rooms, groups); err != nil {
		t.Fatal(err)
	}

	name, outputs, err := f.PlayMusicGroup("ganze wohnung", "library://playlist/whole-home")
	if err != nil {
		t.Fatal(err)
	}
	wantOutputs := []string{testLocalMA, bedroomPrimary}
	if name != "wohnung" || !reflect.DeepEqual(outputs, wantOutputs) {
		t.Fatalf("group = %q %#v, want wohnung %#v", name, outputs, wantOutputs)
	}
	for _, output := range outputs {
		if output == testPrimary {
			t.Fatal("offline preferred living-room speaker leaked into resolved group")
		}
	}

	calls := fake.serviceCalls()
	if len(calls) != 2 {
		t.Fatalf("service calls = %d, want join then play: %#v", len(calls), calls)
	}
	requireCall(t, calls[0], "media_player", "join", testLocalMA)
	requireCall(t, calls[1], "music_assistant", "play_media", testLocalMA)
	members, ok := calls[0].data["group_members"].([]any)
	if !ok || len(members) != 1 || members[0] != bedroomPrimary {
		t.Fatalf("join members = %#v, want only healthy bedroom primary", calls[0].data["group_members"])
	}

	f.musicPlaybackTick()
	view := f.MusicPlayback()
	if view.Route != "wohnung" || view.Entity != testLocalMA {
		t.Fatalf("Now Playing route=%q entity=%q, want group leader %q", view.Route, view.Entity, testLocalMA)
	}
	if !reflect.DeepEqual(view.Rooms, []string{"living", "bedroom"}) {
		t.Fatalf("Now Playing rooms = %#v, want actual resolved rooms", view.Rooms)
	}
}
