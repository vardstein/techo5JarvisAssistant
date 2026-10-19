// Package i18n is the screen in the language picked for it (config.Screen.Language).
//
// It works like the JODS shell's locales: one file per language, English as the fallback, and
// {name} placeholders so a translation can put the values where its own word order wants them.
// The English text itself is the key, so T("Check now") is "Jetzt suchen" on a German screen and
// "Check now" on any screen whose language has no entry for it yet: an untranslated string still
// reads as something. "Match Assistant" (no language set) is the language the voice assistant is set
// to in Home Assistant, or the Direct Brain's own, and English when that is none of these.
//
// Only the screen and the few things the device says on its own are translated; what the assistant
// says is the assistant's business. Wording follows docs/screen-language.md.
package i18n

import (
	"fmt"
	"regexp"
	"strings"
	"sync/atomic"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// Lang is the screen's language code, "" for English.
func Lang() string {
	c := config.Get()
	switch {
	case c.Screen.Language != "":
		return c.Screen.Language
	case c.Brain.Direct():
		return base(c.Brain.Language)
	}
	if l := assistant.Load(); l != nil {
		return *l
	}
	return ""
}

// Known is whether Lang is an answer and not a guess: the screen has a language, the direct pipeline
// names one, or Home Assistant said its pipeline's. Until then Lang's English is only the fallback, and
// words spoken through the pipeline may be in a language nobody there uses: bathroomShow5 asked about
// its update in English through a German pipeline while its own way to Home Assistant was still shut.
func Known() bool {
	c := config.Get()
	return c.Screen.Language != "" || c.Brain.Direct() || assistant.Load() != nil
}

// assistant is the language of Home Assistant's voice pipeline, which a screen with no language set
// follows. Somebody who set up their assistant in German wants the screen in German too.
var assistant atomic.Pointer[string]

// SetAssistant tells the screen which language the assistant's pipeline is in, as Home Assistant
// names it ("de", "de-CH", "pt_BR").
func SetAssistant(code string) {
	b := base(code)
	if old := assistant.Load(); old != nil && *old == b {
		return
	}
	assistant.Store(&b)
	Changed()
}

// base is the language in a code, without its country: "de" for "de-CH".
func base(code string) string {
	code = strings.ToLower(strings.TrimSpace(code))
	if i := strings.IndexAny(code, "-_"); i >= 0 {
		code = code[:i]
	}
	return code
}

// T is s in the screen's language. Text marked by Verbatim is not looked up, only unmarked.
func T(s string) string {
	if strings.Contains(s, mark) {
		return strings.ReplaceAll(s, mark, "")
	}
	if l := current(); l != nil {
		if t := l.text[s]; t != "" {
			return t
		}
	}
	return s
}

// Verbatim is s marked to be drawn as it is: T takes the mark out and translates nothing. Names from
// outside (a network, a speaker, a song, a calendar entry, a Show) go through it where they are drawn,
// so a Wi-Fi network called "Office" stays "Office" on a German screen and isn't drawn as "Büro". The
// mark is for the screen only; text that is spoken, saved or sent on must not carry it.
func Verbatim(s string) string {
	if s == "" || strings.HasPrefix(s, mark) {
		return s
	}
	return mark + s
}

// mark is a private-use character no font draws, so text that missed T shows that it did.
const mark = "\ue000"

// F is T(template) with each {name} filled in from the name, value pairs that follow it:
//
//	F("{name} is ready · this is {current}", "name", next, "current", running)
func F(template string, pairs ...string) string { return fill(T(template), pairs) }

// In is s in the language with this code, s itself when there is no translation.
func In(lang, s string) string {
	if l, ok := langs[lang]; ok {
		if t := l.text[s]; t != "" {
			return t
		}
	}
	return s
}

// The screen draws hundreds of strings a frame and config.Get copies the whole config, so the
// language is looked up again at most once a second.
var (
	cachedAt   atomic.Int64
	cachedLang atomic.Pointer[lang]
	cached24   atomic.Bool
)

// current is the screen language's table, nil for English.
func current() *lang {
	if now := time.Now().UnixNano(); now-cachedAt.Load() > int64(time.Second) {
		code := Lang()
		var l *lang
		if found, ok := langs[code]; ok {
			l = &found
		}
		cachedLang.Store(l)
		cached24.Store(clock24(config.Get().Screen.Clock24, code))
		cachedAt.Store(now)
	}
	return cachedLang.Load()
}

// Changed drops the cached language: call it after setting a new one, for the next frame to be in it.
func Changed() { cachedAt.Store(0) }

// Clock24 is the screen showing 15:04 rather than 3:04 PM: as chosen on the Show, or else as its
// language writes the time. Of the screen's languages only English uses 3:04 PM.
func Clock24() bool {
	current()
	return cached24.Load()
}

func clock24(chosen *bool, code string) bool {
	if chosen != nil {
		return *chosen
	}
	return code != "" && code != "en"
}

func fill(s string, pairs []string) string {
	if len(pairs) < 2 {
		return s
	}
	r := make([]string, 0, len(pairs))
	for i := 0; i+1 < len(pairs); i += 2 {
		r = append(r, "{"+pairs[i]+"}", pairs[i+1])
	}
	return strings.NewReplacer(r...).Replace(s)
}

// Date is t written with one of the screen's date layouts ("Monday, January 2", "Mon, Jan 2",
// "Jan 2", "Mon", "Monday", "January 2006"), in the screen's language and that language's order.
func Date(t time.Time, layout string) string {
	l := current()
	if l == nil {
		return t.Format(layout)
	}
	if own, ok := l.dates[layout]; ok {
		layout = own
	}
	return swapNames(l, t.Format(layout))
}

// Names is s with every English day and month name in it in the screen's language, for text that
// holds names without being a date, like an alarm's "Mon Wed Fri".
func Names(s string) string {
	if l := current(); l != nil {
		return swapNames(l, s)
	}
	return s
}

func swapNames(l *lang, s string) string {
	return names.ReplaceAllStringFunc(s, func(w string) string {
		if n, ok := l.names[w]; ok {
			return n
		}
		return w
	})
}

// names is every English day and month name time.Format writes, long ones first so "Monday" is
// not read as "Mon" and "day".
var names = regexp.MustCompile(`\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|` +
	`January|February|March|April|May|June|July|August|September|October|November|December|` +
	`Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b`)

// lang is one language: its strings, its day and month names, and its own order for the date
// layouts that need one.
type lang struct {
	text  map[string]string
	names map[string]string
	dates map[string]string
}

var langs = map[string]lang{
	"de": de,
	"es": es,
	"fr": fr,
	"it": it,
	"nl": nl,
}

// dayMonth builds a names table from the seven weekdays, seven short weekdays, twelve months and
// twelve short months, each in English order (Monday first, January first).
func dayMonth(days, shortDays, months, shortMonths []string) map[string]string {
	en := [][]string{
		{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"},
		{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"},
		{"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"},
		{"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"},
	}
	m := map[string]string{}
	for i, own := range [][]string{days, shortDays, months, shortMonths} {
		for j, w := range own {
			m[en[i][j]] = w
		}
	}
	// "May" is both the long and the short month; the long name wins.
	m["May"] = months[4]
	return m
}

// Sprintf is fmt.Sprintf with the format in the screen's language. A translation that needs the
// values in another order uses explicit indexes, like "%[2]s um %[1]s".
func Sprintf(format string, args ...any) string { return fmt.Sprintf(T(format), args...) }
