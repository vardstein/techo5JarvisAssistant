package echoref

import (
	"encoding/binary"
	"math"
	"math/rand/v2"
	"testing"
	"time"
)

// stereo packs mono samples at three times Rate as a playback period.
func stereo(x []float64) []byte {
	b := make([]byte, 4*len(x))
	for i, v := range x {
		s := uint16(int16(max(-32768, min(32767, v))))
		binary.LittleEndian.PutUint16(b[4*i:], s)
		binary.LittleEndian.PutUint16(b[4*i+2:], s)
	}
	return b
}

func rms(x []int16) float64 {
	var s float64
	for _, v := range x {
		s += float64(v) * float64(v)
	}
	return math.Sqrt(s / float64(len(x)))
}

func TestTapKeepsTheBandAndDropsWhatWouldAlias(t *testing.T) {
	for _, c := range []struct {
		hz   float64
		want func(float64) bool
	}{
		{1000, func(r float64) bool { return math.Abs(r-1) < 0.02 }},
		{6000, func(r float64) bool { return math.Abs(r-1) < 0.05 }},
		// 10 kHz would fold onto 6 kHz.
		{10000, func(r float64) bool { return r < 0.01 }},
	} {
		tap := NewTap()
		const amp = 10000
		x := make([]float64, 768)
		var n int
		for range 40 {
			for i := range x {
				x[i] = amp * math.Sin(2*math.Pi*c.hz*float64(n)/(factor*Rate))
				n++
			}
			tap.Push(stereo(x))
		}
		head, _ := tap.Head()
		if head != 40*768/factor {
			t.Fatalf("%v Hz: head %d", c.hz, head)
		}
		out := make([]int16, 4000)
		if _, ok := tap.Read(head-4000, out); !ok {
			t.Fatal("not held")
		}
		if r := rms(out) / (amp / math.Sqrt2); !c.want(r) {
			t.Errorf("%v Hz comes out at %.3f of its level", c.hz, r)
		}
	}
}

func TestTapReadsOnlyWhatItHolds(t *testing.T) {
	tap := NewTap()
	tap.Push(make([]byte, 4*768))
	dst := make([]int16, 10)
	if _, ok := tap.Read(250, dst); ok {
		t.Error("read past the head")
	}
	if _, ok := tap.Read(-1, dst); ok {
		t.Error("read before the start")
	}
	if _, ok := tap.Read(246, dst); !ok {
		t.Error("refused what it holds")
	}
}

// room plays noise through a Tap and captures it back the way a board does: the capture runs lag
// samples behind what was written, the loopback is right only in the low band, and the
// microphone hears the speaker through a short path.
type room struct {
	tap      *Tap
	rng      *rand.Rand
	src      []float64
	written  int
	captured int
	lag      int
}

func newRoom(lag int) *room {
	r := &room{tap: NewTap(), rng: rand.New(rand.NewPCG(1, 2)), lag: lag}
	return r
}

// play writes periods until the Tap is lag ahead of the capture.
func (r *room) play() {
	for r.written/factor < r.captured+r.lag+320 {
		x := make([]float64, 768)
		for i := range x {
			x[i] = r.rng.NormFloat64() * 3000
		}
		r.src = append(r.src, x...)
		r.tap.Push(stereo(x))
		r.written += len(x)
	}
}

// capture returns one 20 ms block of loopback: what was written, decimated, low-passed hard, with an
// unrelated signal on top where the loopback goes wrong.
func (r *room) capture() []int16 {
	r.play()
	out := make([]int16, 320)
	for i := range out {
		n := (r.captured + i) * factor
		var lo float64
		for k := range 24 {
			if n-k >= 0 {
				lo += r.src[n-k] / 24
			}
		}
		out[i] = int16(lo*3 + r.rng.NormFloat64()*1500)
	}
	r.captured += len(out)
	return out
}

func TestAlignerLocksOnAndFollowsABreak(t *testing.T) {
	r := newRoom(3000)
	a := NewAligner(r.tap)
	a.wait = true
	dst := make([]int16, 320)

	locked := -1
	for b := range 300 {
		if a.Fill(r.capture(), dst) {
			locked = b
			break
		}
	}
	if locked < 0 {
		t.Fatal("never locked")
	}
	if locked*320 > 3*Rate {
		t.Errorf("took %d ms to lock", locked*20)
	}
	// Written index m is the filter centred on input 3m-28, and the loopback at capture n is centred on
	// input 3n-11.5, so they line up at m = n+5.5. Read off a band edge it lands within a few samples.
	if a.offset < 1 || a.offset > 9 {
		t.Errorf("locked at %d, want about 5", a.offset)
	}
	if a.Moves() != 1 {
		t.Errorf("moves %d", a.Moves())
	}

	// The playback stalls for half a second and goes on where it was, so from here the capture hears
	// what was written half a second further back.
	r.tap.Break()
	r.captured -= Rate / 2
	first := a.offset
	if a.Fill(r.capture(), dst) {
		t.Error("still locked after the stream broke")
	}
	again := false
	for range 300 {
		if a.Fill(r.capture(), dst) {
			again = true
			break
		}
	}
	if !again {
		t.Fatal("never locked again")
	}
	if d := a.offset - first; d < -Rate/2-2 || d > -Rate/2+2 {
		t.Errorf("moved by %d, want %d", d, -Rate/2)
	}
}

// The way the daemon runs it: the estimate goes off on its own goroutine while blocks keep coming,
// and lands a few blocks later.
func TestAlignerLocksOnInTheBackground(t *testing.T) {
	r := newRoom(3000)
	a := NewAligner(r.tap)
	dst := make([]int16, 320)
	for range 2000 {
		if a.Fill(r.capture(), dst) {
			if a.offset < 1 || a.offset > 9 {
				t.Errorf("locked at %d, want about 5", a.offset)
			}
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("never locked")
}

// Playback the loopback does not match, so no estimate finds a peak: they get further apart.
func TestAlignerBacksOffWhatItCannotTime(t *testing.T) {
	r := newRoom(3000)
	a := NewAligner(r.tap)
	a.wait = true
	dst := make([]int16, 320)
	starts := 0
	for range 10 * Rate / 320 {
		loop := r.capture()
		// The loopback hears something unrelated to what was written.
		for i := range loop {
			loop[i] = int16(r.rng.NormFloat64() * 3000)
		}
		before := a.since
		a.Fill(loop, dst)
		if a.since < before {
			starts++
		}
	}
	if a.locked {
		t.Fatal("locked onto noise")
	}
	if a.misses < backoff {
		t.Errorf("only %d misses", a.misses)
	}
	// Ten seconds at 0.5, 1, 2, 4, 4 s between estimates is six of them; without backing off, twenty.
	if starts > 8 {
		t.Errorf("%d estimates in ten seconds", starts)
	}
}

func TestAlignerStaysOffOnSilence(t *testing.T) {
	a := NewAligner(NewTap())
	a.wait = true
	dst := make([]int16, 320)
	for range 200 {
		if a.Fill(make([]int16, 320), dst) {
			t.Fatal("locked onto nothing")
		}
	}
}

// BenchmarkEstimate is one estimate on a full window of music-like noise. They run every half second
// while seeking and every five once locked, so the two metrics are the share of one core each costs.
func BenchmarkEstimate(b *testing.B) {
	r := newRoom(3000)
	a := NewAligner(r.tap)
	for a.captured < window+search {
		a.record(r.capture())
	}
	j, ok := a.begin()
	if !ok {
		b.Fatal("nothing to estimate")
	}
	b.ResetTimer()
	for range b.N {
		a.correlate(j)
	}
	per := float64(b.Elapsed().Nanoseconds()) / float64(b.N)
	b.ReportMetric(per/(seeking*1e9/Rate)*100, "%core-seeking")
	b.ReportMetric(per/(settled*1e9/Rate)*100, "%core-settled")
}
