package home

import (
	"context"
	"errors"
	"log/slog"
	"strings"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/media"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/mass"
)

// What the star could not do, for the page to say so in words; anything else is just "couldn't".
var (
	// ErrFavoriteElsewhere is a favorite this device cannot take out: a Music Assistant track with no
	// token of Music Assistant's on the device, or a station on Home Assistant's own favorites list.
	ErrFavoriteElsewhere = errors.New("the favorite is kept where this device cannot change it")
	// ErrOwnStationsFull is a station starred with the device's own list already full.
	ErrOwnStationsFull = errors.New("the device's own stations are full")
	// ErrNotInLibrary is a song on a station Music Assistant could not find to save (GENBU-152).
	ErrNotInLibrary = errors.New("the song on the station is not in the music library")
)

// radioFavoriteScript is the script some houses have for Home Assistant's own Radio Favorites list.
// Without it a station is kept on the device's own list, which needs nothing in Home Assistant.
const radioFavoriteScript = "radio_favorite_add_station"

// musicFavorite reports whether Music Assistant's queue is what is playing here, and so what the star
// is for, rather than a station of this device's.
func (f *Feature) musicFavorite() bool {
	playback := f.MusicPlayback()
	_, _, _, held := media.Get().Held()
	return media.Get().Carried() || held || playback.Playing || playback.Paused
}

// FavoriteNow saves what this device is playing to the favorites it belongs in: a Music Assistant
// track to Music Assistant's, by pressing its "favorite current song" button for this speaker; a
// station to Home Assistant's Radio Favorites through the script that finds its stream where a house
// has that script, and to the device's own stations where it does not.
func (f *Feature) FavoriteNow() error {
	if f.musicFavorite() {
		playback := f.MusicPlayback()
		ma := playback.Entity
		if ma == "" {
			var err error
			ma, err = musicAssistantPlayer()
			if err != nil {
				return err
			}
		}
		if ma == "" {
			return errors.New("no Music Assistant player for this playback")
		}
		button, err := hass.Get().Render("{{ device_entities(device_id('" + ma + "')) | select('search', 'favorite_current_song') | list | first | default('') }}")
		if err != nil {
			return err
		}
		if !strings.HasPrefix(button, "button.") {
			return errors.New("Music Assistant has no favorite button for " + ma)
		}
		slog.Info("favorite: Music Assistant track", "button", button)
		if err := hass.Get().Call("button", "press", map[string]any{"entity_id": button}); err != nil {
			// On a station Music Assistant saves the song only by finding "artist - title" with its
			// music providers, and raises when none has it, which Home Assistant answers with a 500.
			if playback.Station != "" && hass.ServerFailed(err) {
				return ErrNotInLibrary
			}
			return err
		}
		requeue()
		return nil
	}
	station, url := f.starredStation()
	if station == "" {
		return errors.New("nothing playing to save")
	}
	if _, err := hass.Get().State("script." + radioFavoriteScript); err == nil {
		slog.Info("favorite: radio station to Home Assistant's favorites", "station", station)
		return hass.Get().Call("script", radioFavoriteScript, map[string]any{"station": station})
	}
	own := OwnStations()
	for _, st := range own {
		if st.Name == station {
			return nil
		}
	}
	if len(own) >= config.MaxOwnStations {
		return ErrOwnStationsFull
	}
	if !playableURL(url) {
		return errors.New("the station's stream has no address to keep")
	}
	slog.Info("favorite: radio station to the device's own stations", "station", station)
	return SetOwnStations(append(own, config.Station{Name: station, URL: url}))
}

// UnfavoriteNow takes what is playing out of the favorites again: a track through Music Assistant's
// own API, which Home Assistant has no action for, and a station from the device's own list.
func (f *Feature) UnfavoriteNow() error {
	if f.musicFavorite() {
		id, ok := strings.CutPrefix(f.MusicPlayback().TrackURI, "library://track/")
		host := config.Get().Sendspin.ServerIP
		if !ok || id == "" || host == "" || !mass.Get().Ready() {
			return ErrFavoriteElsewhere
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		slog.Info("favorite: taking a Music Assistant track out", "track", id)
		if _, err := mass.Get().Call(ctx, host, "music/favorites/remove_item", map[string]any{"media_type": "track", "library_item_id": id}); err != nil {
			return err
		}
		requeue()
		return nil
	}
	station, _ := f.starredStation()
	own := OwnStations()
	for i, st := range own {
		if station != "" && st.Name == station {
			slog.Info("favorite: radio station off the device's own stations", "station", station)
			return SetOwnStations(append(own[:i:i], own[i+1:]...))
		}
	}
	return ErrFavoriteElsewhere
}

// starredStation is the station playing here and its stream, as the page names it.
func (f *Feature) starredStation() (name, url string) {
	f.mu.Lock()
	name, url = f.urlName, f.url
	f.mu.Unlock()
	if name == "" {
		name = f.Radio().Now
	}
	return name, url
}

// StationFavorite reports whether a station is among the favorites: the device's own stations or Home
// Assistant's Radio Favorites.
func StationFavorite(name string) bool {
	if name == "" {
		return false
	}
	for _, st := range OwnStations() {
		if st.Name == name {
			return true
		}
	}
	for _, n := range favoriteNames(config.Get().Home.Radio) {
		if n == name {
			return true
		}
	}
	return false
}

// requeue has the queue asked again on the next look, so Music Assistant's own word on the favorite
// follows a press without waiting out the poll.
func requeue() {
	musicPlayback.Lock()
	musicPlayback.queueAt = time.Time{}
	musicPlayback.Unlock()
}
