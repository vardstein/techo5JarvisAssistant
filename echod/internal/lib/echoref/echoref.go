// Package echoref gives an echo canceller what the speaker is really playing.
//
// A codec that loops its playback back into the capture stream offers the obvious reference: it is
// sample-aligned with the microphones by construction. On some boards it is also not what the
// speaker plays. Measured on one against the stream the daemon writes, the loopback agrees below
// 500 Hz and comes apart above it: its coherence with the microphone is 0.35 to 0.52 between 1 and
// 4 kHz where the written stream reaches 0.85, and a filter given the loopback removes 10 dB of music
// where the written stream lets it remove 28.
//
// So the playback side keeps what it wrote, at the capture rate (Tap), and the capture side works
// out where that sits against its own samples (Aligner). The loopback is still used, but only for
// the timing, which it gets right.
//
// Nothing here knows about a particular device, so it can be lifted out and used elsewhere.
package echoref

import (
	"encoding/binary"
	"math"
	"sync"
	"time"
)

const (
	// Rate is the rate the reference is kept at, the capture rate. The playback side runs three
	// times faster.
	Rate   = 16000
	factor = 3

	// ringLen is how much is kept, about four seconds. It has to hold the longest stretch the
	// Aligner compares plus the furthest it searches, with room to spare.
	ringLen = 1 << 16

	// stall is how long the writer may go without pushing before its stream counts as broken: the
	// device stopped or reopened, so what was written no longer sits where it did against the
	// capture.
	stall = 100 * time.Millisecond
)

// lowpass is the anti-aliasing filter in front of the decimation: a windowed sinc cut at the new
// Nyquist frequency, Kaiser window with beta 5, 61 taps. The same design a polyphase resampler uses
// by default, so a reference made offline with one matches what this makes.
var lowpass = func() []float32 {
	const half = 10 * factor
	const beta = 5.0
	h := make([]float32, 2*half+1)
	var sum float64
	coef := make([]float64, len(h))
	for i := range coef {
		t := float64(i - half)
		x := math.Pi * t / factor
		s := 1.0
		if x != 0 {
			s = math.Sin(x) / x
		}
		r := t / half
		w := bessel0(beta*math.Sqrt(max(0, 1-r*r))) / bessel0(beta)
		coef[i] = s * w
		sum += coef[i]
	}
	for i := range h {
		h[i] = float32(coef[i] / sum)
	}
	return h
}()

// bessel0 is the modified Bessel function of the first kind, order zero, by its series.
func bessel0(x float64) float64 {
	sum, term := 1.0, 1.0
	for k := 1; k < 50; k++ {
		term *= (x / (2 * float64(k))) * (x / (2 * float64(k)))
		sum += term
		if term < 1e-12*sum {
			break
		}
	}
	return sum
}

// Played is what this device's speaker plays. The playback loop pushes into it and the echo
// canceller reads from it.
var Played = NewTap()

// Tap keeps the most recent playback, mono, at Rate. Safe for one writer and any number of readers.
type Tap struct {
	mu   sync.Mutex
	ring []int16

	// head counts samples written at Rate since the Tap was made.
	head int64

	// epoch moves whenever the stream breaks, so a reader knows any timing it worked out is stale.
	epoch uint64
	last  time.Time

	// in holds the tail of the playback the filter still needs, followed by the newest push. phase
	// is how many input samples have gone by since the last output.
	in    []float32
	phase int
}

// NewTap makes an empty Tap.
func NewTap() *Tap {
	return &Tap{ring: make([]int16, ringLen), in: make([]float32, len(lowpass)-1)}
}

// Push takes one period as written to the codec: interleaved 16-bit little-endian stereo at three
// times Rate. The two channels are averaged.
func (t *Tap) Push(b []byte) {
	t.mu.Lock()
	defer t.mu.Unlock()

	now := time.Now()
	if !t.last.IsZero() && now.Sub(t.last) > stall {
		t.epoch++
	}
	t.last = now

	keep := len(lowpass) - 1
	for i := 0; i+3 < len(b); i += 4 {
		l := int16(binary.LittleEndian.Uint16(b[i:]))
		r := int16(binary.LittleEndian.Uint16(b[i+2:]))
		t.in = append(t.in, (float32(l)+float32(r))/2)
	}

	// One output every factor-th input, counted across pushes by phase. Each is the filter over the
	// input ending there, which is why the last keep inputs are carried over.
	n := len(t.in)
	for i := keep; i < n; i++ {
		if t.phase++; t.phase < factor {
			continue
		}
		t.phase = 0
		var y float32
		x := t.in[i-keep : i+1]
		for k, h := range lowpass {
			y += h * x[keep-k]
		}
		t.ring[t.head%ringLen] = clamp(y)
		t.head++
	}
	copy(t.in, t.in[n-keep:])
	t.in = t.in[:keep]
}

// Break says the stream just broke, an underrun say, so what follows sits somewhere else against the
// capture than what came before.
func (t *Tap) Break() {
	t.mu.Lock()
	t.epoch++
	t.mu.Unlock()
}

// Head is how many samples have been written, and the current epoch.
func (t *Tap) Head() (int64, uint64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.head, t.epoch
}

// Read copies samples from..from+len(dst) into dst. It reports false, with dst zeroed, unless all of
// them are still held; the epoch is as of the read.
func (t *Tap) Read(from int64, dst []int16) (uint64, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()

	end := from + int64(len(dst))
	if from < 0 || end > t.head || from < t.head-ringLen {
		clear(dst)
		return t.epoch, false
	}
	for i := range dst {
		dst[i] = t.ring[(from+int64(i))%ringLen]
	}
	return t.epoch, true
}

func clamp(v float32) int16 {
	switch {
	case v > math.MaxInt16:
		return math.MaxInt16
	case v < math.MinInt16:
		return math.MinInt16
	}
	return int16(v)
}
