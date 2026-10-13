package sendspin

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/speaker"
)

const Port = 8928

// listener accepts the servers that dial in, one at a time. The spec ranks competing servers by
// declared activity; until that is implemented the first to arrive holds the room.
type listener struct {
	out      *out
	bg       *speaker.Arbiter
	serverIP net.IP

	// report says what the room is doing, for the diagnostic sensor. Called from the accept goroutine
	// and from the session, so whatever it writes to has to tolerate that.
	report func(string)

	mu   sync.Mutex
	busy bool
}

func newListener(o *out, bg *speaker.Arbiter, report func(string), server string) *listener {
	return &listener{out: o, bg: bg, report: report, serverIP: net.ParseIP(server)}
}

// serve holds the port until ctx ends.
func (l *listener) serve(ctx context.Context, name string) error {
	ln, err := bind(ctx, net.JoinHostPort("", strconv.Itoa(Port)))
	if err != nil {
		return err
	}

	up := websocket.Upgrader{
		// Any origin: a server dialing in is not a browser, and there is nothing here a page could
		// reach that the network cannot already.
		CheckOrigin: func(*http.Request) bool { return true },
	}

	mux := http.NewServeMux()
	mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
		if !l.allowed(r.RemoteAddr) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		if !l.take() {
			http.Error(w, "already connected", http.StatusConflict)
			return
		}
		defer l.give()

		conn, err := up.Upgrade(w, r, nil)
		if err != nil {
			slog.Warn("sendspin upgrade failed", "from", r.RemoteAddr, "err", err)
			return
		}
		defer conn.Close()

		slog.Info("sendspin server connected", "from", r.RemoteAddr)
		l.report(stateJoined)
		defer l.report(stateWaiting)

		s := newSession(conn, l.out, l.bg, name, l.report)
		if err := s.run(ctx); err != nil {
			slog.Warn("sendspin session ended", "from", r.RemoteAddr, "err", err)
		} else {
			slog.Info("sendspin server disconnected", "from", r.RemoteAddr)
		}
	})

	srv := &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
		MaxHeaderBytes:    8 << 10,
	}

	// Closing the server unblocks Serve and hangs up on whatever is connected, which is what stopping
	// means here: the room is leaving the group, not pausing.
	go func() {
		<-ctx.Done()
		_ = srv.Close()
	}()

	if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

// bind takes the port, waiting while something else still holds it: a listener closing as this one
// starts, or the last run's socket on its way out. Giving up there would leave the Show out of Music
// Assistant until a reboot, so it waits until ctx ends, trying less often as it goes.
func bind(ctx context.Context, addr string) (net.Listener, error) {
	wait := 100 * time.Millisecond
	for told := false; ; {
		ln, err := net.Listen("tcp", addr)
		if err == nil || !errors.Is(err, syscall.EADDRINUSE) {
			return ln, err
		}
		if !told {
			slog.Info("sendspin: the port is still in use, waiting for it", "addr", addr)
			told = true
		}
		select {
		case <-ctx.Done():
			return nil, err
		case <-time.After(wait):
		}
		wait = min(2*wait, 5*time.Second)
	}
}

func (l *listener) allowed(remote string) bool {
	if l.serverIP == nil {
		return false
	}
	host, _, err := net.SplitHostPort(remote)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.Equal(l.serverIP)
}

// take admits one server and turns away the rest.
func (l *listener) take() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.busy {
		return false
	}
	l.busy = true
	return true
}

func (l *listener) give() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.busy = false
}
