package voice

import (
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/lib/endpoint"
)

// level is a tenth of a second of a steady tone at amplitude a.
func level(a int16) []int16 {
	f := make([]int16, endpoint.Window)
	for i := range f {
		if i%2 == 0 {
			f[i] = a
		} else {
			f[i] = -a
		}
	}
	return f
}

func TestFollowGateHoldsTheRoom(t *testing.T) {
	var g followGate
	for i := 0; i < 50; i++ {
		if got := g.pass(level(300)); got != nil {
			t.Fatalf("window %d: sent %d samples of a quiet room", i, len(got))
		}
	}
}

func TestFollowGateHoldsSteadyMusic(t *testing.T) {
	var g followGate
	for i := 0; i < 50; i++ {
		if got := g.pass(level(3000)); got != nil {
			t.Fatalf("window %d: sent %d samples of steady music", i, len(got))
		}
	}
}

func TestFollowGateSendsTheStartWithTheOnset(t *testing.T) {
	var g followGate
	for i := 0; i < 20; i++ {
		g.pass(level(300))
	}
	if got := g.pass(level(3000)); got != nil {
		t.Fatal("opened on one loud window")
	}
	got := g.pass(level(3000))
	if len(got) != preRoll {
		t.Fatalf("sent %d samples on the onset, want the last %d", len(got), preRoll)
	}
	if got[len(got)-1] != -3000 || got[0] != 300 {
		t.Fatalf("sent %d..%d, want the quiet before and the speech up to now", got[0], got[len(got)-1])
	}
}

func TestBareOkay(t *testing.T) {
	for text, want := range map[string]bool{
		"Okay.":                        true,
		"okay":                         true,
		" OK! ":                        true,
		"Okay…":                        true,
		"Okay, das Licht ist an.":      false,
		"Pausiert":                     false,
		"":                             false,
		"Okay. Was möchtest du hören?": false,
	} {
		if got := bareOkay(text); got != want {
			t.Errorf("bareOkay(%q) = %v, want %v", text, got, want)
		}
	}
}

// Only a reply that ends by asking something keeps the microphone open, unless the slot says every.
func TestAsks(t *testing.T) {
	for text, want := range map[string]bool{
		"Was möchtest du hören?":           true,
		"Okay. Welches Zimmer? ":           true,
		"Meinst du „Küche“?":               true,
		"Soll ich das Licht anmachen?\n":   true,
		"Musik auf 30 Prozent.":            false,
		"Ist das Licht an? Ja, es ist an.": false,
		"":                                 false,
		"Das Wohnzimmer ist auf 21 Grad.":  false,
		// A question with the reason after it still asks (GENBU-117).
		"Wie lange? Das habe ich nicht verstanden.":                  true,
		"Welches Zimmer meinst du? Ich habe Küche und Bad gefunden.": true,
		"Für wann? Die Uhrzeit habe ich nicht verstanden. ":          true,
		"Meinst du „Küche“? Das gibt es zweimal.":                    true,
		"Ist es zu? Nein. Die Tür ist offen.":                        false,
		"Hast du das gemeint? Doch, das war es.":                     false,
		"Wo ist das? Es ist im Flur. Das Licht ist aus.":             false,
		"Die Version ist 3.5. Willst du sie haben?":                  true,
		"Ich habe z.B. Küche gefunden.":                              false,
		"Okay!":                                                      false,
	} {
		if got := asks(text); got != want {
			t.Errorf("asks(%q) = %v, want %v", text, got, want)
		}
	}
}
