package web

import (
	"crypto/tls"
	"net"
	"net/http"
	"testing"
	"time"
)

// A browser that turned http:// into https:// is told about; a plain request is not.
func TestAnHTTPSAskIsHeard(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}),
		ReadHeaderTimeout: 2 * time.Second}
	go srv.Serve(sniffListener{ln})
	defer srv.Close()

	knocks := make(chan struct{}, 4)
	defer HTTPSKnock.Listen(func(struct{}) { knocks <- struct{}{} })()

	resp, err := http.Get("http://" + ln.Addr().String() + "/")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	select {
	case <-knocks:
		t.Fatal("a plain http request was taken for https")
	case <-time.After(100 * time.Millisecond):
	}

	c, err := tls.DialWithDialer(&net.Dialer{Timeout: 2 * time.Second}, "tcp", ln.Addr().String(),
		&tls.Config{InsecureSkipVerify: true})
	if err == nil {
		c.Close()
		t.Fatal("an http port finished a TLS handshake")
	}
	select {
	case <-knocks:
	case <-time.After(2 * time.Second):
		t.Fatal("a TLS hello on the port went unheard")
	}
}
