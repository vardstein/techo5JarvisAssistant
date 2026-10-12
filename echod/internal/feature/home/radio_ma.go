package home

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/mass"
)

// Radio through Music Assistant, for any house that has it, whatever its providers are.
//
// The Music Assistant list is the radio stations in its library: TuneIn's presets once TuneIn is added
// there, and anything somebody starred in its app. They come over Home Assistant's music_assistant
// actions with the device's Home Assistant token, so nothing on the device has to know the server, and
// they play through music_assistant.play_media on this room's player (PlayMusic), as a song does.
//
// When Radio Browser does not answer, the stations near home come from TuneIn instead, through Music
// Assistant's own API: Home Assistant's browser only reaches Music Assistant's library, not its
// providers' directories. That needs the device's Music Assistant token (the music_assistant action)
// and TuneIn added in Music Assistant; without either the page says Radio Browser is not answering.

const (
	// maKind is a station's Kind when Music Assistant plays it; its ID is then Music Assistant's uri.
	maKind = "music_assistant"
	// maRadioMax is as many library stations as are asked for: Home Assistant's own default is 25.
	maRadioMax = 200
	// maLookEvery is how long a house found without Music Assistant waits before it is asked again.
	maLookEvery = 10 * time.Minute
	// viaKeep is how long stations from the stand-in are kept before Radio Browser is asked again.
	viaKeep = 30 * time.Minute

	// viaTuneIn is what radioList.via says when the stations near home came from TuneIn.
	viaTuneIn = "TuneIn"
)

// errRadioBrowser is what the page says when Radio Browser's lists cannot be had; the reason is logged.
var errRadioBrowser = errors.New("Radio Browser is not answering")

// maEntryTemplate is the config entry of Music Assistant in Home Assistant, empty when there is none.
// Its players are the way in: an installation without players has nothing to play radio on anyway.
const maEntryTemplate = "{% set e = integration_entities('music_assistant') | select('match', 'media_player[.]') | list %}" +
	"{{ config_entry_id(e[0]) if e else '' }}"

var maEntry struct {
	sync.Mutex
	id   string
	at   time.Time
	busy bool
}

// maRadioOffered reports whether Home Assistant has Music Assistant, as last looked; it looks again in the
// background when that is old. The page asks every frame, so it never waits on the answer.
func maRadioOffered() bool {
	maEntry.Lock()
	defer maEntry.Unlock()
	keep := listsEvery
	if maEntry.id == "" {
		keep = maLookEvery
	}
	if !maEntry.busy && (maEntry.at.IsZero() || time.Since(maEntry.at) >= keep) {
		maEntry.busy = true
		go lookForMA()
	}
	return maEntry.id != ""
}

func lookForMA() {
	id, err := hass.Get().Render(maEntryTemplate)
	id = strings.TrimSpace(id)
	maEntry.Lock()
	changed := err == nil && id != maEntry.id
	if err == nil {
		maEntry.id = id
	} else {
		slog.Info("radio: looking for Music Assistant", "err", err)
	}
	maEntry.busy, maEntry.at = false, time.Now()
	maEntry.Unlock()
	if changed {
		slog.Info("radio: Music Assistant in Home Assistant", "found", id != "")
		Get().Changed.Emit(struct{}{})
	}
}

// maLibraryStations is the radio stations in Music Assistant's library, through Home Assistant.
func maLibraryStations() ([]station, error) {
	maEntry.Lock()
	entry := maEntry.id
	maEntry.Unlock()
	if entry == "" {
		return nil, errors.New("Music Assistant is not in Home Assistant")
	}
	raw, err := hass.Get().CallResponse("music_assistant", "get_library", map[string]any{
		"config_entry_id": entry, "media_type": "radio", "limit": maRadioMax,
	})
	if err != nil {
		return nil, err
	}
	return libraryStations(raw)
}

// libraryStations reads get_library's answer: favorites first, then the rest in Music Assistant's order.
func libraryStations(raw json.RawMessage) ([]station, error) {
	var lib struct {
		Items []struct {
			Name      string `json:"name"`
			URI       string `json:"uri"`
			MediaType string `json:"media_type"`
			Image     any    `json:"image"`
			Favorite  bool   `json:"favorite"`
		} `json:"items"`
	}
	if err := json.Unmarshal(raw, &lib); err != nil {
		return nil, err
	}
	type fav struct {
		station
		favorite bool
	}
	var out []fav
	seen := map[string]bool{}
	for _, it := range lib.Items {
		name := strings.Join(strings.Fields(it.Name), " ")
		if it.URI == "" || name == "" || (it.MediaType != "" && it.MediaType != "radio") || seen[strings.ToLower(name)] {
			continue
		}
		seen[strings.ToLower(name)] = true
		out = append(out, fav{station{Name: name, ID: it.URI, Kind: maKind, Thumb: maImage(it.Image, nil)}, it.Favorite})
	}
	slices.SortStableFunc(out, func(a, b fav) int {
		switch {
		case a.favorite == b.favorite:
			return 0
		case a.favorite:
			return -1
		}
		return 1
	})
	stations := make([]station, len(out))
	for i, f := range out {
		stations[i] = f.station
	}
	return stations, nil
}

// maItem is what Music Assistant's browse lists: a folder, or something to play.
type maItem struct {
	ItemID    string `json:"item_id"`
	Provider  string `json:"provider"`
	Name      string `json:"name"`
	MediaType string `json:"media_type"`
	URI       string `json:"uri"`
	Path      string `json:"path"`
	Image     any    `json:"image"`
	Metadata  struct {
		Images []maPicture `json:"images"`
	} `json:"metadata"`
}

type maPicture struct {
	Type   string `json:"type"`
	Path   string `json:"path"`
	Remote bool   `json:"remotely_accessible"`
}

// maImage is a picture's address from what Music Assistant sends: Home Assistant's actions give a plain
// address, Music Assistant's own API a picture with a path, or a list of them in the metadata. Only one
// on the web is any use here; a provider's own path is not.
func maImage(v any, more []maPicture) string {
	switch p := v.(type) {
	case string:
		if fetchable(p) {
			return p
		}
	case map[string]any:
		if path, _ := p["path"].(string); fetchable(path) && !strings.HasPrefix(path, "/") {
			return path
		}
	}
	for _, kind := range []string{"thumb", ""} {
		for _, pic := range more {
			if (kind == "" || pic.Type == kind) && fetchable(pic.Path) && !strings.HasPrefix(pic.Path, "/") {
				return pic.Path
			}
		}
	}
	return ""
}

// maCall runs one command of Music Assistant's API.
type maCall func(command string, args map[string]any) (json.RawMessage, error)

// tuneInLocal is TuneIn's stations near home, as Music Assistant's TuneIn provider browses them: its
// folder at the top of Music Assistant's browse, then its "Local Radio".
func tuneInLocal(call maCall) ([]station, error) {
	browse := func(path string) ([]maItem, error) {
		args := map[string]any{}
		if path != "" {
			args["path"] = path
		}
		raw, err := call("music/browse", args)
		if err != nil {
			return nil, err
		}
		var items []maItem
		return items, json.Unmarshal(raw, &items)
	}
	where := func(it maItem) string { return cmp.Or(it.Path, it.URI) }

	top, err := browse("")
	if err != nil {
		return nil, err
	}
	i := slices.IndexFunc(top, func(it maItem) bool {
		return (it.Provider == "tunein" || strings.HasPrefix(it.Provider, "tunein--")) && where(it) != ""
	})
	if i < 0 {
		return nil, errors.New("TuneIn is not in Music Assistant")
	}
	folders, err := browse(where(top[i]))
	if err != nil {
		return nil, err
	}
	i = slices.IndexFunc(folders, func(it maItem) bool {
		return it.ItemID == "local" || strings.HasSuffix(where(it), "://local") || strings.HasSuffix(where(it), "/local")
	})
	if i < 0 {
		return nil, errors.New("TuneIn has no stations near home")
	}
	items, err := browse(where(folders[i]))
	if err != nil {
		return nil, err
	}
	var out []station
	seen := map[string]bool{}
	for _, it := range items {
		name := strings.Join(strings.Fields(it.Name), " ")
		if it.MediaType != "radio" || it.URI == "" || name == "" || seen[strings.ToLower(name)] {
			continue
		}
		seen[strings.ToLower(name)] = true
		out = append(out, station{Name: name, ID: it.URI, Kind: maKind, Thumb: maImage(it.Image, it.Metadata.Images)})
	}
	if len(out) == 0 {
		return nil, errors.New("TuneIn listed no stations near home")
	}
	return out, nil
}

// tuneInStandIn is the stations near home from TuneIn, when the device can ask Music Assistant itself.
func tuneInStandIn() ([]station, error) {
	host := config.Get().Sendspin.ServerIP
	if host == "" || !mass.Get().Ready() {
		return nil, errors.New("no Music Assistant token, or no server paired")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return tuneInLocal(func(command string, args map[string]any) (json.RawMessage, error) {
		return mass.Get().Call(ctx, host, command, args)
	})
}

// playMA plays a station of a Music Assistant list on this room's player, as a song is played.
func (f *Feature) playMA(st station) {
	if _, err := f.PlayMusic(st.ID); err != nil {
		slog.Warn("radio: playing a station through Music Assistant", "station", st.Name, "err", err)
		f.mu.Lock()
		if f.chosen == st.Name {
			f.chosen = ""
		}
		f.mu.Unlock()
		f.Changed.Emit(struct{}{})
		return
	}
	musicPlaybackSoon()
	slog.Info("radio: station asked of Music Assistant", "station", st.Name)
}

// maPlaying is the station of the list shown that Music Assistant is playing in this room, by its queue.
func maPlaying(stations []station, v MusicPlaybackView) string {
	if !v.Playing || (v.StationURI == "" && v.Station == "") {
		return ""
	}
	for _, st := range stations {
		if st.Kind == maKind && (st.ID == v.StationURI || strings.EqualFold(st.Name, v.Station)) {
			return st.Name
		}
	}
	return ""
}
