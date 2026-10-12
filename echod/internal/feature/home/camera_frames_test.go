package home

import (
	"bytes"
	"fmt"
	"image"
	"image/jpeg"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// cameraHA is Home Assistant as far as a camera view sees it: a stream and snapshots of each camera.
type cameraHA struct {
	jpg            []byte
	streamOK       bool
	streams, snaps atomic.Int32
	snapW, snapH   atomic.Value
}

func (h *cameraHA) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch {
	case r.URL.Path == "/api/camera_proxy_stream/camera.show":
		h.streams.Add(1)
		if !h.streamOK {
			http.Error(w, "500 Internal Server Error", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=--frameboundary")
		for r.Context().Err() == nil {
			fmt.Fprintf(w, "--frameboundary\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n", len(h.jpg))
			w.Write(h.jpg)
			w.Write([]byte("\r\n"))
			w.(http.Flusher).Flush()
			time.Sleep(20 * time.Millisecond)
		}
	case r.URL.Path == "/api/camera_proxy/camera.show" || r.URL.Path == "/api/camera_proxy/camera.door":
		h.snaps.Add(1)
		h.snapW.Store(r.URL.Query().Get("width"))
		h.snapH.Store(r.URL.Query().Get("height"))
		w.Header().Set("Content-Type", "image/jpeg")
		w.Write(h.jpg)
	default:
		http.NotFound(w, r)
	}
}

func newCameraHA(t *testing.T, streamOK bool) *cameraHA {
	t.Helper()
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, photo(image.Rect(0, 0, 64, 36), image.YCbCrSubsampleRatio420), nil); err != nil {
		t.Fatal(err)
	}
	h := &cameraHA{jpg: buf.Bytes(), streamOK: streamOK}
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	oldPath := hass.Path
	hass.Path = filepath.Join(t.TempDir(), "hass.json")
	t.Cleanup(func() { hass.Path = oldPath })
	if err := hass.Get().Set(srv.URL, "test-token"); err != nil {
		t.Fatal(err)
	}
	return h
}

// viewCamera opens a view of entity, as showCamera does without its list and sound, and waits for the
// first picture. The view is closed again when the test ends, and its fetcher waited for.
func viewCamera(t *testing.T, f *Feature, entity string) *image.RGBA {
	t.Helper()
	f.mu.Lock()
	f.cam = CameraView{Entity: entity, Until: time.Now().Add(time.Minute), span: time.Minute, gen: 1}
	f.mu.Unlock()
	done := make(chan struct{})
	go func() { f.fetchFrames(1, entity); close(done) }()
	t.Cleanup(func() {
		f.mu.Lock()
		f.cam.gen = 2
		f.mu.Unlock()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("the camera's fetcher outlived its view")
		}
	})
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		f.mu.Lock()
		frame, msg := f.cam.Frame, f.cam.Error
		f.mu.Unlock()
		if frame != nil {
			return frame
		}
		if msg != "" {
			t.Logf("camera error on the way: %s", msg)
		}
	}
	t.Fatal("no picture came up")
	return nil
}

func wantPanelShape(t *testing.T, frame *image.RGBA) {
	t.Helper()
	w, h := fitSize(64, 36, cameraFrameW, cameraFrameH)
	if frame.Rect.Dx() != w || frame.Rect.Dy() != h {
		t.Fatalf("frame is %v, want %d×%d", frame.Rect, w, h)
	}
}

func TestESPHomeCameraIsStreamed(t *testing.T) {
	ha := newCameraHA(t, true)
	f := &Feature{esphomeCameras: map[string]bool{"camera.show": true}, esphomeCamerasAt: time.Now()}
	wantPanelShape(t, viewCamera(t, f, "camera.show"))
	// Pictures keep coming on the one stream.
	time.Sleep(200 * time.Millisecond)
	if n := ha.streams.Load(); n != 1 {
		t.Fatalf("%d streams opened, want 1", n)
	}
	if n := ha.snaps.Load(); n != 0 {
		t.Fatalf("%d snapshots asked for beside the stream", n)
	}
}

func TestOtherCameraIsAskedForScaledSnapshots(t *testing.T) {
	ha := newCameraHA(t, true)
	f := &Feature{esphomeCameras: map[string]bool{"camera.show": true}, esphomeCamerasAt: time.Now()}
	wantPanelShape(t, viewCamera(t, f, "camera.door"))
	if n := ha.streams.Load(); n != 0 {
		t.Fatalf("%d streams opened for a camera that is not ESPHome's", n)
	}
	if w, h := ha.snapW.Load(), ha.snapH.Load(); w != fmt.Sprint(cameraFrameW) || h != fmt.Sprint(cameraFrameH) {
		t.Fatalf("snapshot asked for at %v×%v, want the panel's %d×%d", w, h, cameraFrameW, cameraFrameH)
	}
}

func TestStreamThatFailsFallsBackToSnapshots(t *testing.T) {
	ha := newCameraHA(t, false)
	f := &Feature{esphomeCameras: map[string]bool{"camera.show": true}, esphomeCamerasAt: time.Now()}
	wantPanelShape(t, viewCamera(t, f, "camera.show"))
	if ha.streams.Load() != 1 || ha.snaps.Load() == 0 {
		t.Fatalf("streams %d, snapshots %d; want one failed stream, then snapshots", ha.streams.Load(), ha.snaps.Load())
	}
}

func TestESPHomeCamerasAreTheCamerasOfESPHome(t *testing.T) {
	got := esphomeCameras([]hass.RegistryEntity{
		{ID: "camera.show", Platform: "esphome"},
		{ID: "sensor.show_temp", Platform: "esphome"},
		{ID: "camera.door", Platform: "reolink"},
	})
	if len(got) != 1 || !got["camera.show"] {
		t.Fatalf("esphomeCameras = %v", got)
	}
}
