// Package question is a yes-or-no question the device has asked out loud, waiting for its answer.
//
// Home Assistant asks it (assist_satellite.start_conversation) and opens the microphone after it, but
// it has nothing to do with "yes": the answer is meant for this device. So the voice feature hands
// every transcript here first while a question is open, and one that answers it is acted on here and
// goes no further. Anything else closes the question. Heard in the turn opened for the question, it is
// dropped: a television answers more of these than anybody does, and the assistant has nothing to do
// with "Untertitel im Auftrag des ZDF". Heard after a wake word, it goes on to the assistant as usual.
//
// A turn opened for the question can also end with no words at all: Whisper takes a short, quiet "joa"
// for silence, or nobody answers before listening runs out. Then the question is asked once more.
package question

import (
	"log/slog"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

type open struct {
	what           string
	d              time.Duration
	until          time.Time
	yes, no, again func()
	missed         bool
}

var (
	mu  sync.Mutex
	cur *open
	now = time.Now
	// againAfter is how long a missed question waits before it is asked again: Home Assistant is still
	// closing the run that heard nothing, and a new one started before that has ended is refused.
	againAfter = 2 * time.Second
)

// Ask opens a question, what naming it in the log, answered by the next thing said within d. yes or no
// runs on the answer, and again asks it once more if its turn hears nothing (see Missed); nil asks
// only once. A question already open is replaced: only the last one asked is the one heard.
func Ask(what string, d time.Duration, yes, no, again func()) {
	mu.Lock()
	defer mu.Unlock()
	cur = &open{what: what, d: d, until: now().Add(d), yes: yes, no: no, again: again}
}

// Missed is told that the turn opened for the question ended with no words in it. The first time, the
// question is asked again and stays open for as long again; it reports whether it was. After that the
// question waits out its time, so a second silence is not taken for a no.
func Missed() bool {
	mu.Lock()
	q := cur
	if q == nil || q.again == nil || q.missed || now().After(q.until) {
		mu.Unlock()
		return false
	}
	q.missed = true
	q.until = now().Add(againAfter + q.d)
	mu.Unlock()
	slog.Info("no answer heard, asking again", "question", q.what)
	safe.Go("ask again "+q.what, func() {
		time.Sleep(againAfter)
		mu.Lock()
		still := cur == q
		mu.Unlock()
		if still {
			q.again()
		}
	})
	return true
}

// Withdraw closes the question named what, if it is the one open.
func Withdraw(what string) {
	mu.Lock()
	defer mu.Unlock()
	if cur != nil && cur.what == what {
		cur = nil
	}
}

// Answer takes a transcript, and reports whether it answered the open question. Saying nothing keeps
// the question open; saying something else closes it, since the moment has passed.
func Answer(text string) bool { return Take(text, false) }

// Take is Answer for a turn that may have been opened for the question rather than by a wake word. In
// that one a transcript that does not answer it is dropped as well, and Take reports it as taken.
func Take(text string, forIt bool) bool {
	if strings.TrimSpace(text) == "" {
		return false
	}
	mu.Lock()
	q := cur
	cur = nil
	mu.Unlock()
	if q == nil || now().After(q.until) {
		return false
	}
	switch Reply(text) {
	case Yes:
		slog.Info("question answered yes", "question", q.what, "text", text)
		safe.Go("answer "+q.what, q.yes)
	case No:
		slog.Info("question answered no", "question", q.what, "text", text)
		safe.Go("answer "+q.what, q.no)
	default:
		if forIt {
			slog.Info("not an answer to the question, dropped", "question", q.what, "text", text)
			return true
		}
		slog.Info("question left unanswered", "question", q.what, "text", text)
		return false
	}
	return true
}

// Said is what a reply to a yes-or-no question says.
type Said int

const (
	Other Said = iota
	Yes
	No
)

// The words of an answer. A reply is yes when it is made only of these, at least one of them a yes
// and none a no; any other word makes it something else ("yes, and turn the lights on" is for the
// assistant). Kept closed on purpose, the way stopring.go is.
var (
	yesWords = set("yes yeah yep yup yo sure ok okay alright go do install now absolutely course definitely good fine " +
		"ja jo joa joah jap japp jup jupp jep jawohl jawoll klar gerne gern los sicher natürlich genau logo passt " +
		"okidoki gut unbedingt mach mache installier installiere installieren jetzt fall " +
		"ausführen führ führe updaten aktualisieren aktualisiere " +
		"oui ouais d'accord sûr sì si sí certo vale claro adelante graag prima")
	noWords = set("no nope nah not don't dont later wait cancel never tomorrow stop " +
		"nein nee nö ne nicht später warte morgen abbrechen stopp nie " +
		"non pas tard dopo tardi luego después tarde niet straks")
	fillWords = set("please thanks thank you it that the update right away ahead let's lets of maybe sounds for " +
		"bitte danke das es die den auf jeden na doch gleich vielleicht aus mal ruhig schon einfach dann lass a " +
		"merci vas y bien va bene per favore por favor doe maar het")
)

// "o.k." is one word, not two letters.
var dotted = strings.NewReplacer("o.k.", "ok", "o. k.", "ok")

func set(words string) map[string]bool {
	m := map[string]bool{}
	for _, w := range strings.Fields(words) {
		m[w] = true
	}
	return m
}

// Reply reads a transcript as an answer to a yes-or-no question.
func Reply(text string) Said {
	words := strings.FieldsFunc(dotted.Replace(strings.ToLower(text)), func(r rune) bool {
		return !unicode.IsLetter(r) && r != '\''
	})
	yes, no := false, false
	for _, w := range words {
		if !noWords[w] && !yesWords[w] && !fillWords[w] {
			w = squeeze(w) // "jaaa", "neeein"
		}
		switch {
		case noWords[w]:
			no = true
		case yesWords[w]:
			yes = true
		case fillWords[w]:
		default:
			return Other
		}
	}
	switch {
	case no:
		return No
	case yes:
		return Yes
	}
	return Other
}

// squeeze is a word drawn out the way it is said, back to its letters: "jaaa" to "ja", "neeein" to
// "nein". A doubled letter goes too, so "nee" squeezes to "ne"; both are no.
func squeeze(w string) string {
	var b strings.Builder
	var last rune
	for _, r := range w {
		if r != last {
			b.WriteRune(r)
		}
		last = r
	}
	return b.String()
}
