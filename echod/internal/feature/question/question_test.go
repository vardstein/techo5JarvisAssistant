package question

import (
	"testing"
	"time"
)

func TestReply(t *testing.T) {
	for want, said := range map[Said][]string{
		Yes: {"Yes.", "yeah sure", "Sure!", "OK, do it", "okay go ahead", "yes please", "Install it now.", "Now.",
			"Ja.", "Ja, bitte.", "Klar!", "Gerne", "Mach das.", "Ja, installier es jetzt", "auf jeden Fall", "na klar",
			"Ja, das Update ausführen.", "Updaten.", "Führ es aus", "Aktualisieren",
			"Joa.", "Joah", "Jo, a.", "Jaaa!", "Jupp", "Japp.", "Passt.", "Logo", "Genau", "Na gut", "Mach mal", "Ja, mach ruhig",
			"O.K.", "o. k.", "Okidoki", "Yo", "Sounds good", "Klar doch.  Klar doch.",
			"Oui", "Sì, certo", "Sí, claro", "Ja graag"},
		No: {"No.", "no thanks", "Not now.", "later", "Maybe later", "don't", "wait",
			"Nein.", "Nein danke", "Jetzt nicht.", "Später", "nee", "Neeein", "Nö, lass mal", "Non", "No, dopo", "Nee, straks"},
		Other: {"", "what time is it", "yes and turn on the lights", "play some music", "thanks",
			"Wie spät ist es?", "Mach das Licht an", "Untertitel im Auftrag des ZDF", "Aus", "Update",
			"Naja", "Jein", "Mal sehen", "A"},
	} {
		for _, s := range said {
			if got := Reply(s); got != want {
				t.Errorf("%q read as %v, want %v", s, got, want)
			}
		}
	}
}

func TestAnswer(t *testing.T) {
	at := time.Date(2026, 10, 9, 20, 0, 0, 0, time.UTC)
	now = func() time.Time { return at }
	defer func() { now = time.Now }()

	answered := make(chan string, 1)
	ask := func() {
		Ask("update", time.Minute, func() { answered <- "yes" }, func() { answered <- "no" }, nil)
	}

	if Answer("yes") {
		t.Fatal("answered with no question open")
	}

	ask()
	if Answer("") || Answer("  ") {
		t.Fatal("silence answered the question")
	}
	if !Answer("Ja, bitte") || <-answered != "yes" {
		t.Fatal("yes was not taken")
	}
	if Answer("yes") {
		t.Fatal("the question stayed open after its answer")
	}

	ask()
	if !Answer("not now") || <-answered != "no" {
		t.Fatal("no was not taken")
	}

	// Something else goes on to the assistant, and the question is over.
	ask()
	if Answer("what time is it") || Answer("yes") {
		t.Fatal("the question outlived an unrelated request")
	}

	// Too late.
	ask()
	at = at.Add(2 * time.Minute)
	if Answer("yes") {
		t.Fatal("an answer after the window was taken")
	}

	ask()
	Withdraw("other")
	Withdraw("update")
	if Answer("yes") {
		t.Fatal("a withdrawn question was answered")
	}
	select {
	case a := <-answered:
		t.Fatalf("unexpected answer %q", a)
	default:
	}
}

// In the turn opened for the question, what is not an answer goes nowhere; after a wake word it goes on
// to the assistant. Either way the question is over, when it has no way to be asked again.
func TestTakeDropsWhatIsNotAnAnswer(t *testing.T) {
	answered := make(chan string, 1)
	ask := func() {
		Ask("update", time.Minute, func() { answered <- "yes" }, func() { answered <- "no" }, nil)
	}

	ask()
	if !Take("Untertitel im Auftrag des ZDF", true) {
		t.Fatal("television in the question's own turn went on to the assistant")
	}
	if Take("ja", true) {
		t.Fatal("the question outlived what was dropped")
	}

	ask()
	if Take("Wie spät ist es?", false) {
		t.Fatal("a request after a wake word was dropped")
	}

	ask()
	if !Take("Ja, das Update ausführen.", true) || <-answered != "yes" {
		t.Fatal("yes was not taken in the question's own turn")
	}

	if Take("Wie spät ist es?", true) {
		t.Fatal("a turn with no question open dropped a request")
	}
}

// A turn opened for the question that hears no words asks it once more, and the answer to that is
// taken as the answer to the question. A second silence does not ask a third time.
func TestMissedAsksOnceMore(t *testing.T) {
	at := time.Date(2026, 10, 13, 4, 31, 0, 0, time.UTC)
	now = func() time.Time { return at }
	oldAfter := againAfter
	againAfter = 0
	defer func() { now, againAfter = time.Now, oldAfter }()

	if Missed() {
		t.Fatal("asked again with no question open")
	}
	answered := make(chan string, 1)
	again := make(chan bool, 2)
	Ask("update", time.Minute, func() { answered <- "yes" }, func() { answered <- "no" }, func() { again <- true })

	at = at.Add(50 * time.Second)
	if !Missed() || !<-again {
		t.Fatal("the question was not asked again")
	}
	at = at.Add(50 * time.Second) // past the first minute, inside the second
	if Missed() {
		t.Fatal("asked a third time")
	}
	if !Take(" Joa!", true) || <-answered != "yes" {
		t.Fatal("the answer to the second asking was not taken")
	}

	// Words that answer nothing, in the question's own turn, ask it once more as well; the second time
	// they are only dropped.
	again2 := make(chan bool, 2)
	Ask("update", time.Minute, func() { answered <- "yes" }, func() { answered <- "no" }, func() { again2 <- true })
	if !Take("Untertitel im Auftrag des ZDF", true) || !<-again2 {
		t.Fatal("the question was not asked again after words that answer nothing")
	}
	if !Take("Mal sehen", true) {
		t.Fatal("the second unclear reply went on to the assistant")
	}
	select {
	case <-again2:
		t.Fatal("asked a third time")
	default:
	}
	if Take("ja", true) {
		t.Fatal("the question outlived its second asking")
	}

	// A wake word's request is not an answer either, but it is not asked again for.
	Ask("update", time.Minute, func() {}, func() {}, func() { again2 <- true })
	if Take("Wie spät ist es?", false) {
		t.Fatal("a request after a wake word was dropped")
	}
	select {
	case <-again2:
		t.Fatal("asked again after a request said after a wake word")
	default:
	}

	// Asked only once when there is nothing to ask again with.
	Ask("house", time.Minute, func() {}, func() {}, nil)
	if Missed() {
		t.Fatal("asked again without a way to ask")
	}
	Withdraw("house")

	// A question replaced before it is asked again is not asked for the old one.
	againAfter = 50 * time.Millisecond
	Ask("update", time.Minute, func() {}, func() {}, func() { again <- true })
	Missed()
	Withdraw("update")
	time.Sleep(150 * time.Millisecond)
	select {
	case <-again:
		t.Fatal("a withdrawn question was asked again")
	default:
	}
}
