package echoref

import (
	"log/slog"
	"math"
	"math/cmplx"

	"github.com/HuskerMinion/techo5/echod/internal/lib/fft"
)

const (
	// window is how much of the loopback one estimate compares, about a second.
	window = 1 << 14

	// search is how far behind the newest written sample the capture may be, about half a second:
	// everything queued in the playback device and the capture device together.
	search = 1 << 13

	// size is the transform, big enough that the correlation does not wrap over the search.
	size = 1 << 15

	// Estimates run this often while nothing is settled, and this often once it is, in samples. A
	// settled one only checks that nothing moved; a stream that breaks says so through its epoch.
	// While seeking, each estimate that finds no clear peak doubles the wait, up to backoff times, so
	// playback that cannot be timed does not keep a core busy.
	seeking = Rate / 2
	settled = 5 * Rate
	backoff = 3

	// agree is how far apart, in samples, two estimates may be and still count as the same.
	agree = 2

	// sharp is how far the correlation peak has to stand above the rest of the search for an
	// estimate to count at all. Music with a strong beat repeats itself, and a peak that only
	// narrowly beats its echoes a bar later is a guess.
	sharp = 8.0

	// Lead puts the reference this many samples ahead of where the estimate says it lines up, so a
	// path that arrives a hair before the loopback does is still inside the filter.
	Lead = 16

	// quiet is the mean square per sample, int16 scale, below which the loopback has too little in it
	// to time anything by. About -60 dBFS.
	quiet = 1e-6 * 32768 * 32768

	// The band the timing is read in. The loopback is right below 500 Hz and the written stream has
	// little above 3 kHz that the loopback still shares.
	lowHz  = 100
	highHz = 3000
)

// Aligner finds where the playback held in a Tap lines up with the capture, by matching it against
// the capture's own loopback. One per capture stream; not safe for concurrent use.
type Aligner struct {
	tap *Tap

	// loop is the most recent window of loopback, a ring at pos. captured counts every capture sample
	// seen, and since how many arrived after the last estimate.
	loop     []float32
	pos      int
	captured int64
	since    int

	// offset is where playback lines up: written sample captured+offset is what the loopback had at
	// capture sample captured. Good while locked and the Tap is still at epoch.
	locked bool
	offset int64
	epoch  uint64

	// moves counts every new lock, so a caller can tell the filter it was training has gone stale.
	moves uint64

	// pending is an estimate that has not yet been confirmed by the next one.
	pending   bool
	candidate int64

	// misses counts estimates in a row that found no clear peak.
	misses int

	// An estimate takes tens of milliseconds on a small board, several capture blocks, so it runs on
	// its own goroutine and Fill picks up the result on a later block. While busy, that goroutine owns
	// snap and everything below it. wait runs it in place instead, for tests.
	busy bool
	done chan result
	wait bool

	snap   []float32
	f      *fft.FFT
	a, b   []complex64
	hay    []int16
	weight []float32
}

// job is what an estimate needs from the capture side: the newest written sample the capture could
// line up with, and the epoch that was read under.
type job struct {
	newest int64
	epoch  uint64
}

// result is where an estimate put playback, or ok false if it found no clear peak.
type result struct {
	d     int64
	epoch uint64
	ok    bool
}

// NewAligner makes an Aligner reading from t.
func NewAligner(t *Tap) *Aligner {
	a := &Aligner{
		tap:    t,
		loop:   make([]float32, window),
		done:   make(chan result, 1),
		snap:   make([]float32, window),
		f:      fft.New(size),
		a:      make([]complex64, size),
		b:      make([]complex64, size),
		hay:    make([]int16, window+search),
		weight: make([]float32, size),
	}
	for k := range size/2 + 1 {
		hz := float64(k) * Rate / size
		if hz >= lowHz && hz <= highHz {
			a.weight[k] = 1
			a.weight[(size-k)%size] = 1
		}
	}
	return a
}

// Fill takes the loopback of one capture block and writes into dst, the same length, what the
// speaker played for those samples, Lead samples ahead. It reports false, leaving dst zeroed, while it
// does not know where that is; the caller should then fall back on the loopback.
//
// It has to see every block, playing or not, to keep count of where the capture is.
func (a *Aligner) Fill(loop []int16, dst []int16) bool {
	a.record(loop)

	select {
	case r := <-a.done:
		a.busy = false
		a.settle(r)
	default:
	}
	if !a.busy && a.since >= a.interval() && a.captured >= window {
		a.since = 0
		if j, ok := a.begin(); ok {
			if a.wait {
				a.settle(a.correlate(j))
			} else {
				a.busy = true
				go func() { a.done <- a.correlate(j) }()
			}
		}
	}

	if !a.locked {
		clear(dst)
		return false
	}
	start := a.captured - int64(len(loop))
	epoch, ok := a.tap.Read(start+a.offset+Lead, dst)
	if !ok || epoch != a.epoch {
		a.locked, a.pending, a.misses = false, false, 0
		slog.Info("echo reference lost", "reason", lostReason(ok))
		return false
	}
	return true
}

// Moves counts the times it locked on, which a caller compares to know whether the reference it is
// being given still lines up with the one it had.
func (a *Aligner) Moves() uint64 { return a.moves }

func lostReason(held bool) string {
	if !held {
		return "playback stopped"
	}
	return "playback restarted"
}

func (a *Aligner) interval() int {
	if a.locked {
		return settled
	}
	return seeking << min(a.misses, backoff)
}

func (a *Aligner) record(loop []int16) {
	for _, v := range loop {
		a.loop[a.pos] = float32(v)
		a.pos = (a.pos + 1) % window
	}
	a.captured += int64(len(loop))
	a.since += len(loop)
}

// begin takes what an estimate needs while the capture side still owns it: the last window of
// loopback, unrolled into snap, and everything written that it could be, in hay. It reports false
// when the loopback is too quiet to time anything by or the Tap no longer holds that far back.
func (a *Aligner) begin() (job, bool) {
	var e float64
	for _, v := range a.loop {
		e += float64(v) * float64(v)
	}
	if e/window < quiet {
		return job{}, false
	}

	// Playback cannot have reached the codec before it was written, so the newest capture sample
	// lines up at most with the newest written one.
	head, epoch := a.tap.Head()
	newest := head - a.captured
	from := a.captured - window + newest - search
	if _, ok := a.tap.Read(from, a.hay); !ok {
		return job{}, false
	}
	for i := range window {
		a.snap[i] = a.loop[(a.pos+i)%window]
	}
	return job{newest, epoch}, true
}

// correlate finds where in hay the loopback in snap sits, by GCC-PHAT over the band the loopback
// gets right.
func (a *Aligner) correlate(j job) result {
	for i := range a.a {
		a.a[i], a.b[i] = 0, 0
	}
	for i, v := range a.hay {
		a.a[i] = complex(float32(v), 0)
	}
	for i, v := range a.snap {
		a.b[i] = complex(v, 0)
	}
	a.f.Forward(a.a)
	a.f.Forward(a.b)
	for k := range a.a {
		g := a.a[k] * fft.Conj(a.b[k])
		if m := float32(cmplx.Abs(complex128(g))); m > 0 && a.weight[k] > 0 {
			a.a[k] = g * complex(a.weight[k]/m, 0)
		} else {
			a.a[k] = 0
		}
	}
	a.f.Inverse(a.a)

	best, at := float32(math.Inf(-1)), 0
	var sum float64
	for k := 0; k <= search; k++ {
		v := real(a.a[k])
		sum += float64(v) * float64(v)
		if v > best {
			best, at = v, k
		}
	}
	rms := math.Sqrt(sum / (search + 1))
	if rms == 0 || float64(best) < sharp*rms {
		return result{epoch: j.epoch}
	}
	return result{j.newest - search + int64(at), j.epoch, true}
}

// settle takes a finished estimate. One made against a stream that has since broken says nothing
// about the new one.
func (a *Aligner) settle(r result) {
	if _, epoch := a.tap.Head(); epoch != r.epoch {
		return
	}
	if !r.ok {
		a.misses++
		return
	}
	a.misses = 0
	a.consider(r.d, r.epoch)
}

func (a *Aligner) consider(d int64, epoch uint64) {
	if a.locked && epoch == a.epoch && abs(d-a.offset) <= agree {
		a.pending = false
		return
	}
	if a.pending && abs(d-a.candidate) <= agree {
		a.pending = false
		a.locked, a.offset, a.epoch = true, d, epoch
		a.moves++
		slog.Info("echo reference locked", "behind_ms", float64(a.lag())*1000/Rate)
		return
	}
	a.pending, a.candidate = true, d
}

// lag is how far the capture runs behind what was being written when it was read, for the log: the
// two devices' queues together.
func (a *Aligner) lag() int64 {
	head, _ := a.tap.Head()
	return head - a.captured - a.offset
}

func abs(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}
