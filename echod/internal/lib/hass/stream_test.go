package hass

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// haPart is one picture the way Home Assistant's camera_proxy_stream writes it (camera/__init__.py,
// async_get_still_stream), boundary and all.
func haPart(jpg string) string {
	return fmt.Sprintf("--frameboundary\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n", len(jpg), jpg)
}

func TestMJPEGReadsHomeAssistantsStream(t *testing.T) {
	// The first picture comes twice: Home Assistant sends it again to push it through a browser's buffer.
	// One picture holds a CRLF and a "--" line of its own, which only the length tells apart from the end.
	pics := []string{"first", "first", "two\r\n--frameboundary\r\nthree", strings.Repeat("x", 5000)}
	var s string
	for _, p := range pics {
		s += haPart(p)
	}
	m := NewMJPEG(strings.NewReader(s), 1<<20)
	for i, want := range pics {
		got, err := m.Next()
		if err != nil {
			t.Fatalf("picture %d: %v", i, err)
		}
		if string(got) != want {
			t.Fatalf("picture %d = %.20q, want %.20q", i, got, want)
		}
	}
	if _, err := m.Next(); !errors.Is(err, io.EOF) {
		t.Fatalf("after the last picture: %v, want EOF", err)
	}
}

func TestMJPEGTakesTheStandardBoundaryAndLengthInAnyCase(t *testing.T) {
	s := "\r\n------frameboundary\ncontent-length:  3 \n\nabc\r\n"
	got, err := NewMJPEG(strings.NewReader(s), 10).Next()
	if err != nil || string(got) != "abc" {
		t.Fatalf("Next = %q, %v", got, err)
	}
}

func TestMJPEGRefusesWhatItCannotRead(t *testing.T) {
	for name, s := range map[string]string{
		"no length":       "--b\r\nContent-Type: image/jpeg\r\n\r\nabc\r\n",
		"bad length":      "--b\r\nContent-Length: -4\r\n\r\nabc\r\n",
		"too big":         haPart(strings.Repeat("x", 101)),
		"cut short":       "--b\r\nContent-Length: 10\r\n\r\nabc",
		"no boundary":     "Content-Length: 3\r\n\r\nabc\r\n",
		"long line":       "--b\r\nX-Junk: " + strings.Repeat("y", 2000) + "\r\nContent-Length: 1\r\n\r\na",
		"endless headers": "--b\r\n" + strings.Repeat("X-A: 1\r\n", 40) + "Content-Length: 1\r\n\r\na",
	} {
		t.Run(name, func(t *testing.T) {
			if b, err := NewMJPEG(strings.NewReader(s), 100).Next(); err == nil {
				t.Fatalf("Next = %q, want an error", b)
			} else if errors.Is(err, io.EOF) {
				t.Fatalf("Next = %v, which reads as a clean end", err)
			}
		})
	}
}

func TestStreamSendsTheTokenAndHandsOverTheBody(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer t0k" {
			http.Error(w, "401: Unauthorized", http.StatusUnauthorized)
			return
		}
		if r.URL.Path != "/api/camera_proxy_stream/camera.hall" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=--frameboundary")
		_, _ = io.WriteString(w, haPart("one")+haPart("two"))
	}))
	defer srv.Close()
	c := &Client{acc: access{URL: srv.URL, Token: "t0k"}}
	body, err := c.Stream(context.Background(), "/api/camera_proxy_stream/camera.hall")
	if err != nil {
		t.Fatal(err)
	}
	defer body.Close()
	m := NewMJPEG(body, 100)
	for _, want := range []string{"one", "two"} {
		if got, err := m.Next(); err != nil || !bytes.Equal(got, []byte(want)) {
			t.Fatalf("Next = %q, %v; want %q", got, err, want)
		}
	}

	if _, err := c.Stream(context.Background(), "/api/camera_proxy_stream/camera.none"); err == nil {
		t.Fatal("a 404 opened a stream")
	}
	c.acc.Token = "wrong"
	var se *statusError
	if _, err := c.Stream(context.Background(), "/api/camera_proxy_stream/camera.hall"); !errors.As(err, &se) || se.code != http.StatusUnauthorized {
		t.Fatalf("a wrong token: %v, want a 401", err)
	}
	if _, err := (&Client{}).Stream(context.Background(), "/x"); err == nil {
		t.Fatal("a stream with no access configured")
	}
}
