package home

import (
	"errors"
	"fmt"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

func TestTheQueueCarriesMusicAssistantsFavorite(t *testing.T) {
	_, fake := setupMusicFailoverTest(t)
	fake.queue = map[string]any{
		"current_item": map[string]any{"name": "Release - Reason That I Sing", "media_item": map[string]any{
			"name": "Reason That I Sing", "media_type": "track", "uri": "library://track/17", "favorite": true,
		}},
		"next_item": map[string]any{"name": "Harbour Song"},
	}
	next, cur, err := musicQueue(testLocalMA)
	if err != nil {
		t.Fatal(err)
	}
	if next != "Harbour Song" || !cur.Favorite || cur.URI != "library://track/17" {
		t.Errorf("queue = %q, %+v; want the next song, and the track marked a favorite with its address", next, cur)
	}
}

// A live stream has no next song: Music Assistant's queue names the station again, which the page read
// out as "Next · Hitradio FFH".
func TestAStationHasNoNextSong(t *testing.T) {
	_, fake := setupMusicFailoverTest(t)
	fake.queue = map[string]any{
		"current_item": map[string]any{"name": "Hitradio FFH", "media_item": map[string]any{
			"name": "Hitradio FFH", "media_type": "radio", "uri": "tunein://radio/s17490",
		}},
		"next_item": map[string]any{"name": "Hitradio FFH"},
	}
	next, cur, err := musicQueue(testLocalMA)
	if err != nil {
		t.Fatal(err)
	}
	if next != "" || cur.Station == "" {
		t.Errorf("queue = %q, %+v; want no next song and the station named", next, cur)
	}
}

// The star on a station's song asks Music Assistant to find it with its providers. When none has it,
// Home Assistant answers 500, and the page says the song is not in the library rather than "failed".
// The same 500 on a track of the library is a plain failure.
func TestAStationSongNoProviderHasIsNotInTheLibrary(t *testing.T) {
	for _, c := range []struct {
		station string
		code    int
		want    error
	}{{"Hitradio FFH", 500, ErrNotInLibrary}, {"", 500, nil}, {"Hitradio FFH", 0, nil}} {
		f, fake := setupMusicFailoverTest(t)
		fake.render = "button.living_room_jarvis_favorite_current_song"
		if c.code != 0 {
			fake.fail = map[string]int{"button.press": c.code}
		}
		musicPlayback.view = MusicPlaybackView{Entity: testLocalMA, Playing: true, Title: "Some Song", Station: c.station}
		err := f.FavoriteNow()
		switch {
		case c.want != nil && !errors.Is(err, c.want):
			t.Errorf("station %q, %d: err %v, want %v", c.station, c.code, err, c.want)
		case c.want == nil && c.code != 0 && (err == nil || errors.Is(err, ErrNotInLibrary)):
			t.Errorf("station %q, %d: err %v, want a plain failure", c.station, c.code, err)
		case c.code == 0 && err != nil:
			t.Errorf("station %q: err %v, want the song saved", c.station, err)
		}
		if calls := fake.serviceCalls(); len(calls) == 0 || calls[len(calls)-1].domain != "button" {
			t.Errorf("station %q, %d: calls %+v, want the favorite button pressed", c.station, c.code, calls)
		}
	}
}

func TestAStationIsStarredOnTheDevicesOwnListWithoutTheScript(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	f.urlName, f.url = "Test FM", "http://example.org/test.mp3"

	if StationFavorite("Test FM") {
		t.Fatal("a station on no list is a favorite before the star was pressed")
	}
	if err := f.FavoriteNow(); err != nil {
		t.Fatal(err)
	}
	if own := OwnStations(); len(own) != 1 || own[0] != (config.Station{Name: "Test FM", URL: "http://example.org/test.mp3"}) {
		t.Fatalf("own stations = %v, want the station that was playing", own)
	}
	if !StationFavorite("Test FM") {
		t.Error("the starred station is not read back as a favorite")
	}
	for _, c := range fake.serviceCalls() {
		if c.domain == "script" {
			t.Errorf("called script.%s, which this house does not have", c.service)
		}
	}
	// Twice is still once.
	if err := f.FavoriteNow(); err != nil || len(OwnStations()) != 1 {
		t.Errorf("a second star: err %v, %d own stations; want nil and still 1", err, len(OwnStations()))
	}

	if err := f.UnfavoriteNow(); err != nil {
		t.Fatal(err)
	}
	if StationFavorite("Test FM") || len(OwnStations()) != 0 {
		t.Errorf("own stations after taking it out = %v, want none", OwnStations())
	}
	if err := f.UnfavoriteNow(); !errors.Is(err, ErrFavoriteElsewhere) {
		t.Errorf("taking out a station on no list of the device's: err %v, want ErrFavoriteElsewhere", err)
	}
}

func TestAStationGoesThroughTheHousesScriptWhereThereIsOne(t *testing.T) {
	f, fake := setupMusicFailoverTest(t)
	f.urlName, f.url = "Test FM", "http://example.org/test.mp3"
	fake.mu.Lock()
	fake.states["script."+radioFavoriteScript] = fake.states[testLocalMA]
	fake.mu.Unlock()
	fake.clearCalls()

	if err := f.FavoriteNow(); err != nil {
		t.Fatal(err)
	}
	calls := fake.serviceCalls()
	if len(calls) != 1 || calls[0].domain != "script" || calls[0].service != radioFavoriteScript || calls[0].data["station"] != "Test FM" {
		t.Errorf("calls = %+v, want the house's script for the station", calls)
	}
	if len(OwnStations()) != 0 {
		t.Errorf("own stations = %v, want the house's list used instead", OwnStations())
	}
}

func TestAFullOwnListSaysSo(t *testing.T) {
	f, _ := setupMusicFailoverTest(t)
	var full []config.Station
	for i := range config.MaxOwnStations {
		full = append(full, config.Station{Name: fmt.Sprint("Station ", i), URL: fmt.Sprint("http://example.org/", i)})
	}
	if err := SetOwnStations(full); err != nil {
		t.Fatal(err)
	}
	f.urlName, f.url = "One More FM", "http://example.org/more.mp3"
	if err := f.FavoriteNow(); !errors.Is(err, ErrOwnStationsFull) {
		t.Errorf("starring with the list full: err %v, want ErrOwnStationsFull", err)
	}
}

func TestATrackWithoutMusicAssistantsKeyCannotBeTakenOut(t *testing.T) {
	f, _ := setupMusicFailoverTest(t)
	musicPlayback.view = MusicPlaybackView{Entity: testLocalMA, Playing: true, Title: "Reason That I Sing", Favorite: true, TrackURI: "library://track/17"}
	if err := f.UnfavoriteNow(); !errors.Is(err, ErrFavoriteElsewhere) {
		t.Errorf("err %v, want ErrFavoriteElsewhere with no Music Assistant key on the device", err)
	}
}
