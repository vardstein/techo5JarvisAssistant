package home

import (
	"context"
	"errors"
	"image"
	_ "image/jpeg" // Home Assistant serves camera snapshots as JPEG
	"log/slog"
	"os"
	"strings"
	"time"
	"unicode"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/camera"
	hwspeaker "github.com/HuskerMinion/techo5/echod/internal/hardware/speaker"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/layout"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/triggers"
)

// The cameras page: "show the front door" puts a camera's live view up for a while, a tap takes it
// down. Frames come from Home Assistant through the token, a stream or snapshots depending on the
// camera (camera_frames.go), or from the device's own sensor, and are scaled to the panel here.

const (
	// cameraShow is how long a camera stays up when asked for by voice.
	cameraShow = 30 * time.Second
)

// CameraView is what the screen shows.
type CameraView struct {
	Entity string
	Name   string
	// Doorbell marks a proactive front-door view rather than one the user opened themselves. It is
	// visual metadata only: opening a camera never opens the microphones.
	Doorbell bool
	Until    time.Time
	Frame    *image.RGBA // the latest frame, scaled to fit; nil until the first arrives
	Error    string      // why there is no frame, when there is none

	span time.Duration // how long it was asked for; Until is restarted from the first frame
	gen  uint64        // which view this is: one opened again is a new one
}

// LocalCamera is the entity name of the device's own camera on the list; it is not a Home
// Assistant entity, its frames come from the sensor behind the screen.
const LocalCamera = "local"

// Cameras is the configured list, with the device's own camera first where it has one.
func (f *Feature) Cameras() []config.Camera {
	cams := config.Get().Home.Cameras
	if len(cams) == 0 {
		// No list chosen (the home_cameras action): every camera Home Assistant has, by its own name.
		cams = f.homeAssistantCameras()
	}
	cams = append(cams, config.Get().Home.Reolink.Cameras...)
	if !camera.Available() {
		return cams
	}
	// The device's own camera is also one of Home Assistant's, through its ESPHome entity; it is on the
	// list once, as the local one, which needs no round trip through Home Assistant.
	f.mu.Lock()
	own := f.ownCameras
	f.mu.Unlock()
	return withLocal(cams, own, ownCameraGuess())
}

// withLocal is the list with the device's own camera first, under its local name, and not again under
// the entity Home Assistant has for it: one found in the registry (own), or the id it would have when
// nobody renamed it (guess).
func withLocal(cams []config.Camera, own map[string]bool, guess string) []config.Camera {
	list := []config.Camera{{Entity: LocalCamera, Name: i18n.T(localCameraName)}}
	for _, c := range cams {
		if c.Entity != guess && !own[c.Entity] {
			list = append(list, c)
		}
	}
	return list
}

// ownCameraGuess is the entity id this device's camera has in Home Assistant when nobody renamed it.
func ownCameraGuess() string {
	return "camera." + layout.EntitySlug(config.Get().Device.Name) + "_camera"
}

// ownCameraEntities is which of Home Assistant's cameras are this device's own, by its address; nil when
// Home Assistant cannot say.
func ownCameraEntities() map[string]bool {
	mac := ownMAC()
	if mac == "" {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), registryWait)
	defer cancel()
	devices, entities, err := hass.Get().Registries(ctx)
	if err != nil {
		slog.Debug("home: asking Home Assistant which camera is this device's", "err", err)
		return nil
	}
	own := make(map[string]bool)
	for _, id := range ownEntities(devices, entities, mac, "camera") {
		own[id] = true
	}
	return own
}

// haCamerasEvery is how often Home Assistant's own camera list is looked at again.
const haCamerasEvery = 10 * time.Minute

// homeAssistantCameras is Home Assistant's cameras as last fetched, starting a fetch in the background
// when that is stale; it never waits, since screens ask for it while drawing.
func (f *Feature) homeAssistantCameras() []config.Camera {
	if !hass.Get().Ready() {
		return nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if time.Since(f.haCamerasAt) > haCamerasEvery && !f.haCamerasBusy {
		f.haCamerasBusy = true
		go func() {
			list, err := hass.Get().Entities("camera")
			var own map[string]bool
			if err == nil && camera.Available() {
				own = ownCameraEntities()
			}
			var cams []config.Camera
			for _, e := range list {
				name := e.Name
				if name == "" {
					name = e.ID
				}
				if !validCameraEntity(e.ID) {
					continue
				}
				cams = append(cams, config.Camera{Entity: e.ID, Name: safeCameraName(name)})
			}
			f.mu.Lock()
			f.haCamerasBusy = false
			if err != nil {
				slog.Warn("home: listing Home Assistant's cameras", "err", err)
				f.haCamerasAt = time.Now().Add(time.Minute - haCamerasEvery) // try again in a minute
			} else {
				f.haCameras, f.haCamerasAt = cams, time.Now()
				if own != nil {
					f.ownCameras = own
				}
			}
			f.mu.Unlock()
			if err == nil {
				f.Changed.Emit(struct{}{})
			}
		}()
	}
	return f.haCameras
}

// Camera is the view in progress, if any.
func (f *Feature) Camera() (CameraView, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.cam.Entity == "" || time.Now().After(f.cam.Until) {
		return CameraView{}, false
	}
	return f.cam, true
}

// ShowCamera puts a camera up for d, with its sound if the device's own setting asks for it.
func (f *Feature) ShowCamera(entity string, d time.Duration) {
	f.showCamera(entity, d, CameraSound(), false)
}

// showCamera is ShowCamera with the sound decided by the caller, which is what the action does: an
// automation for a doorbell wants that one camera heard whether or not the device's setting says so.
func (f *Feature) showCamera(entity string, d time.Duration, sound, doorbell bool) {
	name := safeCameraName(entity)
	for _, c := range f.Cameras() {
		if c.Entity == entity {
			name = safeCameraName(c.Name)
		}
	}
	f.mu.Lock()
	fresh := f.cam.Entity != entity || time.Now().After(f.cam.Until)
	gen := f.cam.gen
	if fresh {
		f.camGen++
		gen = f.camGen
	}
	f.cam = CameraView{Entity: entity, Name: name, Doorbell: doorbell, Until: time.Now().Add(d), Frame: f.cam.Frame, span: d, gen: gen}
	if fresh {
		f.cam.Frame = nil
		f.camMuted = false // a fresh view starts audible if its sound was asked for
	}
	f.mu.Unlock()
	slog.Info("camera up", "entity", entity, "for", d, "sound", fresh && sound)
	if fresh {
		if entity == LocalCamera {
			go f.localFrames(gen)
		} else {
			go f.fetchFrames(gen, entity)
		}
		if sound && !isReolink(entity) {
			// The sound is asked of Home Assistant and taken off the speaker when this view ends; a
			// view already up keeps the sound it was started with (camera_sound.go).
			go f.startCameraSound(entity, thisDevice())
		}
	}
	f.Changed.Emit(struct{}{})
}

// ShowDoorbell puts a front-door camera on screen and gives it a local arrival cue. DND/quiet hours
// never suppress the visual notification, but they do suppress both the chime and the camera's own
// audio. A doorbell popup never opens the microphone; HA may separately use Assist Satellite's
// StartConversation when it deliberately wants a reply, which the voice feature also blocks under DND.
func (f *Feature) ShowDoorbell(entity string, d time.Duration, sound bool) {
	audible := doorbellAudible()
	f.showCamera(entity, d, sound && audible, true)
	if audible {
		hwspeaker.Sound().Chime(hwspeaker.ToneDoorbell)
	}
}

// HideCamera takes the view down.
func (f *Feature) HideCamera() {
	f.mu.Lock()
	f.cam.Until = time.Time{}
	f.mu.Unlock()
	f.Changed.Emit(struct{}{})
}

// localFrames shows the device's own camera while view gen is up: every frame the sensor
// produces, scaled to the panel.
func (f *Feature) localFrames(gen uint64) {
	release, err := camera.Get().Acquire()
	if err != nil {
		f.frameFailed(gen, LocalCamera, err)
		return
	}
	defer release()
	size := image.Rect(0, 0, cameraFrameH*camera.Width/camera.Height, cameraFrameH)
	frames := make(chan *camera.Frame, 1)
	cancel := camera.Get().Frames.Listen(func(fr *camera.Frame) {
		select {
		case frames <- fr:
		default:
		}
	})
	defer cancel()
	for f.cameraUp(gen) {
		select {
		case fr := <-frames:
			shown := image.NewRGBA(size)
			scaleInto(shown, fr.Image())
			f.showFrame(gen, shown)
		case <-time.After(500 * time.Millisecond):
			// No frame yet (the sensor is starting) — back round to check the view is still up.
		}
	}
}

// Prewarm asks Home Assistant for one frame from every camera and drops it. Some cameras take
// seconds to start a stream on the first request; asking while the list is on screen means the
// one that gets tapped answers at once.
func (f *Feature) Prewarm() {
	for _, c := range f.Cameras() {
		if c.Entity == LocalCamera || isReolink(c.Entity) {
			continue
		}
		go func(entity string) {
			if _, err := hass.Get().Fetch("/api/camera_proxy/" + entity); err != nil {
				slog.Debug("camera prewarm", "entity", entity, "err", err)
			}
		}(c.Entity)
	}
}

// MatchCamera finds a camera named in what was heard: "show the front door", "show me the deck
// camera", the German "zeig die Haustür". The sentence has to ask for a camera at all (lib/triggers,
// in the screen's language) and then name one; the names are the owner's own, in whatever language
// they wrote them in Home Assistant. Empty when nothing matches.
func (f *Feature) MatchCamera(heard string) string {
	if !triggers.AboutCamera(heard, config.Get().Screen.Language) {
		return ""
	}
	h := strings.ToLower(heard)
	best, bestLen := "", 0
	for _, c := range f.Cameras() {
		n := strings.ToLower(c.Name)
		if n != "" && strings.Contains(h, n) && len(n) > bestLen {
			best, bestLen = c.Entity, len(n)
		}
	}
	return best
}

// Camera actions: the list, and showing one — for an automation that wants the front door up when
// the bell rings. The sound is a second action rather than an argument on the first, because Home
// Assistant registers an action's arguments as a closed, all-required set: no caller can leave one
// out, so adding one to home_show_camera would break every automation already calling it.
func (f *Feature) cameraActions() []*esphome.Action {
	return []*esphome.Action{
		{
			Name: "home_cameras",
			Args: []esphome.Arg{{Name: "cameras", Type: esphome.ArgString}}, // "camera.x=Front door,camera.y=Deck"
			Run: func(c esphome.Call) (any, error) {
				if !cameraAPIEncrypted() {
					return nil, errors.New("home cameras: set an API encryption key first")
				}
				var cams []config.Camera
				for _, item := range strings.Split(c.String("cameras"), ",") {
					entity, name, _ := strings.Cut(strings.TrimSpace(item), "=")
					entity, name = strings.TrimSpace(entity), safeCameraName(name)
					if entity == "" {
						continue
					}
					if !validCameraEntity(entity) {
						return nil, errors.New("home cameras: expected camera.* entities")
					}
					if name == "" {
						name = safeCameraName(strings.TrimPrefix(entity, "camera."))
					}
					cams = append(cams, config.Camera{Entity: entity, Name: name})
				}
				if err := config.Set().Home().Cameras(cams); err != nil {
					return nil, err
				}
				slog.Info("home: cameras set", "count", len(cams))
				f.Changed.Emit(struct{}{})
				return nil, nil
			},
		},
		{
			Name: "home_show_camera",
			Args: []esphome.Arg{{Name: "entity", Type: esphome.ArgString}, {Name: "seconds", Type: esphome.ArgInt}},
			Run: func(c esphome.Call) (any, error) {
				if !cameraAPIEncrypted() {
					return nil, errors.New("show camera: set an API encryption key first")
				}
				entity := strings.TrimSpace(c.String("entity"))
				if !validCameraEntity(entity) {
					return nil, errors.New("show camera: expected a camera.* entity")
				}
				f.ShowCamera(entity, cameraSeconds(c))
				return nil, nil
			},
		},
		{
			Name: "home_show_camera_sound",
			Args: []esphome.Arg{{Name: "entity", Type: esphome.ArgString}, {Name: "seconds", Type: esphome.ArgInt}, {Name: "sound", Type: esphome.ArgString}},
			Run: func(c esphome.Call) (any, error) {
				if !cameraAPIEncrypted() {
					return nil, errors.New("show camera: set an API encryption key first")
				}
				entity := strings.TrimSpace(c.String("entity"))
				if !validCameraEntity(entity) {
					return nil, errors.New("show camera: expected a camera.* entity")
				}
				f.showCamera(entity, cameraSeconds(c), soundAsked(c.String("sound"), CameraSound()), false)
				return nil, nil
			},
		},
		{
			Name: "home_doorbell",
			Args: []esphome.Arg{{Name: "entity", Type: esphome.ArgString}, {Name: "seconds", Type: esphome.ArgInt}, {Name: "sound", Type: esphome.ArgString}},
			Run: func(c esphome.Call) (any, error) {
				if !cameraAPIEncrypted() {
					return nil, errors.New("doorbell: set an API encryption key first")
				}
				entity := strings.TrimSpace(c.String("entity"))
				if !validCameraEntity(entity) {
					return nil, errors.New("doorbell: expected a camera.* entity")
				}
				f.ShowDoorbell(entity, cameraSeconds(c), soundAsked(c.String("sound"), CameraSound()))
				return nil, nil
			},
		},
	}
}

func validCameraEntity(entity string) bool {
	return entity == LocalCamera || strings.HasPrefix(entity, "camera.")
}

const cameraNameMost = 80

func safeCameraName(s string) string {
	s = strings.Map(func(r rune) rune {
		if !unicode.IsPrint(r) {
			return -1
		}
		return r
	}, strings.TrimSpace(s))
	runes := []rune(s)
	if len(runes) > cameraNameMost {
		s = string(runes[:cameraNameMost])
	}
	return strings.TrimSpace(s)
}

func doorbellAudible() bool {
	return !config.Quiet() && !config.Get().Home.DoNotDisturb
}

// cameraAPIEncrypted reports whether the HA↔device API has a real encryption key rather than the
// all-zero adoption key. Camera popups can expose household video/audio on the panel, so HA-triggered
// camera actions fail closed until provisioning has established the encrypted transport.
func cameraAPIEncrypted() bool {
	b, err := os.ReadFile(layout.KeyPath)
	if err != nil {
		return false
	}
	k, err := esphome.ParsePSK(strings.TrimSpace(string(b)))
	return err == nil && !k.IsZero()
}

// cameraSeconds is how long a show-camera action's view lasts: its own seconds, or the default when
// that is zero or less.
func cameraSeconds(c esphome.Call) time.Duration {
	d := time.Duration(c.Int("seconds")) * time.Second
	if d <= 0 {
		d = cameraShow
	}
	return d
}
