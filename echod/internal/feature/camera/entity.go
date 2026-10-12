package camera

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"
	"github.com/ygelfand/go-esphome-device/api"
	"google.golang.org/protobuf/proto"

	"github.com/HuskerMinion/techo5/echod/internal/hardware/camera"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
)

// The ESPHome camera entity. The library has no camera domain, so this feature describes the
// entity itself (component.Describer puts it ahead of the library's list) and answers the image
// requests: Home Assistant asks for a single picture for the entity's still, and for a stream
// while someone is watching, re-asking every few seconds for as long as they do.

const (
	// objectID is the entity's identifier: camera.<device>_camera in Home Assistant.
	objectID = "camera"

	// chunk is how much JPEG goes in one CameraImageResponse; ESPHome sends 1 KiB pieces.
	chunk = 1024

	// streamFor is how long one stream request keeps frames flowing; Home Assistant renews it
	// well inside that while the stream is open.
	streamFor = 5 * time.Second
)

// key is the entity key, the library's FNV-1 of the object id.
func key(s string) uint32 {
	h := uint32(2166136261)
	for i := 0; i < len(s); i++ {
		h *= 16777619
		h ^= uint32(s[i])
	}
	return h
}

// DescribesEntities marks the feature as a component.Describer.
func (f *Feature) DescribesEntities() {}

// Handle answers the entity list and the image requests.
func (f *Feature) Handle(ctx context.Context, c *esphome.Conn, msg proto.Message) error {
	switch m := msg.(type) {
	case *api.ListEntitiesRequest:
		if !camera.Available() {
			return nil
		}
		return c.Send(&api.ListEntitiesCameraResponse{
			ObjectId: objectID,
			Key:      key(objectID),
			Name:     "Camera",
			Icon:     "mdi:camera",
		})
	case *api.CameraImageRequest:
		if !camera.Available() {
			return nil
		}
		if m.GetStream() {
			f.streamTo(c)
		} else if m.GetSingle() {
			safe.Go("camera entity: still", func() { f.single(c) })
		}
	}
	return nil
}

// send ships one JPEG in chunks, the last marked done.
func send(c *esphome.Conn, jpg []byte) error {
	k := key(objectID)
	for off := 0; off < len(jpg); off += chunk {
		end := off + chunk
		if end > len(jpg) {
			end = len(jpg)
		}
		if err := c.Send(&api.CameraImageResponse{Key: k, Data: jpg[off:end], Done: end == len(jpg)}); err != nil {
			return err
		}
	}
	return nil
}

// single answers one still.
func (f *Feature) single(c *esphome.Conn) {
	ctx, cancel := context.WithTimeout(context.Background(), snapshotWait)
	defer cancel()
	fr, err := camera.Get().Snapshot(ctx)
	if err != nil {
		slog.Warn("camera entity: still", "err", err)
		return
	}
	jpg, err := encodeFull(fr)
	if err != nil {
		return
	}
	if err := send(c, jpg); err != nil {
		slog.Debug("camera entity: send", "err", err)
	}
}

// streams is the connections being streamed to, each with its deadline.
type streamState struct {
	mu    sync.Mutex
	until map[*esphome.Conn]time.Time
}

var streams = streamState{until: map[*esphome.Conn]time.Time{}}

// streamTo starts, or extends, a stream to c.
func (f *Feature) streamTo(c *esphome.Conn) {
	streams.mu.Lock()
	_, running := streams.until[c]
	streams.until[c] = time.Now().Add(streamFor)
	streams.mu.Unlock()
	if running {
		return
	}
	safe.Go("camera entity: stream", func() { f.pump(c) })
}

// pump sends frames to c until its deadline passes or the connection is gone.
func (f *Feature) pump(c *esphome.Conn) {
	// Running out of time is checked and the entry removed in one step: done apart, a renewal landing
	// between the two saw the stream still running, started nothing, and was then wiped, and the
	// picture in Home Assistant froze.
	expired := false
	defer func() {
		if expired {
			return
		}
		streams.mu.Lock()
		delete(streams.until, c)
		streams.mu.Unlock()
	}()
	release, err := camera.Get().Acquire()
	if err != nil {
		slog.Warn("camera entity: stream", "err", err)
		return
	}
	defer release()
	frames := make(chan *camera.Frame, 1)
	var made, skipped atomic.Int64
	cancel := camera.Get().Frames.Listen(func(fr *camera.Frame) {
		made.Add(1)
		select {
		case frames <- fr:
		default:
			skipped.Add(1)
		}
	})
	defer cancel()
	st := pumpStats{from: time.Now()}
	// Every frame the sensor makes goes out (GENBU-116): encoding one in hardware takes well under a
	// millisecond, so what paces the stream is the sensor. Should the link fall behind, frames
	// holds one and the rest are skipped, never queued. The voice on the same link is not held up:
	// a picture goes out in 1 KiB pieces, and audio fits in between them.
	for {
		streams.mu.Lock()
		if time.Now().After(streams.until[c]) {
			delete(streams.until, c)
			expired = true
		}
		streams.mu.Unlock()
		if expired {
			return
		}
		select {
		case fr := <-frames:
			t0 := time.Now()
			jpg, err := encode(fr)
			if err != nil {
				return
			}
			t1 := time.Now()
			if err := send(c, jpg); err != nil {
				return
			}
			st.sent++
			st.encode += t1.Sub(t0)
			st.send += time.Since(t1)
			st.bytes += len(jpg)
		case <-time.After(time.Second):
		}
		if since := time.Since(st.from); since >= statsEvery {
			st.log(since, made.Swap(0), skipped.Swap(0))
		}
	}
}

// statsEvery is how often a running stream says how it is keeping up, the way the wake word does.
const statsEvery = 10 * time.Second

// pumpStats is one stream's account since its last report: what the sensor made, what was sent,
// and where the time went, so a slow stream shows whether the sensor, the encoder or the link is
// holding it back.
type pumpStats struct {
	from         time.Time
	sent, bytes  int
	encode, send time.Duration
}

func (st *pumpStats) log(since time.Duration, made, skipped int64) {
	n := max(st.sent, 1)
	slog.Info("camera stream",
		"made_per_second", float64(made)/since.Seconds(),
		"sent_per_second", float64(st.sent)/since.Seconds(),
		"skipped", skipped,
		"encode_ms", float64(st.encode.Microseconds())/1000/float64(n),
		"send_ms", float64(st.send.Microseconds())/1000/float64(n),
		"kib", st.bytes/1024/n)
	*st = pumpStats{from: time.Now()}
}
