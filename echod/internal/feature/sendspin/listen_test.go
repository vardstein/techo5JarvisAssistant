package sendspin

import (
	"context"
	"net"
	"testing"
	"time"
)

func mustIP(t *testing.T, s string) net.IP {
	t.Helper()
	ip := net.ParseIP(s)
	if ip == nil {
		t.Fatalf("bad test IP %q", s)
	}
	return ip
}

func TestListenerOnlyAdmitsPairedServerIP(t *testing.T) {
	l := &listener{serverIP: mustIP(t, "10.0.0.40")}
	for _, tc := range []struct {
		remote string
		want   bool
	}{
		{"10.0.0.40:42000", true},
		{"10.0.0.41:42000", false},
		{"garbage", false},
	} {
		if got := l.allowed(tc.remote); got != tc.want {
			t.Fatalf("allowed(%q)=%v want %v", tc.remote, got, tc.want)
		}
	}
}

func TestUnpairedListenerAdmitsNobody(t *testing.T) {
	var l listener
	if l.allowed("10.0.0.40:42000") {
		t.Fatal("unpaired listener admitted a peer")
	}
}

func TestNormalizeServerIP(t *testing.T) {
	if got, err := normalizeServerIP(" 10.0.0.40 "); err != nil || got != "10.0.0.40" {
		t.Fatalf("normalize IPv4 = %q, %v", got, err)
	}
	if got, err := normalizeServerIP(""); err != nil || got != "" {
		t.Fatalf("normalize empty = %q, %v", got, err)
	}
	if _, err := normalizeServerIP("music-assistant.local"); err == nil {
		t.Fatal("hostname accepted; exact source-IP pairing would no longer be deterministic")
	}
}

func TestNormalizeServerIPAcceptsOnlyLiteralAddresses(t *testing.T) {
	for _, good := range []string{"10.0.0.40", " 10.0.0.40 ", "fd00::34"} {
		if got, err := normalizeServerIP(good); err != nil || got == "" {
			t.Errorf("normalizeServerIP(%q) = %q, %v", good, got, err)
		}
	}
	for _, bad := range []string{"music-assistant.local", "https://10.0.0.40", "10.0.0.40:8928", "not-an-ip"} {
		if got, err := normalizeServerIP(bad); err == nil {
			t.Errorf("normalizeServerIP(%q) = %q, want error", bad, got)
		}
	}
	if got, err := normalizeServerIP(" "); err != nil || got != "" {
		t.Errorf("unpair = %q, %v", got, err)
	}
}

// A port still held by the listener being replaced is waited for, not given up on (GENBU-133).
func TestBindWaitsForThePortToComeFree(t *testing.T) {
	old, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := old.Addr().String()
	time.AfterFunc(300*time.Millisecond, func() { old.Close() })

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ln, err := bind(ctx, addr)
	if err != nil {
		t.Fatalf("bind gave up on a port that came free: %v", err)
	}
	ln.Close()
}

// Stopping while it waits ends the wait.
func TestBindStopsWaitingWhenStopped(t *testing.T) {
	held, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	if ln, err := bind(ctx, held.Addr().String()); err == nil {
		ln.Close()
		t.Fatal("bind took a port that was held the whole time")
	}
	if ctx.Err() == nil {
		t.Fatal("bind returned before it was stopped")
	}
}
