package home

import (
	"strings"
	"testing"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
)

// The words a reply was cut at are only for the encrypted API, like the keys.
func TestReplyCutNeedsTheEncryptedAPI(t *testing.T) {
	f := &Feature{}
	a := f.replyCutAction()
	if !a.Answers {
		t.Error("reply_cut does not answer")
	}
	if got, err := a.Run(esphome.Call{}); err == nil {
		t.Errorf("reply_cut answered %v without an API key", got)
	}
}

func TestReplyCutAnswer(t *testing.T) {
	now := time.Now()
	var r replyCut
	if got := r.answer(now); len(got) != 0 {
		t.Errorf("no cut yet answered %v", got)
	}

	r.cut, r.at = voice.Cutoff{Heard: "The kitchen is at 21", Timed: true, Turn: "t1"}, now.Add(-3*time.Second)
	got := r.answer(now)
	if got["heard"] != "The kitchen is at 21" || got["timed"] != true || got["turn"] != "t1" || got["age_s"] != 3 {
		t.Errorf("answer = %v", got)
	}

	if got := r.answer(now.Add(replyCutKeep)); len(got) != 0 {
		t.Errorf("a cut older than %v answered %v", replyCutKeep, got)
	}
}

func TestLastOfStartsOnAWholeWord(t *testing.T) {
	if got := lastOf("short", 10); got != "short" {
		t.Errorf("lastOf short = %q", got)
	}
	long := strings.Repeat("word ", 60) + "end"
	got := lastOf(long, 20)
	if !strings.HasPrefix(got, "…word") || !strings.HasSuffix(got, "end") || len([]rune(got)) > 21 {
		t.Errorf("lastOf long = %q", got)
	}
}
