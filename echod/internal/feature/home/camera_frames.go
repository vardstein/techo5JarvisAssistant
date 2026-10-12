package home

import (
	"context"
	"errors"
	"fmt"
	"image"
	"log/slog"
	neturl "net/url"
	"strings"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// How a Home Assistant camera's pictures reach the screen (GENBU-116). Asking for one snapshot after
// another gave a frame a second at best from another Show: each snapshot of a Show is a full-size
// picture made for the occasion, and this side then spent a quarter of a second unpacking and scaling
// it before asking for the next.
//
// Now there are two ways in, and both hand their pictures to the same pipe:
//   - A camera from ESPHome (another Show, or any ESPHome camera) is read from Home Assistant's MJPEG
//     stream of it, which carries the pictures the device streams anyway: small, and as fast as it
//     sends them.
//   - Any other camera is asked for snapshots, scaled down by Home Assistant to about the panel's size
//     first. Its stream would be no better: for most cameras it is the same snapshots at two a second,
//     full size.
//
// The pipe fetches on one goroutine and unpacks and scales on another, keeping only the newest picture
// in between, so the screen never falls more than a picture behind the camera.

const (
	// streamIdle is how long a stream may go without a picture before it is opened again: Home
	// Assistant's ESPHome camera waits on the device for each picture and never gives up on one.
	streamIdle = 6 * time.Second

	// streamFrameMost bounds one picture in a stream.
	streamFrameMost = 4 << 20

	// frameRetry is the pause after a picture could not be had.
	frameRetry = 2 * time.Second
)

var errStalled = errors.New("the camera stopped sending pictures")

// cameraUp reports whether view gen is the one up.
func (f *Feature) cameraUp(gen uint64) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.cam.gen == gen && time.Now().Before(f.cam.Until)
}

// showFrame puts a picture up in view gen.
func (f *Feature) showFrame(gen uint64, frame *image.RGBA) {
	f.mu.Lock()
	if f.cam.gen == gen {
		// The time on screen counts from the first picture, not from the request: some cameras take a
		// while to start a stream, and a view that closes as it opens is no view at all.
		if f.cam.Frame == nil {
			f.cam.Until = time.Now().Add(f.cam.span)
		}
		f.cam.Frame, f.cam.Error = frame, ""
	}
	f.mu.Unlock()
	f.Changed.Emit(struct{}{})
}

// frameFailed says on screen why view gen has no new picture.
func (f *Feature) frameFailed(gen uint64, entity string, err error) {
	slog.Warn("camera frame", "entity", entity, "err", err)
	f.mu.Lock()
	if f.cam.gen == gen {
		f.cam.Error = err.Error()
	}
	f.mu.Unlock()
	f.Changed.Emit(struct{}{})
}

// fetchFrames shows a Home Assistant or Reolink camera while view gen is up.
func (f *Feature) fetchFrames(gen uint64, entity string) {
	if !isReolink(entity) && f.streamsLive(entity) {
		if f.streamFrames(gen, entity) {
			return
		}
		slog.Info("camera: its stream gave no picture, asking for snapshots", "entity", entity)
	}
	f.pipe(gen, entity, func(ctx context.Context, put func([]byte)) error {
		for ctx.Err() == nil {
			b, err := snapshotBytes(entity)
			if err != nil {
				f.frameFailed(gen, entity, err)
				sleepFor(ctx, frameRetry)
				continue
			}
			put(b)
		}
		return ctx.Err()
	})
}

// streamFrames shows an ESPHome camera from Home Assistant's MJPEG stream of it, opening the stream
// again when it ends or stalls. False when the stream gave no picture at all, so the caller asks for
// snapshots instead.
func (f *Feature) streamFrames(gen uint64, entity string) bool {
	shown := false
	for f.cameraUp(gen) {
		ok, err := f.pipe(gen, entity, func(ctx context.Context, put func([]byte)) error {
			return streamInto(ctx, entity, put)
		})
		shown = shown || ok
		if err == nil {
			break // the view went down
		}
		if !shown {
			slog.Info("camera stream", "entity", entity, "err", err)
			return false
		}
		f.frameFailed(gen, entity, err)
		time.Sleep(frameRetry)
	}
	return true
}

// streamInto puts each picture of entity's stream until the stream ends, stalls or ctx is done. It
// always returns why it stopped.
func streamInto(ctx context.Context, entity string, put func([]byte)) error {
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	body, err := hass.Get().Stream(ctx, "/api/camera_proxy_stream/"+neturl.PathEscape(entity))
	if err != nil {
		return err
	}
	defer body.Close()
	stalled := time.AfterFunc(streamIdle, func() { cancel(errStalled) })
	defer stalled.Stop()
	parts := hass.NewMJPEG(body, streamFrameMost)
	for {
		b, err := parts.Next()
		if err != nil {
			if cause := context.Cause(ctx); cause != nil {
				return cause
			}
			return err
		}
		stalled.Reset(streamIdle)
		put(b)
	}
}

// pipe shows what produce fetches while view gen is up. produce runs on a goroutine of its own and
// hands each picture over with put; this one unpacks and scales them. A picture that arrives while the
// one before is still being scaled takes the place of any waiting, so only the newest is ever next.
// pipe returns once produce gives up, with its error, or with nil once the view is down; shown says
// whether a picture made it to the screen.
func (f *Feature) pipe(gen uint64, entity string, produce func(ctx context.Context, put func([]byte)) error) (shown bool, err error) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	latest := make(chan []byte, 1)
	ended := make(chan error, 1)
	// produce is the only sender, so once the waiting picture is taken out there is room for the next.
	put := func(b []byte) {
		select {
		case <-latest:
		default:
		}
		latest <- b
	}
	go func() { ended <- produce(ctx, put) }()
	check := time.NewTicker(500 * time.Millisecond)
	defer check.Stop()
	for {
		select {
		case b := <-latest:
			frame, err := decodeFrame(b, entity)
			if err != nil {
				f.frameFailed(gen, entity, err)
				continue
			}
			f.showFrame(gen, frame)
			shown = true
		case err := <-ended:
			if err == nil {
				err = errors.New("the camera's pictures ended")
			}
			return shown, err
		case <-check.C:
		}
		if !f.cameraUp(gen) {
			return shown, nil
		}
	}
}

// decodeFrame unpacks a picture and scales it to fit the panel.
func decodeFrame(b []byte, entity string) (*image.RGBA, error) {
	src, err := decodeWithin(b, maxFramePixels, "camera "+entity)
	if err != nil {
		return nil, err
	}
	return fit(src, cameraFrameW, cameraFrameH), nil
}

// snapshotBytes is one picture from a camera. Home Assistant scales a JPEG down before sending it when
// asked for a size, by a step that keeps it at least that big, so a 4K camera's picture comes a
// quarter as wide and unpacks in a sixteenth of the time.
func snapshotBytes(entity string) ([]byte, error) {
	if isReolink(entity) {
		return reolinkSnap(entity)
	}
	return hass.Get().Fetch(fmt.Sprintf("/api/camera_proxy/%s?width=%d&height=%d",
		neturl.PathEscape(entity), cameraFrameW, cameraFrameH))
}

// streamsLive reports whether entity is one of Home Assistant's ESPHome cameras, by the entity registry
// as last read; it reads the registry again when that is stale, so it may take a moment.
func (f *Feature) streamsLive(entity string) bool {
	f.mu.Lock()
	known, at := f.esphomeCameras, f.esphomeCamerasAt
	f.mu.Unlock()
	if time.Since(at) < haCamerasEvery {
		return known[entity]
	}
	ctx, cancel := context.WithTimeout(context.Background(), registryWait)
	defer cancel()
	_, entities, err := hass.Get().Registries(ctx)
	f.mu.Lock()
	defer f.mu.Unlock()
	if err != nil {
		slog.Debug("home: asking Home Assistant which cameras are ESPHome's", "err", err)
		f.esphomeCamerasAt = time.Now().Add(time.Minute - haCamerasEvery) // try again in a minute
		return known[entity]
	}
	f.esphomeCameras, f.esphomeCamerasAt = esphomeCameras(entities), time.Now()
	return f.esphomeCameras[entity]
}

// esphomeCameras is the ESPHome cameras among the registry's entities.
func esphomeCameras(entities []hass.RegistryEntity) map[string]bool {
	set := make(map[string]bool)
	for _, e := range entities {
		if e.Platform == "esphome" && strings.HasPrefix(e.ID, "camera.") {
			set[e.ID] = true
		}
	}
	return set
}

// sleepFor waits d or until ctx is done.
func sleepFor(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
	case <-ctx.Done():
	}
}
