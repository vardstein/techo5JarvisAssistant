package voice

import (
	"log/slog"
	"strings"
	"time"
	"unicode"

	"github.com/HuskerMinion/techo5/echod/internal/feature/wakeword"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hook"
)

// A wake word said over a reply stops it and opens a new turn on the same conversation (start). Home
// Assistant keeps the conversation, and with it the whole reply the agent wrote, including the part
// nobody heard. So the agent is told where the reply was cut (GENBU-250): Cut carries the last words
// that were heard, Home Assistant's genbu integration keeps them for the turn that follows, and the
// agent can tell a correction ("no, the other one") from a new topic and does not assume the room
// heard the rest.
//
// Where it was cut is worked out from time, not heard: how long the reply had been playing against
// how long it is. That is how OpenAI's Realtime API (conversation.item.truncate, audio_end_ms) and
// LiveKit and Pipecat do it too, and all of them are off by a word or two. Timed says the length was
// known; without it the place is a guess from an average speaking rate.

// Cutoff is a reply cut off by a wake word. Heard is the reply up to the last whole word that played,
// empty when none of it had. Turn is the id of the turn whose reply it was.
type Cutoff struct {
	Heard string
	Timed bool
	Turn  string
}

// Cut fires when a reply is cut off by a wake word. It runs on the conversation's goroutine, so
// listeners must not block.
var Cut hook.Hook[Cutoff]

// speakingRate is characters a second for a reply whose length is not known yet. Home Assistant's
// voices say about fourteen in English and German; a reply still streaming in has no length to time it
// against.
const speakingRate = 14.0

// heardUpTo is how much of text had been said after played, of audio in all. whole says audio is the
// reply's full length; without it the place is estimated from speakingRate. ok is false when the
// whole reply had been heard, so there is nothing cut off to tell anybody about.
//
// It stops at the last whole word: a word half said counts as not heard, so the agent errs toward
// saying a word again rather than toward taking one as heard.
func heardUpTo(text string, played, audio time.Duration, whole bool) (heard string, ok bool) {
	runes := []rune(strings.TrimSpace(text))
	if len(runes) == 0 {
		return "", false
	}
	if played <= 0 {
		return "", true
	}
	var n int
	if whole && audio > 0 {
		if played >= audio {
			return "", false
		}
		n = int(float64(len(runes)) * float64(played) / float64(audio))
	} else {
		n = int(played.Seconds() * speakingRate)
	}
	if n >= len(runes) {
		return "", false
	}
	// Back to the end of the last whole word. The rune at n is the first not said; if it is a letter
	// the word it is in was cut in half.
	for n > 0 && !unicode.IsSpace(runes[n]) {
		n--
	}
	return strings.TrimSpace(string(runes[:n])), true
}

// audible marks the reply as being heard from now. length is how long it is when it was fetched whole.
// A streamed reply plays once its cushion has filled, so it is heard about that much later than its
// first chunk arrives, which is when this is told.
func (c *conversation) audible(length time.Duration) {
	if c.phase != phaseReplying || !c.reply.audibleAt.IsZero() {
		return
	}
	c.reply.audibleAt = time.Now()
	if length > 0 {
		c.reply.audio, c.reply.whole = length, true
	} else if c.reply.stream != nil {
		c.reply.audibleAt = c.reply.audibleAt.Add(wakeword.Buffer(c.slot))
	}
	// No text, no way to tell whether the reply says the wake word itself, so no allowance either.
	if c.shown.Reply != "" {
		text := strings.ToLower(c.shown.Reply)
		c.speaking.Store(&text)
	}
}

// ownReply is the reply being heard, lower case, and whether one is. Detect reads it to judge a wake
// word over the device's own voice (detect.go, overOwnReply).
func (c *conversation) ownReply() (string, bool) {
	p := c.speaking.Load()
	if p == nil {
		return "", false
	}
	return *p, true
}

// cutOff reports where the reply playing now was cut. Only while a reply is audible: a wake word over
// the moment before the audio starts cuts nothing anybody heard.
func (c *conversation) cutOff() {
	if c.phase != phaseReplying || c.reply.audibleAt.IsZero() {
		return
	}
	played := time.Since(c.reply.audibleAt)
	heard, ok := heardUpTo(c.shown.Reply, played, c.reply.audio, c.reply.whole)
	if !ok {
		return
	}
	slog.Info("reply cut off", "played", played.Round(10*time.Millisecond), "of", c.reply.audio.Round(10*time.Millisecond),
		"timed", c.reply.whole, "heard_chars", len([]rune(heard)), "of_chars", len([]rune(c.shown.Reply)))
	Cut.Emit(Cutoff{Heard: heard, Timed: c.reply.whole, Turn: c.turn.ID()})
}

// OwnReply is the reply this device is saying out loud right now, lower case, and whether it is
// saying one. See conversation.ownReply.
func (v *Voice) OwnReply() (string, bool) { return v.turn.ownReply() }
