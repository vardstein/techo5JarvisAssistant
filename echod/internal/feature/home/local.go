package home

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

// Stations without any setup: Home Assistant's Radio Browser integration, which a new installation
// adds on its own, lists the stations within 100 km of home and the most popular ones. The lists come
// over Home Assistant's websocket API with the device's token, and a station plays through
// media_player.play_media on this device's own player, which Home Assistant resolves and converts.
// The stations wired with home_radio stay the Favorites list and play exactly as before. Music
// Assistant's library is a list of its own, and stands in for Radio Browser near home (radio_ma.go).

const (
	// listsEvery is how long a fetched list is kept.
	listsEvery = 12 * time.Hour
	// listRetry is how long a failed fetch waits before the next.
	listRetry = time.Minute

	// popularMax keeps the popular list to pages a finger will turn: Radio Browser gives 250.
	popularMax = 56
)

// station is one entry of a Radio Browser list.
type station struct {
	Name, ID, Kind string
	Thumb          string // its logo's address, when the list has one
}

type radioList struct {
	stations []station
	at       time.Time
	err      string
	busy     bool
	// via is where the stations came from when not where the list's name says: viaTuneIn.
	via string
}

type radioLists struct {
	local, popular, ma radioList
}

// RadioSources are the lists this device can show, in the order the sheet steps through them.
func RadioSources() []string {
	var out []string
	if config.Get().Home.Radio.Configured() {
		out = append(out, config.RadioFavorites)
	}
	if len(config.Get().Home.Radio.Own) > 0 {
		out = append(out, config.RadioOwn)
	}
	if hass.Get().Ready() {
		if maRadioOffered() {
			out = append(out, config.RadioMA)
		}
		out = append(out, config.RadioLocal, config.RadioPopular)
	}
	return out
}

// radioSource is the list shown: the one chosen when it is available, else the first there is.
func radioSource() string {
	sources := RadioSources()
	if len(sources) == 0 {
		return ""
	}
	chosen := config.Get().Home.RadioSource
	for _, s := range sources {
		if s == chosen {
			return s
		}
	}
	return sources[0]
}

// SetRadioSource shows one list by name, if this device has it.
func (f *Feature) SetRadioSource(source string) {
	for _, s := range RadioSources() {
		if s != source {
			continue
		}
		if err := config.Set().Home().RadioSource(source); err != nil {
			slog.Warn("home: saving the radio list failed", "err", err)
		}
		f.fetchList(source)
		f.Changed.Emit(struct{}{})
		return
	}
}

// NextRadioSource steps the radio page to the next list.
func (f *Feature) NextRadioSource() {
	sources := RadioSources()
	if len(sources) < 2 {
		return
	}
	cur := radioSource()
	next := sources[0]
	for i, s := range sources {
		if s == cur {
			next = sources[(i+1)%len(sources)]
		}
	}
	if err := config.Set().Home().RadioSource(next); err != nil {
		slog.Warn("home: saving the radio list failed", "err", err)
	}
	f.fetchList(next)
	f.Changed.Emit(struct{}{})
}

// SourceLabel is how the sheet names a list.
func SourceLabel(source string) string {
	switch source {
	case config.RadioFavorites:
		return "Favorites"
	case config.RadioLocal:
		return "Local stations"
	case config.RadioPopular:
		return "Popular worldwide"
	case config.RadioOwn:
		return "On this device"
	case config.RadioMA:
		return "Music Assistant"
	}
	return source
}

func (f *Feature) list(source string) *radioList {
	switch source {
	case config.RadioLocal:
		return &f.lists.local
	case config.RadioPopular:
		return &f.lists.popular
	case config.RadioMA:
		return &f.lists.ma
	}
	return nil
}

// fetchList gets a list from Home Assistant in the background, unless a fresh one is at hand.
func (f *Feature) fetchList(source string) {
	f.mu.Lock()
	l := f.list(source)
	keep := listsEvery
	if l != nil && l.err != "" {
		keep = listRetry // the page asks every frame; a failing Home Assistant is not asked that often
	} else if l != nil && l.via != "" {
		keep = viaKeep // Radio Browser is asked again before long
	}
	if l == nil || l.busy || (!l.at.IsZero() && time.Since(l.at) < keep) {
		f.mu.Unlock()
		return
	}
	l.busy = true
	f.mu.Unlock()
	go func() {
		stations, via, err := browseStations(source)
		f.mu.Lock()
		l.busy, l.at = false, time.Now()
		if err != nil {
			l.err = err.Error()
		} else {
			l.stations, l.err, l.via = stations, "", via
			slog.Info("radio: stations listed", "list", source, "count", len(stations), "via", via)
		}
		f.mu.Unlock()
		f.Changed.Emit(struct{}{})
	}()
}

// browseStations is a list's stations, and where they came from when that is not the list's own source.
func browseStations(source string) ([]station, string, error) {
	if source == config.RadioMA {
		stations, err := maLibraryStations()
		if err != nil {
			slog.Warn("radio: listing Music Assistant's stations", "err", err)
		}
		return stations, "", err
	}
	m, err := hass.Get().Browse(context.Background(), "media-source://radio_browser/"+source)
	if err == nil {
		return stationsOf(m, source), "", nil
	}
	slog.Warn("radio: listing stations", "list", source, "err", err)
	if source != config.RadioLocal {
		return nil, "", errRadioBrowser
	}
	stations, tuneErr := tuneInStandIn()
	if tuneErr != nil {
		slog.Info("radio: no TuneIn stand-in for the stations near home", "err", tuneErr)
		return nil, "", errRadioBrowser
	}
	return stations, viaTuneIn, nil
}

// stationsOf is the playable entries of a listing, names tidied, the popular list cut short.
func stationsOf(m hass.Media, source string) []station {
	var out []station
	seen := map[string]bool{}
	for _, c := range m.Children {
		name := strings.Join(strings.Fields(c.Title), " ")
		if !c.CanPlay || name == "" || seen[strings.ToLower(name)] {
			continue
		}
		seen[strings.ToLower(name)] = true
		out = append(out, station{Name: name, ID: c.ID, Kind: c.Kind, Thumb: c.Thumb})
		if source == config.RadioPopular && len(out) == popularMax {
			break
		}
	}
	return out
}

// playListed plays a station of a list from Home Assistant by its name in the list shown; false when it is
// not one. Radio Browser's play on this device's own player, Music Assistant's on this room's player.
func (f *Feature) playListed(source, name string) bool {
	f.mu.Lock()
	l := f.list(source)
	var st station
	if l != nil {
		for _, s := range l.stations {
			if s.Name == name {
				st = s
			}
		}
	}
	if st.ID == "" {
		f.mu.Unlock()
		return false
	}
	if st.Kind == maKind {
		// The stream comes over Sendspin, or to another speaker, with its own name: listed is not needed.
		f.chosen = name
		f.mu.Unlock()
		go f.playMA(st)
		return true
	}
	f.chosen, f.listed, f.listedAt = name, name, time.Now()
	f.mu.Unlock()
	go func() {
		if err := hass.Get().PlayMedia(speakerEntity(), st.ID, st.Kind); err != nil {
			slog.Warn("radio: playing a station", "station", name, "err", err)
			f.mu.Lock()
			if f.chosen == name {
				f.chosen = ""
			}
			f.mu.Unlock()
			f.Changed.Emit(struct{}{})
			return
		}
		slog.Info("radio: station asked for", "station", name, "list", source)
	}()
	return true
}

// listedFor is how long a station tapped in a Radio Browser list names the stream that starts: the
// stream comes through Home Assistant's proxy, so its URL does not say.
const listedFor = time.Minute

// callFavorite plays a station through the script wired with home_radio. It is handed to a goroutine
// rather than done where it was asked for: naming this device's media player can wait on Home Assistant
// — the registries are a websocket away — and the caller is a finger on the screen. playListed already
// starts its own request the same way.
//
// Naming the player is also what lets two quick taps arrive out of order: the second sets the chosen
// station while the first is still looking the player up, and the first then reaches Home Assistant after
// the second and starts the station the finger has moved off. Only the station still chosen is asked for.
func (f *Feature) callFavorite(h config.Radio, station string) {
	safe.Go("radio: playing a favorite", func() {
		speaker := speakerEntity()
		f.mu.Lock()
		chosen := f.chosen
		f.mu.Unlock()
		if chosen != station {
			return
		}
		component.CallService.Emit(component.Call{
			Service: h.Service,
			Data:    map[string]string{h.Field: askFor(station), h.SpeakerField: speaker},
		})
	})
}
