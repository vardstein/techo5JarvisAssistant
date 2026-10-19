package home

import (
	"errors"
	"strings"
	"sync"
	"time"
	"unicode"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
)

// Where a reply was cut off by a wake word, for Genbu to tell the agent (GENBU-250, voice/cut.go): an
// event that says it happened, and an action that answers with the words that were heard.
//
// The words are not a sensor. A sensor's state goes into Home Assistant's history, and the replies said
// in a home are nobody's business to keep for ten days; Genbu holds the answer in memory for the next
// turn and nothing else. Over the encrypted API only, like the keys (key.go).

// ReplyCutEvent is the event type the reply_cut entity reports.
const ReplyCutEvent = "cut"

// replyCutKeep is how long a cut is worth telling about. The turn it is for opens right after it.
const replyCutKeep = 2 * time.Minute

// replyCutHeard is as much of what was heard as the action answers with, from the end: the last words
// are what places the cut, and the agent has the whole reply in its own history.
const replyCutHeard = 200

type replyCut struct {
	mu  sync.Mutex
	cut voice.Cutoff
	at  time.Time
}

func (f *Feature) buildReplyCut() {
	f.replyCutEv = &esphome.Event{
		Base: esphome.Base{
			ObjectID: "reply_cut",
			Name:     "Reply cut off",
			Icon:     "mdi:message-minus",
		},
		Types: []string{ReplyCutEvent},
	}
	voice.Cut.Listen(func(c voice.Cutoff) {
		f.replyCut.mu.Lock()
		f.replyCut.cut, f.replyCut.at = c, time.Now()
		f.replyCut.mu.Unlock()
		f.replyCutEv.Trigger(ReplyCutEvent)
	})
}

// replyCutAction is reply_cut: the words heard of the last reply cut off, the last replyCutHeard
// characters of them, whether the place was timed or guessed, the turn and how long ago. Empty when
// there is none worth telling.
func (f *Feature) replyCutAction() *esphome.Action {
	return &esphome.Action{
		Name:    "reply_cut",
		Answers: true,
		Run: func(esphome.Call) (any, error) {
			if !security.APIEncrypted() {
				return nil, errors.New("reply_cut: set an API encryption key first")
			}
			return f.replyCut.answer(time.Now()), nil
		},
	}
}

func (r *replyCut) answer(now time.Time) map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.at.IsZero() || now.Sub(r.at) > replyCutKeep {
		return map[string]any{}
	}
	return map[string]any{
		"heard": lastOf(r.cut.Heard, replyCutHeard),
		"timed": r.cut.Timed,
		"turn":  r.cut.Turn,
		"age_s": int(now.Sub(r.at).Seconds()),
	}
}

// lastOf is the end of s, at most n characters, starting on a whole word.
func lastOf(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	runes = runes[len(runes)-n:]
	if i := strings.IndexFunc(string(runes), unicode.IsSpace); i >= 0 {
		return "…" + strings.TrimSpace(string(runes)[i:])
	}
	return "…" + string(runes)
}
