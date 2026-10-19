package home

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/lib/mass"
)

// Lyrics for the song Music Assistant is playing. Music Assistant knows them for most songs: a
// library ripped and tagged at home carries its words in the files, often with the time of every
// line, and its lyrics providers (LRCLIB and the rest, when online lookups are on) find them for songs
// from a streaming service. Home Assistant's integration does not pass them on, so the Show asks
// Music Assistant itself, the server Sendspin is paired with, as a user of its own that the installer
// made (docs/lyrics.md). Without that user's token there are simply no lyrics.

// lyricsRetry is how long a Show without a token, or a server that did not answer, is left alone
// before it is asked again.
const lyricsRetry = 10 * time.Minute

// lyricsWait is how long one song's lookup may take. A provider asked online can be slow.
const lyricsWait = 20 * time.Second

// LyricLine is one line of a song's words; At is when it is sung, from the start of the song.
type LyricLine struct {
	At   time.Duration
	Text string
}

// Lyrics are one song's words. Title is the song they belong to, so a page that has moved on to the
// next song does not show the last one's words while these are fetched. Without Synced the lines
// have no times, only an order.
type Lyrics struct {
	Title  string
	Synced bool
	Lines  []LyricLine
}

// Current is the line being sung at elapsed, -1 before the first. Unsynced lyrics have no current
// line.
func (l *Lyrics) Current(elapsed time.Duration) int {
	if l == nil || !l.Synced {
		return -1
	}
	return sort.Search(len(l.Lines), func(i int) bool { return l.Lines[i].At > elapsed }) - 1
}

// NextAt is when the line after the one sung at elapsed starts, zero when there is none.
func (l *Lyrics) NextAt(elapsed time.Duration) time.Duration {
	if l == nil || !l.Synced {
		return 0
	}
	if i := l.Current(elapsed) + 1; i < len(l.Lines) {
		return l.Lines[i].At
	}
	return 0
}

// queueSong is what the queue says about the song playing: its name and Music Assistant's address
// for it ("library://track/12"), which is empty for radio and anything else that is not a song.
type queueSong struct {
	Name string
	URI  string
	// Favorite is Music Assistant's own mark on the track, which its favorite button sets.
	Favorite bool
	// Station and StationURI are set instead of URI when what plays is a radio station.
	Station, StationURI string
}

var lyrics struct {
	mu      sync.Mutex
	song    string // the address of the song asked for last
	current *Lyrics
	resting time.Time // not asked again before this
}

// MusicLyrics is the words of the song playing, nil when there are none. It does no network I/O.
func (f *Feature) MusicLyrics() *Lyrics {
	lyrics.mu.Lock()
	defer lyrics.mu.Unlock()
	return lyrics.current
}

// refreshLyrics fetches the words for song when it is not the song they were fetched for already.
func refreshLyrics(song queueSong) {
	lyrics.mu.Lock()
	if song.URI == "" {
		changed := lyrics.current != nil
		lyrics.song, lyrics.current = "", nil
		lyrics.mu.Unlock()
		if changed {
			Get().Changed.Emit(struct{}{})
		}
		return
	}
	if song.URI == lyrics.song || time.Now().Before(lyrics.resting) {
		lyrics.mu.Unlock()
		return
	}
	lyrics.song, lyrics.current = song.URI, nil
	lyrics.mu.Unlock()

	got, err := fetchLyrics(song)
	lyrics.mu.Lock()
	if lyrics.song != song.URI {
		lyrics.mu.Unlock()
		return
	}
	if err != nil {
		// Most often there is no token, which is the normal state of a Show the installer did not give
		// one; once in a while is often enough to notice that it has one now.
		lyrics.song, lyrics.resting = "", time.Now().Add(lyricsRetry)
		lyrics.mu.Unlock()
		slog.Info("lyrics", "song", song.Name, "err", err)
		return
	}
	lyrics.current = got
	lyrics.mu.Unlock()
	if got != nil {
		slog.Info("lyrics", "song", song.Name, "lines", len(got.Lines), "synced", got.Synced)
	}
	Get().Changed.Emit(struct{}{})
}

func fetchLyrics(song queueSong) (*Lyrics, error) {
	host := config.Get().Sendspin.ServerIP
	if host == "" || !mass.Get().Ready() {
		return nil, errors.New("no Music Assistant token that works, or no server paired")
	}
	ctx, cancel := context.WithTimeout(context.Background(), lyricsWait)
	defer cancel()
	// The lyrics command wants the whole song, as Music Assistant has it, not its address.
	track, err := mass.Get().Call(ctx, host, "music/item_by_uri", map[string]any{"uri": song.URI})
	if err != nil {
		return nil, err
	}
	raw, err := mass.Get().Call(ctx, host, "metadata/get_track_lyrics", map[string]any{"track": track})
	if err != nil {
		return nil, err
	}
	return decodeLyrics(raw, song.Name)
}

// decodeLyrics reads Music Assistant's answer: the plain words and the timed (LRC) words, either of
// them null. The timed ones are worth more on a screen that follows the song.
func decodeLyrics(raw json.RawMessage, title string) (*Lyrics, error) {
	var pair []*string
	if err := json.Unmarshal(raw, &pair); err != nil {
		return nil, err
	}
	if len(pair) > 1 && pair[1] != nil {
		if l := parseLRC(*pair[1], title); l != nil {
			return l, nil
		}
	}
	if len(pair) > 0 && pair[0] != nil {
		l := &Lyrics{Title: title}
		for _, line := range strings.Split(strings.ReplaceAll(*pair[0], "\r\n", "\n"), "\n") {
			l.Lines = append(l.Lines, LyricLine{Text: strings.TrimSpace(line)})
		}
		if l = trimLyrics(l); l != nil {
			return l, nil
		}
	}
	return nil, nil
}

// parseLRC reads LRC: every line has one or more times in front ("[01:02.50]"), a line sung twice
// has two. Tags without a time ("[ar:…]") are left out, an "[offset:…]" moves every line, and the
// times of single words some files have ("<01:02.80>") are taken out of the text.
func parseLRC(text, title string) *Lyrics {
	l := &Lyrics{Title: title, Synced: true}
	offset := time.Duration(0)
	for _, line := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		var times []time.Duration
		for strings.HasPrefix(line, "[") {
			end := strings.IndexByte(line, ']')
			if end < 0 {
				break
			}
			tag := line[1:end]
			line = strings.TrimSpace(line[end+1:])
			if at, ok := lrcTime(tag); ok {
				times = append(times, at)
			} else if v, ok := strings.CutPrefix(strings.ToLower(tag), "offset:"); ok {
				if ms, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
					offset = time.Duration(ms) * time.Millisecond
				}
			}
		}
		line = strings.TrimSpace(wordTimes.ReplaceAllString(line, ""))
		for _, at := range times {
			l.Lines = append(l.Lines, LyricLine{At: at, Text: line})
		}
	}
	// A positive offset has the words come sooner.
	for i := range l.Lines {
		if l.Lines[i].At -= offset; l.Lines[i].At < 0 {
			l.Lines[i].At = 0
		}
	}
	sort.SliceStable(l.Lines, func(i, j int) bool { return l.Lines[i].At < l.Lines[j].At })
	return trimLyrics(l)
}

var wordTimes = regexp.MustCompile(`<\d+:\d+(?:[.:]\d+)?>`)

// lrcTime reads "mm:ss", "mm:ss.xx" or "mm:ss:xx".
func lrcTime(tag string) (time.Duration, bool) {
	m, rest, ok := strings.Cut(tag, ":")
	if !ok {
		return 0, false
	}
	min, err := strconv.Atoi(m)
	if err != nil || min < 0 {
		return 0, false
	}
	rest = strings.Replace(rest, ":", ".", 1)
	sec, err := strconv.ParseFloat(rest, 64)
	if err != nil || sec < 0 || sec >= 60 || strings.ContainsAny(rest, "eE+-") {
		return 0, false
	}
	return time.Duration(min)*time.Minute + time.Duration(sec*float64(time.Second)).Round(time.Millisecond), true
}

// trimLyrics drops the empty lines at either end, and is nil when nothing is left.
func trimLyrics(l *Lyrics) *Lyrics {
	for len(l.Lines) > 0 && l.Lines[len(l.Lines)-1].Text == "" {
		l.Lines = l.Lines[:len(l.Lines)-1]
	}
	if !l.Synced {
		for len(l.Lines) > 0 && l.Lines[0].Text == "" {
			l.Lines = l.Lines[1:]
		}
	}
	if len(l.Lines) == 0 {
		return nil
	}
	return l
}
