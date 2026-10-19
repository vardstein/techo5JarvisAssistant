package i18n

import (
	"path/filepath"
	"regexp"
	"slices"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
)

// speak sets the screen to lang for the rest of the test.
func speak(t *testing.T, lang string) {
	t.Helper()
	config.Use(filepath.Join(t.TempDir(), "state.json"))
	if err := config.Set().Screen().Language(lang); err != nil {
		t.Fatal(err)
	}
	Changed()
	t.Cleanup(Changed)
}

func TestEnglishIsTheKey(t *testing.T) {
	speak(t, "")
	if got := T("Check now"); got != "Check now" {
		t.Errorf("T = %q", got)
	}
	speak(t, "en")
	if got := T("Settings"); got != "Settings" {
		t.Errorf("T = %q on an English screen", got)
	}
}

// Show 5: picking Deutsch saved the language and left every word on the screen in English.
func TestPickingALanguageChangesTheNextFrame(t *testing.T) {
	speak(t, "")
	if got := T("Settings"); got != "Settings" {
		t.Fatalf("T = %q", got)
	}
	if err := config.Set().Screen().Language("de"); err != nil {
		t.Fatal(err)
	}
	Changed()
	if got := T("Settings"); got != "Einstellungen" {
		t.Errorf("after Changed, T = %q, want Einstellungen", got)
	}
}

// bathroomShow5 asked out loud in English through a German pipeline: English before anyone said a
// language is a fallback, not an answer.
func TestTheLanguageIsKnownOnlyOnceSaid(t *testing.T) {
	speak(t, "")
	old := assistant.Load()
	assistant.Store(nil)
	t.Cleanup(func() { assistant.Store(old) })
	if Known() {
		t.Fatal("known with no screen language and nothing from Home Assistant")
	}
	SetAssistant("de-DE")
	if !Known() || Lang() != "de" {
		t.Errorf("after Home Assistant said de-DE: known %v, lang %q", Known(), Lang())
	}
	speak(t, "en")
	assistant.Store(nil)
	if !Known() {
		t.Error("a screen set to English is an answer")
	}
}

func TestUntranslatedStaysEnglish(t *testing.T) {
	speak(t, "de")
	if got := T("Something nobody translated"); got != "Something nobody translated" {
		t.Errorf("T = %q", got)
	}
	speak(t, "xx")
	if got := T("Settings"); got != "Settings" {
		t.Errorf("an unknown language gave %q", got)
	}
}

// GENBU-201: a name that happens to be a UI word is drawn as it is. A Wi-Fi network called "Office"
// stayed "Büro" on a German screen.
func TestVerbatimTextIsNotTranslated(t *testing.T) {
	speak(t, "de")
	if got := T("Office"); got != "Büro" {
		t.Fatalf("T(Office) = %q, the test needs a key that is translated", got)
	}
	for _, s := range []string{"Office", "Connected", "Office Party"} {
		if got := T(Verbatim(s)); got != s {
			t.Errorf("T(Verbatim(%q)) = %q", s, got)
		}
	}
	// Text made from a marked name by joining it to others keeps it untranslated and loses every mark.
	if got := T("Home" + " · " + Verbatim("Office")); got != "Home · Office" {
		t.Errorf("joined = %q", got)
	}
	if Verbatim("") != "" || Verbatim(Verbatim("x")) != Verbatim("x") {
		t.Error("an empty name stays empty and a marked one isn't marked twice")
	}
}

func TestTemplatesPutValuesWhereTheLanguageWants(t *testing.T) {
	speak(t, "de")
	got := F("{time} left", "time", "4:59")
	if got != "noch 4:59" {
		t.Errorf("F = %q", got)
	}
	got = F("Up to date · {current} · checked {time}", "current", "v1.0.1", "time", "14:02")
	if got != "Aktuell · v1.0.1 · geprüft 14:02" {
		t.Errorf("F = %q", got)
	}
	if got := Sprintf("Missed: %s at %s", "Tee", "7:00"); got != "Verpasst: Tee um 7:00" {
		t.Errorf("Sprintf = %q", got)
	}
}

func TestDatesInTheLanguagesOwnOrder(t *testing.T) {
	day := time.Date(2026, time.March, 2, 9, 0, 0, 0, time.UTC) // a Monday
	cases := []struct{ lang, layout, want string }{
		{"", "Monday, January 2", "Monday, March 2"},
		{"de", "Monday, January 2", "Montag, 2. März"},
		{"de", "Mon, Jan 2", "Mo, 2. Mär"},
		{"de", "January 2006", "März 2026"},
		{"es", "Monday, January 2", "lunes, 2 de marzo"},
		{"es", "January 2006", "marzo de 2026"},
		{"fr", "Mon, Jan 2", "lun. 2 mars"},
		{"it", "Monday, January 2", "lunedì 2 marzo"},
		{"nl", "Jan 2", "2 mrt"},
	}
	for _, c := range cases {
		speak(t, c.lang)
		if got := Date(day, c.layout); got != c.want {
			t.Errorf("%s %q: got %q, want %q", c.lang, c.layout, got, c.want)
		}
	}
	// May is a long and a short name at once.
	speak(t, "de")
	if got := Date(time.Date(2026, time.May, 4, 0, 0, 0, 0, time.UTC), "Jan 2"); got != "4. Mai" {
		t.Errorf("May: %q", got)
	}
}

func TestNamesInsideText(t *testing.T) {
	speak(t, "nl")
	if got := Names(T("Mon Wed Fri")); got != "ma wo vr" {
		t.Errorf("Names = %q", got)
	}
}

// Every translation keeps the key's placeholders, or a value is lost or Sprintf prints %!s.
func TestTranslationsKeepPlaceholders(t *testing.T) {
	marks := regexp.MustCompile(`\{\w+\}|%[-.\d\[\]]*[a-z%]`)
	index := regexp.MustCompile(`\[\d+\]`) // "%[2]s" moves a value, it is still a %s
	for code, l := range langs {
		for key, text := range l.text {
			want, got := marks.FindAllString(key, -1), marks.FindAllString(index.ReplaceAllString(text, ""), -1)
			slices.Sort(want)
			slices.Sort(got)
			if !slices.Equal(want, got) {
				t.Errorf("%s: %q has %v, its key %q has %v", code, text, got, key, want)
			}
		}
		if n := len(l.names); n < 7+7+12+11 {
			t.Errorf("%s: %d day and month names", code, n)
		}
	}
}

// TestTheClockFollowsTheLanguageUntilChosen: an Echo Show set up in German said 7:43 PM, as nobody had
// picked a clock format. Unset, the language decides; a choice on the Show wins either way.
func TestTheClockFollowsTheLanguageUntilChosen(t *testing.T) {
	defer func(was *string) { assistant.Store(was) }(assistant.Load())
	for _, english := range []string{"", "en"} {
		speak(t, english)
		if Clock24() {
			t.Errorf("English (%q) without a choice is 24-hour", english)
		}
	}
	if err := config.Set().Screen().Language("de"); err != nil {
		t.Fatal(err)
	}
	Changed()
	if !Clock24() {
		t.Error("German without a choice is 12-hour")
	}
	if err := config.Set().Screen().Clock24(false); err != nil {
		t.Fatal(err)
	}
	Changed()
	if Clock24() {
		t.Error("12-hour chosen on a German screen did not hold")
	}

	// A screen that matches the assistant follows the pipeline's language too.
	speak(t, "")
	SetAssistant("de-CH")
	if !Clock24() {
		t.Error("a screen following a German assistant is 12-hour")
	}
	if err := config.Set().Screen().Clock24(true); err != nil {
		t.Fatal(err)
	}
	SetAssistant("en-US")
	if !Clock24() {
		t.Error("24-hour chosen did not hold in English")
	}
}
