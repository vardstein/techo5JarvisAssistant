package home

import (
	"encoding/json"
	"errors"
	"slices"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

func TestLibraryStationsAsHomeAssistantSendsThem(t *testing.T) {
	// The shape of music_assistant.get_library's answer, as Home Assistant 2026.10 gives it.
	raw := json.RawMessage(`{"items":[
		{"media_type":"radio","uri":"library://radio/1","name":"Hitradio  FFH","image":null,"favorite":false},
		{"media_type":"radio","uri":"library://radio/2","name":"Radio Paradise","image":"https://img.example.org/rp.png","favorite":true},
		{"media_type":"track","uri":"library://track/9","name":"A song","favorite":true},
		{"media_type":"radio","uri":"library://radio/3","name":"hitradio ffh","favorite":true},
		{"media_type":"radio","uri":"","name":"No address"},
		{"media_type":"radio","uri":"library://radio/4","name":"  "},
		{"media_type":"radio","uri":"library://radio/5","name":"SWR3","image":"/local/swr3.png","favorite":false}
	],"limit":200,"offset":0,"order_by":"name","media_type":"radio"}`)
	got, err := libraryStations(raw)
	if err != nil {
		t.Fatal(err)
	}
	want := []station{
		{Name: "Radio Paradise", ID: "library://radio/2", Kind: maKind, Thumb: "https://img.example.org/rp.png"},
		{Name: "Hitradio FFH", ID: "library://radio/1", Kind: maKind},
		{Name: "SWR3", ID: "library://radio/5", Kind: maKind, Thumb: "/local/swr3.png"},
	}
	if !slices.Equal(got, want) {
		t.Fatalf("stations\n got %+v\nwant %+v", got, want)
	}
}

func TestLibraryStationsEmptyAndBroken(t *testing.T) {
	if got, err := libraryStations(json.RawMessage(`{"items":[]}`)); err != nil || len(got) != 0 {
		t.Fatalf("empty library: %v %v", got, err)
	}
	if _, err := libraryStations(json.RawMessage(`[1,2]`)); err == nil {
		t.Fatal("a list instead of the answer read without an error")
	}
}

func TestMAImage(t *testing.T) {
	for _, c := range []struct {
		name string
		v    any
		more []maPicture
		want string
	}{
		{"plain address", "https://img.example.org/a.png", nil, "https://img.example.org/a.png"},
		{"Home Assistant path", "/api/media_player_proxy/x", nil, "/api/media_player_proxy/x"},
		{"provider's own path", "tunein/s123", nil, ""},
		{"picture on the web", map[string]any{"path": "https://img.example.org/b.png", "provider": "tunein"}, nil, "https://img.example.org/b.png"},
		{"picture in a provider's files", map[string]any{"path": "/media/logo.png", "provider": "filesystem"}, nil, ""},
		{"metadata, thumb first", nil, []maPicture{
			{Type: "fanart", Path: "https://img.example.org/fan.jpg"},
			{Type: "thumb", Path: "https://img.example.org/thumb.jpg"},
		}, "https://img.example.org/thumb.jpg"},
		{"metadata, any when no thumb", nil, []maPicture{
			{Type: "thumb", Path: "/data/thumb.jpg"},
			{Type: "logo", Path: "https://img.example.org/logo.png"},
		}, "https://img.example.org/logo.png"},
		{"nothing", nil, nil, ""},
	} {
		if got := maImage(c.v, c.more); got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

// fakeMA answers music/browse by path, as Music Assistant's API would.
func fakeMA(t *testing.T, pages map[string]string) maCall {
	return func(command string, args map[string]any) (json.RawMessage, error) {
		if command != "music/browse" {
			t.Fatalf("unexpected command %q", command)
		}
		path, _ := args["path"].(string)
		page, ok := pages[path]
		if !ok {
			return nil, errors.New("no such folder: " + path)
		}
		return json.RawMessage(page), nil
	}
}

func TestTuneInLocal(t *testing.T) {
	call := fakeMA(t, map[string]string{
		"": `[{"item_id":"root","provider":"library","name":"Library","path":"library://"},
		      {"item_id":"root","provider":"tunein--a1b2","name":"TuneIn","path":"tunein--a1b2://"}]`,
		"tunein--a1b2://": `[{"item_id":"presets","provider":"tunein--a1b2","name":"My presets","path":"tunein--a1b2://presets"},
		                     {"item_id":"local","provider":"tunein--a1b2","name":"Local Radio","path":"tunein--a1b2://local"}]`,
		"tunein--a1b2://local": `[
			{"item_id":"s1","media_type":"radio","name":"Hitradio FFH","uri":"tunein--a1b2://radio/s1",
			 "metadata":{"images":[{"type":"thumb","path":"https://cdn.example.org/s1.png","remotely_accessible":true}]}},
			{"item_id":"s1b","media_type":"radio","name":"HITRADIO FFH","uri":"tunein--a1b2://radio/s1b"},
			{"item_id":"f","media_type":"folder","name":"More","path":"tunein--a1b2://local/more"},
			{"item_id":"s2","media_type":"radio","name":"hr3","uri":"tunein--a1b2://radio/s2","image":{"path":"https://cdn.example.org/s2.png"}}
		]`,
	})
	got, err := tuneInLocal(call)
	if err != nil {
		t.Fatal(err)
	}
	want := []station{
		{Name: "Hitradio FFH", ID: "tunein--a1b2://radio/s1", Kind: maKind, Thumb: "https://cdn.example.org/s1.png"},
		{Name: "hr3", ID: "tunein--a1b2://radio/s2", Kind: maKind, Thumb: "https://cdn.example.org/s2.png"},
	}
	if !slices.Equal(got, want) {
		t.Fatalf("stations\n got %+v\nwant %+v", got, want)
	}
}

func TestTuneInLocalWithoutIt(t *testing.T) {
	for _, c := range []struct {
		name  string
		pages map[string]string
		want  string
	}{
		{"no TuneIn", map[string]string{"": `[{"provider":"library","path":"library://"}]`}, "TuneIn is not in Music Assistant"},
		{"no local folder", map[string]string{
			"":          `[{"provider":"tunein","path":"tunein://"}]`,
			"tunein://": `[{"item_id":"presets","path":"tunein://presets"}]`,
		}, "TuneIn has no stations near home"},
		{"empty local folder", map[string]string{
			"":               `[{"provider":"tunein","path":"tunein://"}]`,
			"tunein://":      `[{"item_id":"local","path":"tunein://local"}]`,
			"tunein://local": `[{"media_type":"folder","name":"More","path":"tunein://local/more"}]`,
		}, "TuneIn listed no stations near home"},
	} {
		_, err := tuneInLocal(fakeMA(t, c.pages))
		if err == nil || err.Error() != c.want {
			t.Errorf("%s: got %v, want %q", c.name, err, c.want)
		}
	}
	broken := func(string, map[string]any) (json.RawMessage, error) { return nil, errors.New("connection refused") }
	if _, err := tuneInLocal(broken); err == nil {
		t.Error("an unreachable Music Assistant read as stations")
	}
}

func TestMAPlaying(t *testing.T) {
	stations := []station{
		{Name: "Hitradio FFH", ID: "library://radio/1", Kind: maKind},
		{Name: "SWR3", ID: "https://swr3.example.org/live", Kind: "radio_browser"},
	}
	for _, c := range []struct {
		name string
		v    MusicPlaybackView
		want string
	}{
		{"by uri", MusicPlaybackView{Playing: true, Station: "FFH", StationURI: "library://radio/1"}, "Hitradio FFH"},
		{"by name", MusicPlaybackView{Playing: true, Station: "hitradio ffh"}, "Hitradio FFH"},
		{"paused", MusicPlaybackView{Station: "Hitradio FFH", StationURI: "library://radio/1"}, ""},
		{"a song", MusicPlaybackView{Playing: true}, ""},
		{"not Music Assistant's", MusicPlaybackView{Playing: true, Station: "SWR3"}, ""},
	} {
		if got := maPlaying(stations, c.v); got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

func TestMASourceLabel(t *testing.T) {
	if got := SourceLabel(config.RadioMA); got != "Music Assistant" {
		t.Fatalf("got %q", got)
	}
}
