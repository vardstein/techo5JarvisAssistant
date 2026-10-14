package speaker

import (
	"bytes"
	"context"
	"errors"
	"io"
	"slices"
	"syscall"
	"testing"

	"github.com/HuskerMinion/techo5/echod/internal/lib/alsa"
	"github.com/HuskerMinion/techo5/echod/internal/service"
)

// flaky underruns a few times before taking anything, and keeps what it was handed each time.
type flaky struct {
	fails int
	got   [][]byte
}

func (f *flaky) Write(p []byte) (int, error) {
	f.got = append(f.got, bytes.Clone(p))
	if f.fails > 0 {
		f.fails--
		return 0, alsa.ErrUnderrun
	}
	return len(p), nil
}

// The frames were already taken off the queue, so an underrun must put the same ones back rather than
// move on to the next period.
func TestAnUnderrunRetriesTheSameAudio(t *testing.T) {
	p := &Player{}
	to := &flaky{fails: 2}
	buf := []byte{1, 2, 3, 4}

	if err := p.send(context.Background(), to, buf); err != nil {
		t.Fatalf("send = %v", err)
	}

	if len(to.got) != 3 {
		t.Fatalf("wrote %d times, want 3", len(to.got))
	}
	for i, got := range to.got {
		if !slices.Equal(got, buf) {
			t.Errorf("attempt %d wrote %v, wanted the same %v", i, got, buf)
		}
	}
	if p.Underruns() != 2 {
		t.Errorf("underruns = %d, want 2", p.Underruns())
	}
}

func TestAWriteThatWorksIsNotCountedOrRepeated(t *testing.T) {
	p := &Player{}
	to := &flaky{}

	if err := p.send(context.Background(), to, []byte{1, 2}); err != nil {
		t.Fatalf("send = %v", err)
	}

	if len(to.got) != 1 {
		t.Errorf("wrote %d times, want 1", len(to.got))
	}
	if p.Underruns() != 0 {
		t.Errorf("underruns = %d, want 0", p.Underruns())
	}
}

// dead never recovers, which is how a retry loop becomes a spin.
type dead struct{ writes int }

func (d *dead) Write(p []byte) (int, error) {
	d.writes++
	return 0, alsa.ErrUnderrun
}

func TestRetryingStopsWhenTheContextDoes(t *testing.T) {
	p := &Player{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if err := p.send(ctx, &dead{}, []byte{1, 2}); !errors.Is(err, context.Canceled) {
		t.Errorf("send = %v, want context.Canceled", err)
	}
}

func TestAnythingOtherThanAnUnderrunIsReturned(t *testing.T) {
	p := &Player{}
	want := errors.New("device gone")

	err := p.send(context.Background(), writerFunc(func([]byte) (int, error) {
		return 0, want
	}), []byte{1, 2})

	if !errors.Is(err, want) {
		t.Errorf("send = %v, want %v", err, want)
	}
	if p.Underruns() != 0 {
		t.Errorf("underruns = %d, want 0", p.Underruns())
	}
}

type writerFunc func([]byte) (int, error)

func (w writerFunc) Write(p []byte) (int, error) { return w(p) }

// A write the driver cut short has to carry on from where it stopped, not drop the rest of the period.
func TestAShortWriteSendsTheRest(t *testing.T) {
	p := &Player{}
	var got []byte
	calls := 0
	err := p.send(context.Background(), writerFunc(func(b []byte) (int, error) {
		calls++
		n := min(len(b), 4)
		got = append(got, b[:n]...)
		return n, nil
	}), []byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10})

	if err != nil {
		t.Fatalf("send = %v", err)
	}
	if want := []byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}; !slices.Equal(got, want) {
		t.Errorf("played %v, want %v", got, want)
	}
	if calls != 3 {
		t.Errorf("wrote %d times, want 3", calls)
	}
}

func TestAnInterruptedWriteIsRetried(t *testing.T) {
	p := &Player{}
	calls := 0
	err := p.send(context.Background(), writerFunc(func(b []byte) (int, error) {
		calls++
		if calls == 1 {
			return 0, syscall.EINTR
		}
		return len(b), nil
	}), []byte{1, 2})

	if err != nil || calls != 2 {
		t.Errorf("send = %v after %d writes, want nil after 2", err, calls)
	}
}

func TestAWriterThatTakesNothingIsNotASpin(t *testing.T) {
	p := &Player{}
	err := p.send(context.Background(), writerFunc(func([]byte) (int, error) { return 0, nil }), []byte{1, 2})
	if !errors.Is(err, io.ErrShortWrite) {
		t.Errorf("send = %v, want io.ErrShortWrite", err)
	}
}

// A driver that underruns after every few periods is stuck, not starved, and the stream has to be
// opened again: re-preparing it is what a write already does, and it never came back from that
// (GENBU-202). Asking for a restart is what gets the supervisor to close and open the device.
func TestUnderrunsBackToBackAskForARestart(t *testing.T) {
	p := &Player{}
	writes := 0
	stuck := writerFunc(func(b []byte) (int, error) {
		writes++
		if writes%4 == 0 {
			return 0, alsa.ErrUnderrun
		}
		return len(b), nil
	})

	var err error
	for range 1000 {
		if err = p.send(context.Background(), stuck, []byte{1, 2}); err != nil {
			break
		}
	}

	if !errors.Is(err, errStuck) || !errors.Is(err, service.ErrRestart) {
		t.Fatalf("send = %v, want errStuck asking for a restart", err)
	}
	if p.Underruns() != stormUnderruns {
		t.Errorf("gave up after %d underruns, want %d", p.Underruns(), stormUnderruns)
	}
}

// An underrun now and then is the loop being late, which a prepare recovers from. However many there
// are over a day, a calm second between them means the stream is fine.
func TestUnderrunsWithCalmBetweenThemDoNotRestart(t *testing.T) {
	p := &Player{}
	writes := 0
	late := writerFunc(func(b []byte) (int, error) {
		writes++
		if writes%(calmPeriods+2) == 0 {
			return 0, alsa.ErrUnderrun
		}
		return len(b), nil
	})

	for i := range stormUnderruns * 3 * (calmPeriods + 2) {
		if err := p.send(context.Background(), late, []byte{1, 2}); err != nil {
			t.Fatalf("send %d = %v after %d underruns, want nil", i, err, p.Underruns())
		}
	}
	if p.Underruns() < 2*stormUnderruns {
		t.Fatalf("only %d underruns, the test is not exercising anything", p.Underruns())
	}
}
