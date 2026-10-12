//go:build !dot && !spot

package display

import (
	"image"
	"testing"
	"time"
)

func TestSentenceCase(t *testing.T) {
	for in, want := range map[string]string{"wie wird das wetter": "Wie wird das wetter", " ändere das": "Ändere das", "": "", "OK": "OK"} {
		if got := sentenceCase(in); got != want {
			t.Errorf("sentenceCase(%q) = %q, want %q", in, got, want)
		}
	}
}

// The Classic page is drawn every frame of a turn, so it has to leave the Show 5 time for the rest.
func BenchmarkCarTurn(b *testing.B) {
	r := newRenderer(image.NewRGBA(image.Rect(0, 0, drawnFor, 480)))
	at := time.Date(2026, 10, 12, 14, 7, 0, 0, time.UTC)
	for _, phase := range []string{"listening", "thinking", "replying"} {
		s := scene{now: at, since: at.Add(-time.Second), phase: phase, heard: "Wie wird das Wetter morgen",
			reply: "Morgen wird es wechselhaft.", eq: classicVoice(0.6)}
		b.Run(phase, func(b *testing.B) {
			for b.Loop() {
				r.carTurn(s)
			}
		})
	}
}

// BenchmarkWaveTurn is the yardstick: the Wave page takes 48 ms a frame on a Show 5 (turn_draw.go).
func BenchmarkWaveTurn(b *testing.B) {
	r := newRenderer(image.NewRGBA(image.Rect(0, 0, drawnFor, 480)))
	at := time.Date(2026, 10, 12, 14, 7, 0, 0, time.UTC)
	v := classicVoice(0.6)
	v.classic, v.wave = false, true
	s := scene{now: at, since: at.Add(-time.Second), phase: "listening", eq: v}
	for b.Loop() {
		r.wave(s)
	}
}
