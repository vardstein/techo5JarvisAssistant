// Package camera serves the Show's own camera over HTTP for physically-authorized diagnostics: a
// JPEG snapshot and an MJPEG stream. Home Assistant uses the encrypted ESPHome camera entity for
// ordinary camera access, so these debug endpoints do not need to be anonymous LAN services.
//
// Both pages are off unless switched on (feature/security), and every read still needs the
// physical-presence setup session. The switch controls exposure; it is not the authorization.
//
// The sensor runs only while a request holds it, and stops a few seconds after the last one, so
// a device nobody is watching has its camera off — and the mute button keeps it off entirely.
package camera

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/jpeg"
	"log/slog"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/web"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/camera"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/jpegenc"
)

func init() {
	component.Register(component.Network, Get, component.Order(70))
	Get().register()
}

const (
	// quality is the JPEG quality for both.
	quality = 85

	// snapshotWait bounds a snapshot request: sensor start plus a frame or two.
	snapshotWait = 8 * time.Second
)

type Feature struct{}

var shared = &Feature{}

func Get() *Feature { return shared }

func (f *Feature) Name() string { return "camera" }

// register puts the camera's pages on the device's web port (feature/web), which is open while any
// of its pages is switched on and shut when none is. A device with no camera registers nothing here
// and still has the port for the pages it does have.
func (f *Feature) register() {
	web.HandlePrivate("/camera.jpg", "Camera", cameraOpen, f.snapshot)
	web.HandlePrivate("/camera.mjpeg", "", cameraOpen, f.stream)
	f.registerScreen()
}

// cameraOpen is the switch and the hardware together, asked at the moment of the request: the device
// nodes are looked for then rather than when the daemon starts, since this runs before the rest of
// the daemon has touched anything.
func cameraOpen() bool { return config.Get().Security.Camera && camera.Available() }

// encode is a frame of the stream. Everyone streaming gets the same frames, so the last one encoded
// is kept and handed to whoever asks for it next.
func encode(f *camera.Frame) ([]byte, error) {
	last.Lock()
	defer last.Unlock()
	if last.f == f {
		return last.jpg, nil
	}
	jpg, err := encodeStream(f)
	if err != nil {
		return nil, err
	}
	last.f, last.jpg = f, jpg
	return jpg, nil
}

var last struct {
	sync.Mutex
	f   *camera.Frame
	jpg []byte
}

// encodeStream hands the encoder the frame painted straight into its buffer where the sensor's
// package can do that (the Show 5), which leaves out the RGBA picture and its conversion: the two
// together were most of the time a frame took. Anywhere else, or should that fail, it is Image.
func encodeStream(f *camera.Frame) ([]byte, error) {
	if n, ok := any(f).(nv12Painter); ok && jpegenc.Available() {
		jpg, err := jpegenc.EncodeNV12(camera.Width, camera.Height, quality, n.NV12)
		if err == nil {
			return jpg, nil
		}
		slog.Debug("camera: hardware encode failed, using software", "err", err)
	}
	return encodeImage(f.Image())
}

type nv12Painter interface {
	NV12(y, uv []byte, stride int)
}

// encodeFull is a still at the sensor's own size.
func encodeFull(f *camera.Frame) ([]byte, error) {
	return encodeImage(f.Full())
}

// encodeImage uses the SoC's encoder where there is one (well under a millisecond for a stream
// frame) and image/jpeg, about 50 ms on the Show, where there is not or it failed.
func encodeImage(img image.Image) ([]byte, error) {
	if rgba, ok := img.(*image.RGBA); ok && jpegenc.Available() {
		if jpg, err := jpegenc.Encode(rgba, quality); err == nil {
			return jpg, nil
		} else {
			slog.Debug("camera: hardware encode failed, using software", "err", err)
		}
	}
	var b bytes.Buffer
	if err := jpeg.Encode(&b, img, &jpeg.Options{Quality: quality}); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
}

// snapshot answers with the next frame.
func (f *Feature) snapshot(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), snapshotWait)
	defer cancel()
	fr, err := camera.Get().Snapshot(ctx)
	if err != nil {
		slog.Warn("camera snapshot", "err", err)
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	b, err := encodeFull(fr)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Length", strconv.Itoa(len(b)))
	w.Write(b)
}

// stream sends frames as they come until the client hangs up.
func (f *Feature) stream(w http.ResponseWriter, r *http.Request) {
	release, err := camera.Get().Acquire()
	if err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	defer release()
	const boundary = "techo5frame"
	frames := make(chan *camera.Frame, 1)
	cancel := camera.Get().Frames.Listen(func(fr *camera.Frame) {
		select {
		case frames <- fr:
		default: // the client is slower than the sensor: skip, never queue
		}
	})
	defer cancel()
	w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary="+boundary)
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	flusher, _ := w.(http.Flusher)
	for {
		select {
		case <-r.Context().Done():
			return
		case fr := <-frames:
			b, err := encode(fr)
			if err != nil {
				return
			}
			if _, err := fmt.Fprintf(w, "--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n", boundary, len(b)); err != nil {
				return
			}
			if _, err := w.Write(b); err != nil {
				return
			}
			if _, err := fmt.Fprint(w, "\r\n"); err != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
	}
}
