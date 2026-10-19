package update

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

// Somebody at the Show is told why in words, and the log keeps the message it always had.
func TestReasonPutsTheKnownFailuresInPlainWords(t *testing.T) {
	refused := &net.OpError{Op: "dial", Net: "tcp", Err: fmt.Errorf("connect: connection refused")}
	for err, want := range map[error]string{
		mark(ErrNoRoom, fmt.Errorf("update: 3 bytes free on /data, need 9")):                       "not enough space on the Show",
		Manifest{Product: "other", Boards: []string{"crown"}}.ValidFor(JarvisShowProduct, "crown"): "the release isn't made for this Show",
		verify([]byte("m"), []byte(strings.Repeat("A", 88)), releaseKey):                           "the release isn't signed with Genbu's key",
		fmt.Errorf("update: fetching x: %w", refused):                                              "can't reach the update server",
		fmt.Errorf("%w (offline)", ErrClock):                                                       "the clock isn't set yet",
		fmt.Errorf("update: something new"):                                                        "",
	} {
		if err == nil {
			t.Fatalf("no error made for %q", want)
		}
		if got := Reason(err); got != want {
			t.Errorf("Reason(%v) = %q, want %q", err, got, want)
		}
	}

	err := mark(ErrNoRoom, fmt.Errorf("update: 3 bytes free"))
	if err.Error() != "update: 3 bytes free" {
		t.Errorf("marking changed the message: %q", err)
	}
}

// A hash that doesn't match is a damaged download, whatever the server says.
func TestADownloadThatDoesNotMatchIsDamaged(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("x")) }))
	defer srv.Close()
	err := download(context.Background(), Binary{URL: srv.URL, SHA256: strings.Repeat("a", 64), Size: 1},
		filepath.Join(t.TempDir(), "echod"), nil)
	if got := Reason(err); got != "the download was damaged" {
		t.Errorf("Reason(%v) = %q", err, got)
	}
}

// The screen says the step, so it must be the download's while it downloads, and nothing once an
// install has failed: a step left behind would keep the Show saying it is installing.
func TestTheStepFollowsTheInstall(t *testing.T) {
	setStep(StepNone)
	body := []byte("echod")
	sum := sha256.Sum256(body)
	var during Step
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		during = CurrentStep()
		w.Write(body)
	}))
	defer srv.Close()
	b := Binary{URL: srv.URL, SHA256: hex.EncodeToString(sum[:]), Size: int64(len(body))}
	if err := download(context.Background(), b, filepath.Join(t.TempDir(), "echod"), nil); err != nil {
		t.Fatal(err)
	}
	if during != StepDownloading {
		t.Errorf("step while downloading = %d, want %d", during, StepDownloading)
	}
	if CurrentStep() != StepChecking {
		t.Errorf("step after the download = %d, want checking", CurrentStep())
	}

	somewhere(t)
	if err := Install(context.Background(), Manifest{Product: JarvisShowProduct, Boards: []string{"crown"}}, nil); err == nil {
		t.Fatal("installed a manifest with no version")
	}
	if CurrentStep() != StepNone {
		t.Errorf("step after a failed install = %d, want none", CurrentStep())
	}
}

// Home Assistant hears each step once, as it changes.
func TestOnStepHearsEachChangeOnce(t *testing.T) {
	setStep(StepNone)
	var heard []Step
	OnStep(func(s Step) { heard = append(heard, s) })
	defer OnStep(func(Step) {})
	setStep(StepChecking)
	setStep(StepChecking)
	setStep(StepDownloading)
	setStep(StepNone)
	if fmt.Sprint(heard) != fmt.Sprint([]Step{StepChecking, StepDownloading, StepNone}) {
		t.Errorf("heard %v", heard)
	}
}
