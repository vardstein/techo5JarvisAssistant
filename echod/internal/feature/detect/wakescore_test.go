package detect

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/mic"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wake"
)

// TestWakeScore runs recordings through the real wake word models, frame by frame as the device feeds
// them, and counts how often each word comes close and how often it would fire. It answers "how many
// false starts would this policy cost over this much music" without a device or a room.
//
// It only runs when asked:
//
//	WAKESCORE_DIR    a folder of 16 kHz mono s16le files (*.raw), e.g. ffmpeg -ac 1 -ar 16000 -f s16le
//	WAKESCORE_MODELS the model folder (the build's inputs/models, which git does not carry)
//	WAKESCORE_WORDS  comma separated ids, default okay_nabu
//
// An event starts when a score reaches NearMiss and ends once it has stayed below that for
// NearMissSettle, the same way the engine settles a near miss; its peak is what gets counted.
func TestWakeScore(t *testing.T) {
	dir := os.Getenv("WAKESCORE_DIR")
	if dir == "" {
		t.Skip("WAKESCORE_DIR not set")
	}
	modelDir := os.Getenv("WAKESCORE_MODELS")
	if modelDir == "" {
		t.Fatal("WAKESCORE_MODELS not set")
	}
	words := strings.Split(envOr("WAKESCORE_WORDS", "okay_nabu"), ",")

	installed, err := wake.Installed(modelDir)
	if err != nil {
		t.Fatalf("models: %v", err)
	}
	files, _ := filepath.Glob(filepath.Join(dir, "*.raw"))
	sort.Strings(files)
	if len(files) == 0 {
		t.Fatalf("no *.raw files in %s", dir)
	}

	cuts := []float64{NearMiss, 0.7, 0.75, 0.8, 0.87, 0.9}
	total := map[string][]int{}
	var length time.Duration

	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		samples := make([]int16, len(raw)/2)
		for i := range samples {
			samples[i] = int16(uint16(raw[2*i]) | uint16(raw[2*i+1])<<8)
		}
		d := time.Duration(len(samples)) * time.Second / mic.Rate
		length += d

		var line []string
		for _, id := range words {
			peaks, top := scoreFile(t, installed, id, samples)
			if total[id] == nil {
				total[id] = make([]int, len(cuts))
			}
			for _, p := range peaks {
				for i, c := range cuts {
					if p >= c {
						total[id][i]++
					}
				}
			}
			line = append(line, fmt.Sprintf("%s top %.2f events %v", id, top, round(peaks)))
		}
		t.Logf("%s (%s): %s", filepath.Base(f), d.Round(time.Second), strings.Join(line, "; "))
	}

	hours := length.Hours()
	t.Logf("%d files, %s", len(files), length.Round(time.Second))
	for _, id := range words {
		var parts []string
		for i, c := range cuts {
			parts = append(parts, fmt.Sprintf(">=%.2f: %d (%.1f/h)", c, total[id][i], float64(total[id][i])/hours))
		}
		t.Logf("%s events with peak %s", id, strings.Join(parts, ", "))
	}
}

// scoreFile feeds one recording through a fresh engine holding one wake word and returns the peak of
// every event, and the highest score seen at all.
func scoreFile(t *testing.T, installed []wake.Model, id string, samples []int16) ([]float64, float64) {
	t.Helper()
	m, ok := wake.Find(installed, id)
	if !ok {
		t.Fatalf("no model %q", id)
	}
	b, err := newBackend(m.Kind)
	if err != nil {
		t.Fatalf("%s: %v", id, err)
	}
	defer b.close()
	if err := b.load(m); err != nil {
		t.Fatalf("%s: %v", id, err)
	}

	settle := int(NearMissSettle / (time.Second / mic.Rate))
	var peaks []float64
	peak, top, below, in := 0.0, 0.0, 0, false
	for at := 0; at+mic.FrameSamples <= len(samples); at += mic.FrameSamples {
		scores, fresh := b.feed(samples[at : at+mic.FrameSamples])
		if !fresh {
			continue
		}
		s := scores[id]
		top = max(top, s)
		switch {
		case s >= NearMiss:
			in, below = true, 0
			peak = max(peak, s)
		case in:
			below += mic.FrameSamples
			if below >= settle {
				peaks = append(peaks, peak)
				peak, below, in = 0, 0, false
			}
		}
	}
	if in {
		peaks = append(peaks, peak)
	}
	return peaks, top
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func round(v []float64) []float64 {
	out := make([]float64, len(v))
	for i, x := range v {
		out[i] = float64(int(x*100+0.5)) / 100
	}
	return out
}
