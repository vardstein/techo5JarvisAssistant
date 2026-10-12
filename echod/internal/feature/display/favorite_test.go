package display

import (
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
)

func TestTheStarKeepsToAPressUntilTheListsAgree(t *testing.T) {
	song := favKeyOf(home.Radio{Now: "Music Assistant", Title: "Reason That I Sing"})
	other := favKeyOf(home.Radio{Now: "Music Assistant", Title: "Harbour Song"})
	if song == "" || song == other {
		t.Fatalf("keys %q and %q, want two different ones", song, other)
	}
	if favKeyOf(home.Radio{}) != "" {
		t.Error("nothing playing has a key")
	}

	var p favPress
	if p.shown(song, true) != true || p.shown(song, false) != false {
		t.Error("with no press the star is not what the lists say")
	}
	// Saved: filled while the queue still says no, and the press is let go once it says yes.
	p = favPress{song, true}
	if !p.shown(song, false) {
		t.Error("the star went empty right after a press saved the song")
	}
	if !p.shown(song, true) || p.key != "" {
		t.Errorf("press %+v once the lists agree, want it let go", p)
	}
	// Taken out again on the lists elsewhere later: the old press does not come back.
	if p.shown(song, false) {
		t.Error("the star stayed filled after the song was taken out elsewhere")
	}
	// A press does not carry over to the next song.
	p = favPress{song, true}
	if p.shown(other, false) || p.key != "" {
		t.Error("a press for one song filled the star of the next")
	}
}
