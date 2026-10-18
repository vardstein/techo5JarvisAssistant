package mic

import (
	"math"
	"sync"
)

// How loud the last second was, kept for one question: when two Shows hear the same wake word, which of
// them is the one being spoken to (GENBU-224). Both answer with their own numbers and Home Assistant
// compares, so the numbers have to mean the same on every Show: before any gain, in the same band the
// floor is kept in, and against that floor.
const (
	// One second of frames, which holds a wake word with room on either side: the model fires after the
	// word has ended, so what is wanted is behind it.
	heardFrames = 50

	// The loudest fifth of a second of it, as the level. A wake word is half a second or more, so this is
	// its louder syllables and none of the silence around them, and one cough or knock is a single frame
	// and cannot carry it alone.
	loudestFrames = 10

	// A sample this close to full scale before any gain means the converter ran out of room, and whatever
	// was said was louder than the number says.
	clipSample = 32000
)

// heard is the ring of the last second: the band level and the loudest raw sample of each frame. The
// reader writes it and the voice feature reads it from its own goroutine, hence the lock; it is taken once
// a frame for two stores.
type heard struct {
	mu    sync.Mutex
	band  [heardFrames]float32
	peak  [heardFrames]float32
	next  int
	count int
}

func (h *heard) push(band, peak float32) {
	h.mu.Lock()
	h.band[h.next], h.peak[h.next] = band, peak
	h.next = (h.next + 1) % heardFrames
	h.count = min(h.count+1, heardFrames)
	h.mu.Unlock()
}

// loudest is the mean energy of the loudest frames in the ring and whether any of them clipped.
func (h *heard) loudest() (rms float32, clipped bool) {
	h.mu.Lock()
	band := append([]float32(nil), h.band[:h.count]...)
	for _, p := range h.peak[:h.count] {
		clipped = clipped || p >= clipSample
	}
	h.mu.Unlock()

	if len(band) == 0 {
		return 0, clipped
	}
	// The loudest ten to the front, by selection: ten passes over fifty.
	for i := 0; i < min(loudestFrames, len(band)); i++ {
		for j := i + 1; j < len(band); j++ {
			if band[j] > band[i] {
				band[i], band[j] = band[j], band[i]
			}
		}
	}
	n := min(loudestFrames, len(band))
	var sum float64
	for _, v := range band[:n] {
		sum += float64(v) * float64(v)
	}
	return float32(math.Sqrt(sum / float64(n))), clipped
}

// Heard is how loud the last second was, for comparing one Show's hearing of a wake word with another's.
type Heard struct {
	// LevelDB is the loudest fifth of a second, in dBFS before gain, in the band the floor is kept in.
	LevelDB float64
	// FloorDB is the room's own noise in that band (Floor), so LevelDB less this is how far the voice
	// stood out.
	FloorDB float64
	// Clipped says the converter ran out of room, so the voice was louder than LevelDB says.
	Clipped bool
}

// Voice is what the last second sounded like. Ask it right after a wake word: the ring moves on with the
// next frame.
func (s *Source) Voice() Heard {
	rms, clipped := s.leveler.heard.loudest()
	return Heard{LevelDB: dbfs(rms), FloorDB: s.Floor(), Clipped: clipped}
}

// dbfs is a level against this package's full scale, with silence held at the bottom of the int16 range
// rather than minus infinity, which JSON cannot carry.
func dbfs(rms float32) float64 {
	return 20 * math.Log10(math.Max(float64(rms), 1)/fullScale)
}
