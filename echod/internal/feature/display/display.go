//go:build !dot && !spot

// Package display is the Echo Show's screen: what the device shows on it, what a finger on it does,
// and what Home Assistant gets for it.
//
// The screen follows the conversation. Idle, it is a clock; while a turn runs it says what the
// device is doing and shows the words — what was heard, then the answer — and lets them linger a
// while after the turn ends. Everything is drawn by the daemon itself onto the kernel framebuffer
// (hardware/screen): no compositor, no browser, no Android.
//
// A tap does what the Dot's action button does: starts a turn, or ends the one running; on a dark
// screen it only lights it. A swipe down from the top edge opens the settings; other up and down
// swipes do nothing, since they kept getting in the way of the sideways ones. The room's light dims the panel when auto-brightness is on; what Home Assistant
// sets is the ceiling.
//
// To Home Assistant the screen is a switch and a brightness number — on/off and how bright, which is
// what people automate — plus a switch for auto-brightness. Not a light (screen_entities.go).
package display

import (
	"context"
	"errors"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"image"
	"log/slog"
	"math"
	"strings"
	"sync"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/alarm"
	"github.com/HuskerMinion/techo5/echod/internal/feature/announce"
	"github.com/HuskerMinion/techo5/echod/internal/feature/assistant"
	"github.com/HuskerMinion/techo5/echod/internal/feature/btaudio"
	"github.com/HuskerMinion/techo5/echod/internal/feature/dashboard"
	"github.com/HuskerMinion/techo5/echod/internal/feature/hastate"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/feature/media"
	"github.com/HuskerMinion/techo5/echod/internal/feature/mute"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/feature/remind"
	"github.com/HuskerMinion/techo5/echod/internal/feature/ring"
	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/feature/setup"
	"github.com/HuskerMinion/techo5/echod/internal/feature/timer"
	"github.com/HuskerMinion/techo5/echod/internal/feature/voice"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/ambient"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/buttons"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/screen"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/touch"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
	"github.com/HuskerMinion/techo5/echod/internal/lib/safe"
	"github.com/HuskerMinion/techo5/echod/internal/lib/wifi"
	"github.com/HuskerMinion/techo5/echod/internal/service"
)

func init() {
	// A panel that cannot be opened is retried rather than given up on: the device answers without
	// it, and a boot where the node was late should still end with a screen.
	component.Register(component.Device, Get(), component.Order(60),
		component.Supervise(service.Restart(time.Second, 30*time.Second)))
}

// hasEqualizer is whether this screen offers the equalizer turn screen: the Show's does; the Spot's
// round one would want its own.
const hasEqualizer = true

// hasNightSwitch is whether Home Assistant can turn the night on and off here (Night mode).
const hasNightSwitch = true

const (
	// volumeShow is how long the level stays up after it last moved.
	volumeShow = 2 * time.Second

	// idleFrame and activeFrame are how often the screen is redrawn: once a second for a clock, and
	// fast enough for the listening indicator to breathe and the volume to follow a finger.
	idleFrame = time.Second

	// flipFrame is how often the night's flip clock is drawn while a card is flipping.
	flipFrame   = 33 * time.Millisecond
	activeFrame = 150 * time.Millisecond

	// floor is the dimmest an "on" backlight goes; below it the panel reads as off.
	floor = 8

	// Auto-brightness: the fraction of the ceiling the room's light allows, from darkFraction in the
	// dark rising on a log curve to the full ceiling at brightLux. Applied through a running average
	// so a passing shadow does not flicker the panel.
	darkFraction = 0.12
	brightLux    = 400.0
	autoSmooth   = 0.25
)

type Display struct {
	power  *esphome.Switch // the screen on Home Assistant (screen_entities.go)
	bright *esphome.Number
	auto   *esphome.Switch
	clock  *esphome.Select
	// camTime is how long a camera opened from the screen stays up, and answerTime how long a turn's
	// words do once it is over.
	camTime    *esphome.Select
	clockPos   *esphome.Select // Clock position, and dateCol Date color (clock_layout.go)
	dateCol    *esphome.Select
	answerTime *esphome.Select
	menuSize   *esphome.Select // how large the settings screen and the drawer are drawn (menu_size.go)
	turnStyle  *esphome.Select
	// callBtn is the home screen's Call button, on or off (callbutton.go).
	callBtn *esphome.Switch
	// weatherFx is the weather page's sky moving, on or off (weatherfx.go).
	weatherFx *esphome.Switch
	lang      *esphome.Select

	mu      sync.Mutex
	on      bool
	ceiling int // percent Home Assistant asked for
	autoOn  bool
	level   float64 // backlight actually applied, 0..BacklightMax, as a running average
	view    voice.State
	viewAt  time.Time
	volume  int
	volAt   time.Time

	// The dashboard page: asked for, when last touched, whether the last frame drew it, whether the
	// touchscreen was put in follow mode for it, and a finger that started at its left edge.
	dash          bool
	dashHeld      bool // put up by Home Assistant: stays until it is taken down, not dashForget
	dashTouched   time.Time
	dashShowing   bool
	dashFollow    bool
	listFollow    bool     // the touchscreen follows fingers for a list (follow.go)
	list          listDrag // a finger on one
	dashEdge      int      // edgeNone, or the edge the finger on it started at
	dashEdgeAt    image.Point
	dashAwayUntil time.Time // the idle dashboard put away, the clock up until then
	dashPage      string    // "" for the dashboard, dashboard.PageMusic (the library button's page), or the room's dashboard
	dashScroll    int       // how far down the drawn dashboard is scrolled
	dashScrollFor string    // the dashboard it is scrolled on
	dashDrag      drawnDrag // a finger moving on the drawn dashboard
	dashAdjust    dashAdjusting

	poke chan struct{}

	// shots are screenshot requests, answered with a copy of the next frame drawn.
	shots chan chan *image.RGBA
	dev   *screen.Device
	r     *renderer

	// booting is the splash: from the first frame until Home Assistant is listening and at least
	// splashMin has passed, however long that takes. Only a ring or the Wi-Fi page cut it short.
	booting bool
	started time.Time

	// comboAt is when both volume buttons went down for a factory reset, zero while they are not
	// (reset_combo.go); comboLit that the count lit a dark panel, and comboUndo that a count stopped
	// and the next frame should put it back out.
	comboAt             time.Time
	comboLit, comboUndo bool
	logo                *splash
	// online is whether the device had a network address at onlineAt, looked at once a second.
	online   bool
	onlineAt time.Time

	// sheet is the settings sheet being shown; restartArm is the first of the two taps Restart wants.
	sheet      bool
	restartArm time.Time

	// sheetCtl is where the settings screen is: the open category, a list open over it, how far each
	// is scrolled, and the editors.
	sheetCtl

	// drawer is Cameras and Radio, in from the right over the clock: drawerTab is which,
	// drawerScroll how far its list is scrolled, and drawerPick a list of choices open over it.
	drawer       bool
	drawerTab    int
	drawerScroll int
	drawerPick   string

	// guidePage is the starter guide's page while it is up (render_welcome.go).
	guidePage int

	// touchedAt is the last finger on the panel; nightDark is the screen having been put out by the
	// night schedule rather than by anyone.
	touchedAt time.Time
	nightDark bool

	// pausedKey is the song a paused page is up for, and pausedSince when it paused (music_paused.go).
	pausedKey   string
	pausedSince time.Time

	// menuSince is when the drawer or a settings, Wi-Fi or Bluetooth page last came up; menuUp is one
	// being up at the last frame (menu_idle.go).
	menuSince time.Time
	menuUp    bool
	// takeOver is a conversation just started, for the next frame to clear the screen for it
	// (assistantTakesOver).
	takeOver bool

	// nightGlow is the screen turned down to a night light by the night schedule, until a touch or
	// the end of the night brings it back.
	nightGlow bool

	// nightHours and atNight are the night's settings in Home Assistant, glowLevel the night light's
	// brightness.
	nightHours, atNight *esphome.Select
	// nightStart and nightEnd are the night's custom hours, to the quarter hour.
	nightStart, nightEnd *esphome.Select
	// nightStyle is the red night clock's look.
	nightStyle *esphome.Select
	glowLevel  *esphome.Number
	// nightMode is Night mode: Home Assistant turning the night on and off; nightShown what it last said.
	nightMode  *esphome.Switch
	nightShown bool
	nightSeen  bool // nightShown has been sent at least once
	// nightPubMu keeps publishing the Night mode switch to one caller at a time (showNightMode).
	nightPubMu sync.Mutex

	// wide is the Show 8's bigger, brighter panel, which glows harder at the same backlight.
	wide bool

	// slideshowIdleSince is when the screen last became the plain idle page (nothing else showing);
	// zero while it is not. Screensaver mode waits for this to run long enough before taking over.
	slideshowIdleSince time.Time

	// wifi is the Wi-Fi pages' state; wifiOpen shows them. wifiAt is when the status was last read.
	wifi     wifiState
	wifiOpen bool
	wifiAt   time.Time

	// btFromSheet is the Bluetooth page opened from the settings: its Done goes back there. btScroll
	// is how far its list is scrolled.
	btFromSheet bool
	btScroll    int

	// quiet is a turn that was a screen command ("go home", "show the deck"): its words and reply
	// are not shown, so the screen moves at once. radioCue is when Home Assistant last named a
	// station as playing, which comes seconds before the stream does.
	quiet    bool
	radioCue time.Time

	// weatherArmed is a weather question in progress; weatherUntil is how long the forecast page
	// stays once the turn is over.
	weatherArmed bool
	weatherUntil time.Time

	// The calendar page (render_calendar.go): up until calUntil, on calMonth, as calDay's list when
	// that is set, with calDetail's window open over it when that is.
	calUntil  time.Time
	calMonth  time.Time
	calDay    time.Time
	calDetail *hass.Event
	calScroll int // the day's list, scrolled this many rows

	// popup is an event popped up on the screen (calendar_popup.go), until popupUntil or a tap;
	// popupNext is when the calendar is next looked at (what was shown is kept in the config).
	popup      *hass.Event
	popupUntil time.Time
	popupNext  time.Time
	popupQueue []hass.Event  // due while another was up: shown next
	pop        popupEntities // the pop-ups' settings in Home Assistant

	// The alert page (alerts.go): up until alertUntil, on alertIdx of the alerts at home, scrolled
	// alertScroll lines.
	alertUntil  time.Time
	alertIdx    int
	alertScroll int
	// radar is the rain map in place of the forecast, while the weather page is up.
	radar bool

	// away is the now-playing page put away by a swipe, and awayTrack/awayStation are what was playing
	// when it went: the next track brings it back, so a dismissal costs nothing and never strands the
	// buttons. awayPlaying is whether that track was playing at the last look, which is how a start
	// after a stop is told apart from the track simply going on.
	away        bool
	awayTrack   string
	awayStation string
	awayPlaying bool

	// strip is the music strip's setting in Home Assistant; stripKey/stripSince are the track the strip
	// is timing and when it started, stripFullUntil a tap on the strip's song bringing the full page
	// back, and showingStrip whether the last frame had it, for the touch handler.
	strip          *esphome.Select
	stripKey       string
	stripSince     time.Time
	stripFullUntil time.Time
	showingStrip   bool

	// themeSel is the theme in Home Assistant, and themeShown what it was last set to.
	themeSel   *esphome.Select
	themeShown string

	// callShown is the Call button on the clock as last drawn, and callees who the drawer's Call tab
	// listed: a tap acts on what was on the screen.
	callShown bool
	callees   []phone.Callee

	// favShown is the song or station the page last showed and whether its star was filled: a tap
	// on the star acts on what was on the screen. favPress is the last press, which the star keeps to
	// until the music's own lists have caught up or the song has changed.
	favShown     string
	favShownOn   bool
	favPress     favPress
	favNote      string
	favNoteUntil time.Time
	// pressed is the music button last pressed, lit until pressedUntil.
	pressed      image.Rectangle
	pressedUntil time.Time

	// showingPlaying and showingWord are what the last painted screen was: whether the now-playing
	// page was on it, and whether the footer was drawing the word for the music. The touch handler acts
	// on what is on the screen, rather than working the same thing out a second way and drifting from
	// it — which is how a tap out of an empty corner came to send a play/pause.
	showingPlaying bool
	showingWord    bool

	// lyricsOn is the now-playing page showing the song's words, from its Lyrics button, and
	// showingLyrics whether the last frame had that button on it.
	lyricsOn      bool
	showingLyrics bool

	// showingLibrary is whether the last frame had the now-playing page's library button on it.
	showingLibrary bool

	// ringPreview shows the ringing page silently until then.
	ringPreview time.Time

	// demoUntil puts placeholders where the sheet shows the owner's details (name, network,
	// address, SSH key names), for screenshots that are going to be published.
	demoUntil time.Time
}

var (
	once   sync.Once
	shared *Display
)

func Get() *Display {
	once.Do(func() { shared = build() })
	return shared
}

func build() *Display {
	d := &Display{

		auto: &esphome.Switch{
			Base: esphome.Base{
				ObjectID: "screen_auto_brightness",
				Name:     "Screen auto-brightness",
				Icon:     "mdi:brightness-auto",
				Category: esphome.CategoryConfig,
			},
		},
		poke:  make(chan struct{}, 1),
		shots: make(chan chan *image.RGBA, 4),
		view:  voice.State{Phase: "idle"},
	}
	d.power, d.bright = screenEntities()
	d.power.OnCommand = func(on bool) { d.apply(on, d.screenLevel(), true) }
	d.bright.OnCommand = func(v float32) { d.apply(d.screenOn(), int(math.Round(float64(v))), true) }
	d.auto.OnCommand = func(on bool) { d.setAuto(on, true) }
	d.clock = clockSelect(d.wake)
	d.camTime = cameraTimeSelect()
	d.answerTime = answerTimeSelect()
	d.menuSize = menuSizeSelect(d.wake)
	d.clockPos, d.dateCol = clockLayoutSelects(d.wake)
	d.turnStyle = turnStyleSelect(d.wake)
	d.callBtn = callButtonSwitch(d.wake)
	d.weatherFx = weatherAnimationSwitch(d.wake)
	d.strip = stripSelect(d.wake)
	d.themeSel = themeSelect(d.wake)
	d.nightHours = nightHoursSelect(d)
	d.nightStart, d.nightEnd = nightEndSelect(d, true), nightEndSelect(d, false)
	d.nightStyle = nightStyleSelect(d)
	d.buildPopupEntities()
	d.atNight = atNightSelect(d)
	d.nightMode = nightModeSwitch(d)
	d.glowLevel = glowNumber(d)
	d.lang = langSelect()
	voice.Changed.Listen(d.changed)
	media.Get().OnVolume.Listen(d.volumeMoved)
	buttons.Get().Events.Listen(d.resetCombo)
	ambient.Get().Lux.Listen(d.lux)
	touch.Get().Gestures.Listen(d.gesture)
	// A device with no address a while after boot gets the Wi-Fi page without being asked: a
	// fresh unit, or one carried to another house. The page ends the splash (frame), which would
	// otherwise wait for Home Assistant for ever on a device that cannot reach it.
	go func() {
		time.Sleep(noAddressWait)
		if wifi.Available() && wifi.Current(context.Background()).Address == "" {
			d.mu.Lock()
			open := d.wifiOpen
			d.mu.Unlock()
			if !open {
				slog.Info("wifi: no address after boot, opening setup")
				d.openWifi()
			}
		}
	}()
	hastate.Get().Changed.Listen(func(u hastate.Update) {
		// The first value is the station that played last, sent when Home Assistant connects after
		// a start; only a change means a station is starting.
		if u.First || u.Attribute != "" || u.Entity == "" || u.Entity != config.Get().Home.Radio.Now {
			return
		}
		if u.Value == "" || u.Value == "unknown" || u.Value == "unavailable" {
			return
		}
		d.mu.Lock()
		d.radioCue = time.Now()
		d.mu.Unlock()
		d.wake()
	})
	btaudio.Get().Changed.Listen(func(btaudio.State) { d.wake() })
	phone.Get().Changed.Listen(func(phone.State) { d.wake() })
	security.Get().Changed.Listen(func(struct{}) { d.wake() })
	alarm.Get().Changed.Listen(func(struct{}) { d.wake() })
	remind.Get().Changed.Listen(func(struct{}) { d.wake() })
	timer.Get().Changed.Listen(func(struct{}) { d.wake() })
	onMissed(d.wake)
	home.Get().Changed.Listen(func(struct{}) { d.wake() })
	dashboard.Get().Changed.Listen(func(struct{}) { d.wake() })
	dashboard.Get().Asked.Listen(d.dashboardAsked)
	dashboard.Get().RecipeAsked.Listen(func(struct{}) { d.openRecipe() })
	assistant.SetScreen(d.showPage)
	return d
}

func (d *Display) Name() string { return "screen" }

// turnShown is whether the turn's picture is the page: nothing drawn before it in render.draw has the
// screen. Only then does it need its fast frames.
func turnShown(s scene) bool {
	return s.eq != nil && s.call.Phase == phone.Idle && !s.ring.any() && !s.setupAsking && !s.bt.Pairing &&
		!s.showWifi && !s.showSheet && !s.showCamera && !s.showAlert && !s.showCalendar && !s.showRadar &&
		!s.showWeather && !s.showDash
}

// turnStyleSel is the Turn screen setting in Home Assistant.
func (d *Display) turnStyleSel() *esphome.Select { return d.turnStyle }

func (d *Display) Entities() []esphome.Entity {
	return []esphome.Entity{d.power, d.bright, d.auto, d.clock, d.clockPos, d.dateCol, d.camTime, d.answerTime, d.menuSize, d.turnStyle, d.callBtn, d.weatherFx, d.lang, d.strip, d.themeSel, d.nightHours, d.nightStart, d.nightEnd, d.nightMode, d.atNight, d.nightStyle, d.glowLevel,
		d.pop.on, d.pop.lead, d.pop.chime, d.pop.allDay}
}

// Restore lights the panel the way it was left. Before the framebuffer is opened: the backlight is
// its own device.
func (d *Display) Restore(c config.Config) {
	setClock24(d.clock, c.Screen.Clock24)
	d.camTime.Set(cameraTimes[cameraTimeIndex()].label)
	d.answerTime.Set(answerTimes[answerTimeIndex()].label)
	useMenuSize(menuSizeIndex())
	d.menuSize.Set(menuSizes[menuSizeIndex()].label)
	d.clockPos.Set(clockPositions[clockPositionIndex()].label)
	d.dateCol.Set(dateColors[dateColorIndex()].label)
	d.turnStyle.Set(turnStyles[turnStyleIndex()].label)
	setCallButton(d.callBtn, c.Screen.CallButton)
	setWeatherAnimation(d.weatherFx, !c.Screen.WeatherStill)
	d.strip.Set(stripOptions[stripIndex()])
	d.nightHoursChanged()
	d.atNight.Set(atNightOptions[atNightIndex()])
	d.nightStyle.Set(nightStyleOptions[nightStyleIndex()])
	d.popupSettingsChanged()
	d.glowLevel.Set(float32(d.glowSetting()))
	d.setAuto(c.Screen.Auto, false)
	d.apply(c.Screen.On, c.Screen.Brightness, false)
}

// apply sets the screen: the ceiling, and whether the panel is lit at all.
func (d *Display) apply(on bool, pct int, save bool) {
	pct = min(max(pct, 0), 100)
	d.mu.Lock()
	d.on, d.ceiling = on, pct
	// Somebody chose this, so the night no longer owns the screen: without this, a screen switched off
	// during the night was switched back on when the night ended.
	if save {
		d.nightDark = false
	}
	d.mu.Unlock()
	d.relight(true)

	d.power.Set(on)
	d.bright.Set(float32(pct))
	d.wake()

	if save {
		if err := config.Set().Screen().On(on); err != nil {
			slog.Error("saving the screen state failed", "err", err)
		}
		if err := config.Set().Screen().Brightness(pct); err != nil {
			slog.Error("saving the screen brightness failed", "err", err)
		}
	}
	slog.Info("screen", "on", on, "brightness", pct)
}

func (d *Display) setAuto(on bool, save bool) {
	d.mu.Lock()
	d.autoOn = on
	d.mu.Unlock()
	d.auto.Set(on)
	d.relight(true)
	if save {
		if err := config.Set().Screen().Auto(on); err != nil {
			slog.Error("saving the auto-brightness setting failed", "err", err)
		}
		slog.Info("screen auto-brightness", "on", on)
	}
}

// relight works out the backlight from the ceiling, the room and whether the panel is on, and
// applies it. jump skips the smoothing, for a change the user just asked for.
func (d *Display) relight(jump bool) {
	d.mu.Lock()
	target := 0.0
	if d.on {
		target = float64(d.ceiling) * screen.BacklightMax / 100
		if d.autoOn {
			if lux, _, ok := ambient.Get().Current(); ok {
				target *= allowed(lux)
			}
		}
		target = math.Max(target, floor)
		if d.nightGlow {
			target = float64(d.glowBacklight()) // relight holds mu
		} else if nightNow(time.Now()) && (phone.Get().Busy() || ring.IsSounding()) {
			// A call or a ringing alarm/timer at night must be visible, but need not blast the room at
			// the panel's daytime ceiling. Sunrise alarms take over below with their own gradual level.
			target = math.Min(target, screen.BacklightMax/2)
		}
	}
	// The light before an alarm takes the backlight over while it runs: it starts under anything the
	// room would otherwise ask for and ends at the screen's own brightness.
	if p := sunriseProgress(time.Now()); p > 0 && d.on {
		target = float64(d.ceiling) * screen.BacklightMax / 100 * sunriseLevel(p)
	}
	if jump || d.level == 0 {
		d.level = target
	} else {
		d.level += (target - d.level) * autoSmooth
	}
	level := int(math.Round(d.level))
	glowing := d.nightGlow
	d.mu.Unlock()
	// A long press is the way up from the night light (gesture), and this screen reports no holds
	// otherwise. Every change to the night light comes through here.
	touch.Get().SetHolds(glowing)

	if err := screen.SetBacklight(level); err != nil {
		slog.Warn("setting the backlight failed", "err", err)
	}
}

// allowed is the fraction of the ceiling a room this bright gets.
func allowed(lux float64) float64 {
	f := darkFraction + (1-darkFraction)*math.Log10(1+math.Max(lux, 0))/math.Log10(1+brightLux)
	return math.Min(math.Max(f, darkFraction), 1)
}

// lux is a reading from the room. On the sensor's goroutine, twice a second.
func (d *Display) lux(float64) {
	d.mu.Lock()
	auto, on := d.autoOn, d.on
	d.mu.Unlock()
	if auto && on {
		d.relight(false)
	}
}

// changed is the conversation moving on. It runs on the conversation's goroutine, so it only
// records and wakes the loop.
func (d *Display) changed(s voice.State) {
	d.mu.Lock()
	newHeard := s.Heard != "" && s.Heard != d.view.Heard
	if s.Phase == "listening" && d.view.Phase != "listening" {
		d.quiet = false
		// A new conversation takes the screen from whatever is on it; a follow-up is the same one
		// going on, and leaves up what its last answer put there.
		d.takeOver = !s.FollowUp
	}
	if s.Phase != "listening" {
		// Only while it is still listening: a frame that comes later, once the question has been heard,
		// would take down the camera or the page the answer is putting up.
		d.takeOver = false
	}
	d.view = s
	d.viewAt = time.Now()
	// A question about the weather brings the forecast page up once the answer is done, for a
	// while, and then the screen goes back to whatever it was showing.
	// Not when the device answers directly: its assistant knows where a question was about and puts
	// the page up itself (showPage), and a forecast for another town is not this one's.
	if newHeard && aboutWeather(s.Heard) && !config.Get().Brain.Direct() {
		d.weatherArmed = true
		d.radar = aboutRadar(s.Heard)
	}
	// "Show the front door": the camera goes up at once, while the assistant answers. Once per
	// sentence: the state repeats the transcript on every phase change.
	if newHeard {
		if entity := home.Get().MatchCamera(s.Heard); entity != "" {
			d.quiet = true
			go home.Get().ShowCamera(entity, cameraVoiceShow)
		}
		// "Go home": whatever page is up comes down, back to the clock, and music stops rather than
		// holding the now-playing page.
		if aboutGoingHome(s.Heard) {
			d.weatherArmed, d.weatherUntil = false, time.Time{}
			d.calUntil, d.calDetail = time.Time{}, nil
			d.closeAlert()
			d.sheet, d.quiet = false, true
			d.dash = false
			go home.Get().HideCamera()
			go d.endMusic()
			slog.Info("screen: home by voice")
		}
	}
	if s.Phase == "idle" && d.weatherArmed {
		d.weatherArmed = false
		d.weatherUntil = time.Now().Add(weatherShow)
	}
	d.mu.Unlock()
	d.wake()
}

// showPage is the voice assistant putting a page up (feature/assistant): the forecast or the rain map
// once the answer has been said, as a question about the weather does, or the calendar now.
func (d *Display) showPage(page string) bool {
	switch page {
	case "weather", "radar":
		d.mu.Lock()
		d.weatherArmed, d.radar = true, page == "radar"
		d.mu.Unlock()
		d.wake()
		return true
	case "calendar":
		return d.OpenCalendar()
	}
	return false
}

// volumeMoved is the level changing on purpose; the screen shows it for a moment.
func (d *Display) volumeMoved(step int) {
	d.mu.Lock()
	d.volume, d.volAt = step, time.Now()
	d.mu.Unlock()
	d.wake()
}

// gesture is a finger on the panel. A dark screen only lights up; otherwise a tap is the action
// button.
func (d *Display) gesture(g touch.Gesture) {
	d.mu.Lock()
	on := d.on
	d.mu.Unlock()
	if g.Kind != touch.Drag {
		// A moving finger reports dozens of times a second; where it came down and lifted is enough.
		slog.Info("touch", "gesture", g.String())
	}
	d.mu.Lock()
	d.touchedAt = time.Now()
	d.mu.Unlock()

	if !on {
		if g.Kind == touch.Tap && d.ringing(time.Now()).any() {
			// A ring on a panel the night left dark: the tap stops it, and the panel stays dark.
			d.stopRing()
			return
		}
		if g.Kind == touch.Tap {
			d.apply(true, d.ceilingOrDefault(), true)
		}
		return
	}
	// A night light stays a night light when it is touched: bright light is the last thing somebody
	// asleep or just waking wants, and a hand in the dark is more likely a knock than a request. A long
	// press is the way to the full screen, as the first touch used to be; nothing else on a night
	// light does anything, since nobody can see what they are pressing on a screen this dim.
	d.mu.Lock()
	glowing := d.nightGlow && !d.wifiOpen && !setup.Get().Waiting()
	d.mu.Unlock()
	if glowing {
		if g.Kind == touch.Tap && d.ringing(time.Now()).any() {
			// An alarm or a timer rings over the night clock; a tap anywhere stops it, as the ringing
			// page's own button would.
			d.stopRing()
			d.wake()
			return
		}
		if g.Kind == touch.Hold {
			d.mu.Lock()
			d.nightGlow = false
			d.mu.Unlock()
			slog.Info("screen: night light lifted by a long press")
			d.relight(true)
			d.wake()
		}
		return
	}
	// A call: its page takes every tap.
	if st := phone.Get().State(); st.Phase != phone.Idle {
		if g.Kind == touch.Tap {
			// Unless something is ringing behind it. The call keeps its buttons; the rest of the
			// page stops the ring. Taps outside the buttons did nothing on this page before, so
			// answering and hanging up are untouched — and a timer that finishes mid-call used to
			// have no way out at all, since this page took every tap and the stop word was off.
			if rs := d.ringing(time.Now()); rs.any() && d.r != nil && !d.r.actionDecided(g.Y) {
				d.stopRing()
			} else {
				d.callTap(g.X, g.Y, st)
			}
		}
		d.wake()
		return
	}

	// A timer or an alarm ringing: its page takes every tap.
	if st := d.ringing(time.Now()); st.any() {
		if g.Kind == touch.Tap {
			d.ringTap(g.X, g.Y, st)
		}
		d.wake()
		return
	}

	// A browser asking to be let in: its page takes every tap, and only the answers decide.
	if setup.Get().Waiting() {
		if g.Kind == touch.Tap {
			if d.r != nil {
				if allow, answered := d.r.askTap(g.X, g.Y); answered {
					setup.Get().Answer(allow)
				}
			}
		}
		d.wake()
		return
	}

	// A voice turn said in the strip over a page: the strip takes the tap, so a row hidden under it is
	// not pressed while somebody is answering. It is drawn over a modal too, so it goes first.
	if g.Kind == touch.Tap && d.r != nil && d.r.turnStripTapped(image.Pt(g.X, g.Y)) {
		d.wake()
		return
	}

	// A modal up (render_modal.go): the page under it is at a tenth of its light, so a finger anywhere
	// is for the modal, and nothing reaches the page until it is gone. The microphone open for an
	// announcement ends with a tap sending what was said or a hold throwing it away; the rest are put
	// away with a tap, a reminder on every device it went off on.
	if d.r != nil {
		if kind := d.r.modalUp(); kind != modalNone {
			switch {
			case kind == modalRecording && g.Kind == touch.Tap:
				go announce.Get().Finish()
			case kind == modalRecording && g.Kind == touch.Hold:
				go announce.Get().Cancel()
			case kind == modalAnnouncement && g.Kind == touch.Tap:
				go announce.Get().Dismiss()
			case kind == modalReminder && g.Kind == touch.Tap:
				go remind.Get().Stop()
			case kind == modalPopup && g.Kind == touch.Tap:
				d.dismissPopup()
			}
			d.wake()
			return
		}
	}

	// The starter guide: a tap is the next page, and Skip or a tap on the last page puts it away for
	// good. Nothing else takes a finger while it is up.
	if !config.Get().Screen.Welcomed {
		if g.Kind == touch.Tap && d.r != nil {
			d.mu.Lock()
			d.guidePage++
			done := d.guidePage >= len(guidePages()) || image.Pt(g.X, g.Y).In(d.r.guideSkip())
			if done {
				d.guidePage = 0 // a reset that brings the guide back starts it from the top
			}
			d.mu.Unlock()
			if done {
				if err := config.Set().Screen().Welcomed(true); err != nil {
					slog.Warn("saving the welcome failed", "err", err)
				}
				slog.Info("starter guide put away")
			}
			d.wake()
		}
		return
	}

	// A finger moving a list: the list goes with it, and what it turns out to be, a tap or a swipe, comes
	// back through here as one (follow.go).
	if (g.Kind == touch.Hold || g.Kind == touch.Drag || g.Kind == touch.Release) && d.listFinger(g) {
		return
	}

	// The Bluetooth page: a tap on a row pairs and connects it, or disconnects the one connected, and
	// the back arrow ends the page, back to the settings when it was opened from there.
	bt := btaudio.Get()
	if bt.Pairing() {
		if d.r == nil {
			return
		}
		if d.drawerSwipe(g) {
			bt.SetPairing(false)
			d.mu.Lock()
			d.btFromSheet, d.btScroll = false, 0
			d.mu.Unlock()
			d.openDrawerOver()
			return
		}
		done := false
		switch g.Kind {
		case touch.Tap:
			z, ok := d.r.zoneAt(g.X, g.Y)
			switch {
			case !ok:
			case z.kind == zoneBack:
				done = true
			case z.kind == zoneRow:
				for _, dev := range bt.State().Devices {
					switch {
					case dev.Address != z.id || dev.Busy:
					case dev.Connected:
						safe.Go("bluetooth disconnect from the screen", bt.Disconnect)
					default:
						bt.Choose(dev.Address)
					}
				}
			}
		case touch.SwipeUp, touch.SwipeDown:
			cardMax, _ := d.r.scrollLimits()
			by := notchPx
			if g.Kind == touch.SwipeDown {
				by = -notchPx
			}
			d.mu.Lock()
			d.btScroll = min(max(d.btScroll+by, 0), cardMax)
			d.mu.Unlock()
		case touch.SwipeRight:
			done = true
		}
		if done {
			bt.SetPairing(false)
			d.mu.Lock()
			back := d.btFromSheet
			d.btFromSheet, d.btScroll = false, 0
			d.mu.Unlock()
			if back {
				d.showSheet(true)
			}
		}
		d.wake()
		return
	}

	// The dashboard: every finger is its, including the one that takes it away.
	d.mu.Lock()
	dashUp := d.dashShowing
	d.mu.Unlock()
	if dashUp {
		// The streamed dashboard owns the panel, except for the native music strip visibly laid over
		// its foot. Consume gestures in the strip before forwarding anything to the browser; outside
		// it, the dashboard remains completely in control.
		if d.musicStripGesture(g) {
			d.wake()
			return
		}
		d.dashGesture(g)
		d.wake()
		return
	}

	// A live camera: a tap takes it down, unless it lands on the sound's control — that silences what
	// the camera is saying and leaves the view up, which is the whole use of it at a doorbell.
	if _, up := home.Get().Camera(); up {
		if g.Kind == touch.Tap {
			if d.r != nil && d.r.cameraSoundTapped(image.Pt(g.X, g.Y)) {
				// Silence it, or ask for it again: the control is a toggle, and the view stays either way.
				home.Get().ToggleCameraSound()
			} else {
				home.Get().HideCamera()
			}
		}
		d.wake()
		return
	}

	// The Wi-Fi pages take every tap while they are up.
	d.mu.Lock()
	wifiOpen := d.wifiOpen
	d.mu.Unlock()
	if wifiOpen {
		if d.drawerSwipe(g) {
			d.closeWifi()
			d.showSheet(false)
			d.openDrawerOver()
			return
		}
		if d.r != nil {
			d.wifiGesture(g)
		}
		d.wake()
		return
	}

	// The drawer, Cameras and Radio: while it is in, every finger is its.
	d.mu.Lock()
	drawerIn := d.drawer
	d.mu.Unlock()
	if drawerIn {
		d.drawerGesture(g)
		d.wake()
		return
	}

	// The settings screen: taps land on what it drew, vertical swipes scroll it.
	d.mu.Lock()
	sheet := d.sheet
	d.mu.Unlock()
	if sheet {
		// Vertical swipes do nothing here: the swipe that opened the sheet keeps reporting notches
		// until the finger lifts, and those must not turn into volume steps. The Volume row has
		// buttons instead.
		switch {
		case d.r == nil:
		case g.Kind == touch.Tap:
			d.mu.Lock()
			d.openedBy = image.Point{-1, -1}
			d.mu.Unlock()
			d.nextTap(g.X, g.Y)
		case g.Kind == touch.SwipeUp || g.Kind == touch.SwipeDown:
			d.sheetSwipe(g)
		case g.Kind == touch.SwipeRight:
			d.sheetBack() // the way the Car Thing went back: a swipe to the right, anywhere
		case d.drawerSwipe(g):
			d.showSheet(false)
			d.openDrawerOver()
		}
		d.wake()
		return
	}

	// The alert page takes its own taps and swipes while it is up; it is drawn over the calendar, so it
	// is asked first.
	if d.alertUp() {
		d.alertGesture(g)
		d.wake()
		return
	}
	if d.calendarUp() {
		d.calendarGesture(g)
		d.wake()
		return
	}
	switch g.Kind {
	case touch.Tap:
		d.mu.Lock()
		weatherUp := time.Now().Before(d.weatherUntil)
		idle := d.view.Phase == "idle"
		d.mu.Unlock()
		// A finished turn's words: a tap puts them away. It used to start another turn, which is not
		// what a hand put on an answer to clear it means.
		if !weatherUp && d.answerUp(time.Now()) {
			d.clearAnswer()
			return
		}
		// The alert badge on the clock opens the alert, and a pill over the rain map opens its alert.
		// Both sit in the top band too.
		if idle && !weatherUp && d.r != nil && d.r.badgeTapped(image.Pt(g.X, g.Y)) && d.OpenAlert(0) {
			return
		}
		d.mu.Lock()
		onRadar := d.radar
		d.mu.Unlock()
		if weatherUp && onRadar && d.r != nil {
			if i := d.r.pillTapped(image.Pt(g.X, g.Y)); i >= 0 && d.OpenAlert(i) {
				return
			}
		}
		// The weather on the home screen opens the forecast, the page a weather question brings up. It
		// sits in the top band, so it is looked for before that band's rule below.
		if idle && !weatherUp && d.r != nil && d.r.weatherTapped(image.Pt(g.X, g.Y)) {
			slog.Info("screen: forecast by touch")
			d.ShowWeather(false)
			return
		}
		// The date under the clock opens the calendar, once this device shows one.
		if idle && !weatherUp && d.r != nil && d.r.dateTapped(image.Pt(g.X, g.Y)) && d.OpenCalendar() {
			slog.Info("screen: calendar by touch")
			return
		}
		// A short swipe from the top edge that never made a notch arrives as a tap; it must not
		// start a turn. The top band is the sheet's, taps there do nothing.
		if g.Y < topEdge {
			return
		}
		if weatherUp {
			// The weather page: its button turns between the forecast and the rain map, and keeps the
			// page up a while longer; a tap anywhere else puts it away.
			d.mu.Lock()
			if d.r != nil && g.X >= 0 && image.Pt(g.X, g.Y).In(d.r.weatherButton()) {
				d.radar = !d.radar
				d.weatherUntil = time.Now().Add(weatherShow)
			} else {
				d.weatherUntil, d.radar = time.Time{}, false
			}
			d.mu.Unlock()
			d.wake()
			return
		}
		// The footer's word for the music is the way back to the page: it shows what is playing, so a
		// tap made to look at the music never leaves you looking at the clock, and it answers whether
		// the track is playing or paused. While it was paused only, a page swiped away during a song had
		// no way back at all. It brings the page back and does nothing else: playing or pausing is the
		// page's own button, and a tap made to look at the song should not stop it.
		d.mu.Lock()
		showing, away, inStrip, word := d.showingPlaying, d.away, d.showingStrip, d.showingWord
		d.mu.Unlock()
		// Only while the word is really drawn, and only when the page is really away: this is the way back
		// to it, so taking a tap out of the bottom-right corner of the clock is worth it only when there
		// is both something to come back to and something drawn there. Nothing playing leaves the corner
		// free, which is where a tap meant for a voice turn lands. Not with the strip up, either: the
		// word is not drawn then, and its corner is the strip's.
		if d.r != nil && idle && word && !showing && away && !inStrip &&
			image.Pt(g.X, g.Y).In(d.r.playingButton()) {
			d.mu.Lock()
			d.away, d.awayTrack, d.awayStation, d.awayPlaying = false, "", "", false
			d.mu.Unlock()
			d.wake()
			return
		}
		// The Call button, when it is on the clock: the list of devices and contacts to call.
		d.mu.Lock()
		callShown := d.callShown
		d.mu.Unlock()
		if idle && callShown && d.r != nil && image.Pt(g.X, g.Y).In(d.r.callButtonRect().Inset(-d.r.s(12))) {
			d.openDrawer(drawerCall)
			return
		}
		if idle && d.musicStripGesture(g) {
			return
		}
		if idle && showing {
			// The now-playing screen: each button does what it says, and a tap anywhere else is
			// play/pause, because that is what a hand put on a screen like this means.
			if d.r != nil {
				fav, back, play, next, stop := d.r.nowPlayingButtons()
				at := image.Pt(g.X, g.Y)
				d.mu.Lock()
				words, library := d.showingLyrics, d.showingLibrary
				d.mu.Unlock()
				switch {
				case library && at.In(d.r.libraryButton().Inset(-d.r.s(8))):
					d.openLibrary()
				case words && at.In(d.r.lyricsButton(time.Now()).Inset(-d.r.s(8))):
					d.mu.Lock()
					d.lyricsOn = !d.lyricsOn
					d.mu.Unlock()
					d.wake()
				case at.In(stop):
					go d.endMusic()
				case at.In(fav):
					go d.favorite()
				case at.In(back):
					d.musicTap(media.TransportPrevious, back)
				case at.In(next):
					d.musicTap(media.TransportNext, next)
				default:
					d.musicTap(media.TransportToggle, play)
				}
				return
			}
			d.musicTap(media.TransportToggle, image.Rectangle{})
			return
		}
		voice.Get().Action()
	case touch.SwipeLeft:
		// From the right edge it brings the drawer in, on the tab it was last on.
		if d.drawerSwipe(g) {
			d.openDrawerOver()
		}
	case touch.SwipeUp:
		// Up is the recipe Jarvis last looked up, when there is one (dashboard.RecipePath).
		d.openRecipe()
	case touch.SwipeDown:
		// From the top edge it is the sheet. Anywhere else down does nothing: up and down used to be
		// the volume, and a slanted swipe meant for the dashboard turned it instead.
		if g.Y < topEdge {
			d.mu.Lock()
			d.openedBy = image.Pt(g.X, g.Y)
			d.mu.Unlock()
			d.showSheet(true)
		}
	case touch.SwipeRight:
		// From the left edge it brings the dashboard up, the drawer's gesture mirrored.
		if d.r != nil && g.X < d.r.drawerEdge() && d.openDashboard() {
			return
		}
		// Right puts the now-playing page away until the track changes. It is the one gesture left on that
		// page that cannot be taken for play or pause, which a tap there has to be. It is not the drawer's
		// way out: the drawer has closed on this swipe since long before there was a page to put away,
		// because while it is in every finger belongs to drawerGesture.
		d.mu.Lock()
		sheet, showing, idle := d.sheet, d.showingPlaying, d.view.Phase == "idle"
		d.mu.Unlock()
		_, camera := home.Get().Camera()
		if sheet || camera || !idle || !showing {
			return
		}
		rd := home.Get().Radio()
		if rd.Now == "Music Assistant" {
			return // MA owns a persistent full-screen Now Playing page while the session exists.
		}
		// Whether the music is playing, not whether the page says so: a carried stream that has stopped
		// still has a page, and a dismissal made on one of those is over too.
		playing, _ := media.Get().ScreenState()
		d.mu.Lock()
		d.away, d.awayTrack, d.awayStation, d.awayPlaying = true, rd.Title, rd.Now, playing
		d.mu.Unlock()
		d.wake()
	}
}

// musicStripGesture consumes a finger that lands inside the native music strip, when the last frame
// actually drew one. This is shared by the clock and streamed-dashboard paths so the browser can
// never steal the strip's buttons, while pages that do not draw the strip keep their original touch
// priority. Non-tap gestures inside the strip are consumed but do not trigger an action.
func (d *Display) musicStripGesture(g touch.Gesture) bool {
	d.mu.Lock()
	strip := d.showingStrip
	d.mu.Unlock()
	if !strip || d.r == nil {
		return false
	}
	at := image.Pt(g.X, g.Y)
	if !at.In(d.r.stripRect()) {
		return false
	}
	if g.Kind != touch.Tap {
		return true
	}

	back, play, next, closeX := d.r.stripButtons()
	switch {
	case at.In(closeX):
		go d.endMusic()
	case at.In(back):
		d.musicTap(media.TransportPrevious, back)
	case at.In(play):
		d.musicTap(media.TransportToggle, play)
	case at.In(next):
		d.musicTap(media.TransportNext, next)
	case at.In(d.r.stripSong()):
		d.mu.Lock()
		d.stripFullUntil = time.Now().Add(stripFull)
		d.away, d.awayTrack, d.awayStation, d.awayPlaying = false, "", "", false
		d.mu.Unlock()
		d.wake()
	}
	return true
}

// favNoteFor is how long the word under a pressed star stays up.
const favNoteFor = 3 * time.Second

// favorite saves what the page shows to favorites, or takes it out again when the star was already
// filled, and says which it did or that it could not.
func (d *Display) favorite() {
	d.mu.Lock()
	key, on := d.favShown, d.favShownOn
	d.mu.Unlock()
	if key == "" {
		return
	}
	var err error
	note := "Saved to favorites"
	if on {
		err, note = home.Get().UnfavoriteNow(), "Removed from favorites"
	} else {
		err = home.Get().FavoriteNow()
	}
	switch {
	case errors.Is(err, home.ErrFavoriteElsewhere):
		note = "Already a favorite"
	case errors.Is(err, home.ErrOwnStationsFull):
		note = "Your stations are full"
	case err != nil && on:
		note = "Couldn't remove it"
	case err != nil:
		note = "Couldn't save it"
	}
	if err != nil {
		slog.Warn("favorite: the star's press failed", "removing", on, "err", err)
	}
	d.mu.Lock()
	if err == nil {
		d.favPress = favPress{key, !on}
	}
	d.favNote, d.favNoteUntil = note, time.Now().Add(favNoteFor)
	d.mu.Unlock()
	d.wake()
	time.AfterFunc(favNoteFor, d.wake)
}

// endMusic is "go home" and the now-playing screen's Done: the music stops, a held track is let go,
// and the page goes back to the clock.
func (d *Display) endMusic() {
	// Nothing playing is nothing to ask: "go home" on an idle clock with a remote merely holding the
	// speaker sent a stop to Music Assistant's queue when it was the last remote. What a stop means for
	// whoever has the music is home's to decide: a stream this device did not start is asked to stop, and
	// its own is ended rather than paused.
	if playing, paused := media.Get().ScreenState(); playing || paused {
		home.Get().Stop()
	}
	// And the page goes, as a swipe puts it away: a track somebody else is holding paused is still a
	// page. It comes back for the next track, and after a stop the next start counts as one, so the
	// music just ended is not playing however the page went.
	rd := home.Get().Radio()
	d.mu.Lock()
	d.away, d.awayTrack, d.awayStation, d.awayPlaying = true, rd.Title, rd.Now, false
	d.mu.Unlock()
	d.wake()
}

// putAway is whether the now-playing page is staying out of the way. A swipe puts it away and it stays
// away only for the track it was put away on: a dismissal is temporary without needing a timer, and the
// page comes back for the next song. Nothing playing at all clears it, so what comes next is not hidden
// by a gesture made about something else.
//
// A start after a stop is a next song as much as a different one is, which is why awayPlaying is
// remembered: the quiet while that track is stopped keeps the page away, and playing it again brings the
// page back. Reading the track's name alone made restarting the station somebody had just put away look
// like the same track going on, so the one deliberate press that should never leave you looking at the
// clock was the one that did.
func (d *Display) putAway(rd home.Radio, wanted, playing bool) bool {
	d.mu.Lock()
	defer d.mu.Unlock()

	if !wanted || !d.away || rd.Title != d.awayTrack || rd.Now != d.awayStation {
		d.away, d.awayTrack, d.awayStation, d.awayPlaying = false, "", "", false
		return false
	}
	switch {
	case playing && !d.awayPlaying:
		d.away, d.awayTrack, d.awayStation = false, "", ""
		return false
	case !playing:
		d.awayPlaying = false
	}
	return true
}

// nowPlaying is whether the idle screen should be what is playing rather than the clock: something
// on the speaker, and a name to put on the page.
//
// A stream this player did not start says so itself - Music Assistant over Sendspin - and it is the
// reason the page exists at all, so it is asked first: its audio never passes through this player's own
// stream, which is what Playing() reports on.
func (d *Display) nowPlaying() bool {
	if media.Get().ExternalPlaying() {
		return true
	}
	if _, _, _, ok := media.Get().Held(); ok {
		return true
	}
	if !home.Get().Radio().Configured {
		return false
	}
	playing, paused := media.Get().Playing()
	if playing || paused {
		return true
	}
	// Home Assistant named a station a moment ago: the stream is on its way, show the page now.
	d.mu.Lock()
	cued := time.Since(d.radioCue) < radioCueFor
	d.mu.Unlock()
	return cued
}

// radioCueFor is how long the now-playing page is shown on Home Assistant's word alone, before
// the stream itself has to be playing to keep it.
const radioCueFor = 20 * time.Second

func (d *Display) showSheet(on bool) {
	d.mu.Lock()
	d.sheet = on
	d.restartArm, d.picker, d.cardScroll, d.pickScroll, d.colors = time.Time{}, "", 0, 0, false
	d.root, d.page, d.adjust, d.confirm, d.draft = true, "", "", "", nil
	d.mu.Unlock()
	slog.Info("settings sheet", "open", on)
	d.wake()
}

// ShowWeather puts the weather page up, the forecast or the rain map, as a question would.
func (d *Display) ShowWeather(radar bool) {
	d.mu.Lock()
	d.weatherUntil, d.radar, d.sheet = time.Now().Add(weatherShow), radar, false
	d.calUntil, d.calDetail = time.Time{}, nil // the forecast comes up over the calendar, not under it
	d.closeAlert()                             // and over an alert
	d.mu.Unlock()
	d.wake()
}

func (d *Display) ceilingOrDefault() int {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.ceiling == 0 {
		return config.DefaultScreenBrightness
	}
	return d.ceiling
}

// night puts the screen out inside the night window once nothing has happened for a while, and
// brings it back when the window ends. It reports true when it changed the screen, so the frame
// is redrawn from the new state.
func (d *Display) night(now time.Time, on bool, view voice.State) bool {
	in := nightNow(now)
	d.showNightMode(in)
	d.mu.Lock()
	dark, touched, viewAt, wifiUp := d.nightDark, d.touchedAt, d.viewAt, d.wifiOpen
	d.mu.Unlock()
	if !in {
		d.mu.Lock()
		glowing := d.nightGlow
		d.nightGlow = false
		d.mu.Unlock()
		if glowing {
			slog.Info("screen: night over, night light off")
			d.relight(true)
		}
	}
	switch {
	case in && on:
		// Only a call lifts the night light, and only to half brightness (relight): somebody has to
		// see who it is. The light before an alarm lifts it too, since that light is what was asked
		// for. Everything else stays at the night light's level: an alarm or a timer rings over the
		// night clock, and the words of an answer, an announcement or a reminder are shown dim, the
		// clock coming back after, rather than the main screen at full brightness for somebody asleep
		// or just waking.
		// The Wi-Fi page too: it is the one page that fixes a device with no network, and a clock
		// that has never been set (no network, no time) can put a new device in its night.
		// And a browser asking to be let in to the setup page: its Allow has to be seen and pressed,
		// and whoever is asking is standing at the device.
		lift := phone.Get().Busy() || ring.IsSounding() || sunriseProgress(now) > 0 || wifiUp || setup.Get().Waiting()
		active := view.Phase != "idle" || now.Sub(touched) < nightIdle || now.Sub(viewAt) < nightIdle
		// Something playing is not somebody using the screen. At night it is rain or music to sleep
		// to, and it kept a guest room's screen at full brightness all night.
		if !lift && active {
			// Left as it is: a night light stays one, and a screen a long press brought up stays up
			// until the room has been quiet a while.
			return false
		}
		if lift {
			// A call at night is seen at half brightness (relight), and the light before an alarm at
			// its own; neither at the night light's.
			d.mu.Lock()
			glowing := d.nightGlow
			d.nightGlow = false
			d.mu.Unlock()
			if glowing {
				d.relight(true)
				return true
			}
			return false
		}
		if config.Get().Screen.NightLight {
			d.mu.Lock()
			already := d.nightGlow
			d.nightGlow = true
			d.mu.Unlock()
			if already {
				return false
			}
			slog.Info("screen: night, down to a night light")
			d.relight(true)
			return true
		}
		slog.Info("screen: night, going dark")
		d.mu.Lock()
		d.nightDark = true
		d.mu.Unlock()
		d.apply(false, d.ceilingOrDefault(), false)
		return true
	case !in && dark && !on:
		slog.Info("screen: night over, back on")
		d.mu.Lock()
		d.nightDark = false
		d.mu.Unlock()
		d.apply(true, d.ceilingOrDefault(), false)
		return true
	case !in && dark:
		d.mu.Lock()
		d.nightDark = false
		d.mu.Unlock()
	}
	return false
}

// nightIdle is how long the panel stays lit after a finger or a turn during the night.
const nightIdle = 90 * time.Second

// inNight is whether now falls in the window, which may cross midnight.

// ---- Wi-Fi pages ----

// OpenWifi shows the Wi-Fi pages; demo puts the keyboard up for a made-up network, for a look at
// the layout from afar. CloseWifi takes them down.
func (d *Display) OpenWifi(demo bool) {
	d.openWifi()
	if demo {
		d.mu.Lock()
		d.wifi.pick = &wifi.Network{SSID: "Example Network", Secured: true}
		d.wifi.text = "correct horse"
		d.mu.Unlock()
		d.wake()
	}
}

func (d *Display) CloseWifi() { d.closeWifi() }

// openWifi shows the network list and starts a scan.
func (d *Display) openWifi() {
	d.mu.Lock()
	d.wifiOpen = true
	d.wifi = wifiState{scanning: true}
	glowing, dark := d.nightGlow, d.nightDark
	d.nightGlow, d.nightDark = false, false
	d.mu.Unlock()
	// Over the night, as a call is: the page has to be seen and pressed.
	if dark {
		d.apply(true, d.ceilingOrDefault(), false)
	} else if glowing {
		d.relight(true)
	}
	wifi.SettingUp(true)
	slog.Info("wifi page", "open", true)
	go d.refreshWifi()
	go d.wifiScan()
	d.wake()
}

func (d *Display) closeWifi() {
	d.mu.Lock()
	d.wifiOpen = false
	d.wifi = wifiState{}
	d.mu.Unlock()
	wifi.SettingUp(false)
	slog.Info("wifi page", "open", false)
	d.wake()
}

func (d *Display) refreshWifi() {
	st := wifi.Current(context.Background())
	d.mu.Lock()
	d.wifi.status = st
	d.mu.Unlock()
	d.wake()
}

func (d *Display) wifiScan() {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	nets, err := wifi.Scan(ctx)
	d.mu.Lock()
	d.wifi.scanning = false
	if err != nil {
		d.wifi.err = "scan failed: " + err.Error()
	} else {
		d.wifi.nets, d.wifi.err = nets, ""
	}
	d.mu.Unlock()
	d.wake()
}

// wifiGesture is a finger on the Wi-Fi pages. The list scrolls, and a swipe back closes it; the
// keyboard only takes taps, so a slip of the finger does not throw the typing away.
func (d *Display) wifiGesture(g touch.Gesture) {
	d.mu.Lock()
	w := d.wifi
	d.mu.Unlock()
	switch g.Kind {
	case touch.SwipeUp, touch.SwipeDown:
		if w.pick != nil {
			return
		}
		cardMax, _ := d.r.scrollLimits()
		by := notchPx
		if g.Kind == touch.SwipeDown {
			by = -notchPx
		}
		d.mu.Lock()
		d.wifi.scroll = min(max(d.wifi.scroll+by, 0), cardMax)
		d.mu.Unlock()
		return
	case touch.SwipeRight:
		if w.pick == nil {
			d.closeWifi()
		}
		return
	case touch.Tap:
	default:
		return
	}
	z, ok := d.r.zoneAt(g.X, g.Y)
	if !ok {
		return
	}
	switch {
	case z.kind == zoneBack && w.pick != nil:
		d.wifiKey("cancel")
	case z.kind == zoneBack:
		d.closeWifi()
	case z.kind == zoneAction && z.id == "rescan":
		d.mu.Lock()
		d.wifi.scanning, d.wifi.err = true, ""
		d.mu.Unlock()
		go d.wifiScan()
	case z.kind == zoneAction && w.pick != nil:
		if k, ok := strings.CutPrefix(z.id, "key:"); ok {
			d.wifiKey(k)
		}
	case z.kind == zoneRow && w.pick == nil:
		for _, n := range w.nets {
			if n.SSID != z.id {
				continue
			}
			if !n.Secured {
				d.wifiJoin(n, "")
				return
			}
			d.mu.Lock()
			d.wifi.pick, d.wifi.text, d.wifi.shift, d.wifi.symbols, d.wifi.err = &n, "", false, false, ""
			d.mu.Unlock()
			return
		}
	}
}

// wifiKey is a key on the passphrase keyboard.
func (d *Display) wifiKey(k string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.wifi.busy != "" {
		return
	}
	switch k {
	case "":
	case "shift":
		d.wifi.shift = !d.wifi.shift
	case "symbols":
		d.wifi.symbols = !d.wifi.symbols
	case "space":
		d.wifi.text += " "
	case "backspace":
		if n := len(d.wifi.text); n > 0 {
			d.wifi.text = d.wifi.text[:n-1]
		}
	case "cancel":
		d.wifi.pick, d.wifi.text = nil, ""
	case "join":
		n := *d.wifi.pick
		text := d.wifi.text
		d.mu.Unlock()
		d.wifiJoin(n, text)
		d.mu.Lock()
	default:
		if d.wifi.shift && !d.wifi.symbols {
			k = strings.ToUpper(k)
			d.wifi.shift = false
		}
		d.wifi.text += k
	}
}

// wifiJoin joins a network in the background and shows how it went.
func (d *Display) wifiJoin(n wifi.Network, passphrase string) {
	d.mu.Lock()
	d.wifi.busy, d.wifi.err = i18n.F("Connecting to {name}…", "name", n.SSID), ""
	d.mu.Unlock()
	d.wake()
	go func() {
		err := wifi.Join(context.Background(), n.SSID, passphrase)
		d.mu.Lock()
		d.wifi.busy = ""
		if err != nil {
			d.wifi.err = err.Error()
			slog.Warn("wifi: join failed", "ssid", n.SSID, "err", err)
		} else {
			d.wifi.pick, d.wifi.text = nil, ""
			slog.Info("wifi: joined", "ssid", n.SSID)
		}
		d.mu.Unlock()
		d.refreshWifi()
	}()
}

// answerShots hands a copy of the canvas to whoever asked for a screenshot.
func (d *Display) answerShots() {
	for {
		select {
		case ch := <-d.shots:
			var img *image.RGBA
			if d.r != nil {
				img = image.NewRGBA(d.r.dst.Rect)
				copy(img.Pix, d.r.dst.Pix)
			}
			ch <- img
		default:
			return
		}
	}
}

// Screenshot is the next frame drawn, for a look at the panel from afar.
func (d *Display) Screenshot(ctx context.Context) (*image.RGBA, error) {
	ch := make(chan *image.RGBA, 1)
	select {
	case d.shots <- ch:
	default:
		return nil, errors.New("too many screenshot requests")
	}
	d.wake()
	select {
	case img := <-ch:
		if img == nil {
			return nil, errors.New("the screen is not up")
		}
		return img, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// SetTheme switches to a preset by name and saves it.
func (d *Display) SetTheme(name string) {
	if err := config.Set().Screen().Theme(themes[themeIndex(name)].name); err != nil {
		slog.Warn("saving the theme failed", "err", err)
	}
	d.wake()
}

// Demo puts placeholders in for the owner's details for a while, for published screenshots.
func (d *Display) Demo(for_ time.Duration) {
	d.mu.Lock()
	d.demoUntil = time.Now().Add(for_)
	d.mu.Unlock()
	d.wake()
}

// OpenSheet puts the settings sheet up on the named tab, or takes it down for "off". It reports whether
// the name meant anything.
func (d *Display) OpenSheet(name string) bool {
	if strings.EqualFold(name, "off") {
		d.showSheet(false)
		return true
	}
	switch strings.ToLower(name) {
	case "cameras":
		d.showSheet(false)
		d.openDrawer(drawerCameras)
		return true
	case "radio":
		d.showSheet(false)
		d.openDrawer(drawerRadio)
		return true
	case "dashboard":
		d.showSheet(false)
		d.closeDrawer()
		return d.openDashboard()
	}
	if strings.EqualFold(name, "settings") {
		d.closeDrawer()
		d.showSheet(true)
		return true
	}
	cat, ok := catByName(name)
	if !ok {
		return false
	}
	d.closeDrawer()
	d.mu.Lock()
	d.sheet, d.cat, d.picker, d.restartArm = true, cat, "", time.Time{}
	d.draft, d.cardScroll, d.pickScroll, d.colors = nil, 0, 0, false
	d.root, d.page, d.adjust, d.confirm = false, "", "", ""
	d.mu.Unlock()
	d.wake()
	return true
}

// pressFlash is how long a music button lights up under the finger that pressed it.
const pressFlash = 220 * time.Millisecond

// musicTap is a transport button on the music page or the strip. The button lights at once and the
// page shows what was asked for straight away; Music Assistant is told behind that, so the touch loop
// never waits on Home Assistant. b is the button pressed, lit for pressFlash; none for a tap without one.
func (d *Display) musicTap(t media.Transport, b image.Rectangle) {
	if !b.Empty() {
		d.mu.Lock()
		d.pressed, d.pressedUntil = b, time.Now().Add(pressFlash)
		d.mu.Unlock()
		time.AfterFunc(pressFlash, d.wake)
	}
	home.Get().MusicTap(t)
	d.wake()
}

func (d *Display) wake() {
	select {
	case d.poke <- struct{}{}:
	default:
	}
}

// Start opens the framebuffer.
func (d *Display) Start(context.Context) error {
	dev, err := screen.Open()
	if err != nil {
		return err
	}
	d.dev = dev
	d.r = newRenderer(dev.Canvas())
	w, h := dev.Size()
	d.mu.Lock()
	d.wide = w > 1000 || h > 1000
	d.mu.Unlock()
	d.logo = newSplash(d.r)
	d.mu.Lock()
	d.booting, d.started = true, time.Now()
	d.mu.Unlock()
	d.settleScreen(dev)
	slog.Info("screen open", "fb", dev.String())
	return nil
}

func (d *Display) Close() error {
	if d.dev == nil {
		return nil
	}
	err := d.dev.Close()
	d.dev = nil
	return err
}

// Run redraws the screen until ctx is canceled: on the second while idle, faster while a turn is
// on or the volume is showing, and at once when something changes.
func (d *Display) Run(ctx context.Context) error {
	for {
		wait := d.frame()
		select {
		case <-ctx.Done():
			return nil
		case <-d.poke:
		case <-time.After(wait):
		}
	}
}

// frame draws what the moment calls for and says how long until the next one is due.
func (d *Display) frame() time.Duration {
	d.mu.Lock()
	on, view, at := d.on, d.view, d.viewAt
	volume, volAt := d.volume, d.volAt
	d.mu.Unlock()

	// Apply the configured palette before any page can draw. Fresh devices show the welcome page
	// before ordinary idle rendering, so doing this later would briefly (or indefinitely, until the
	// welcome is dismissed) expose the old compiled-in TECHO5 palette instead of the default, Obsidian.
	t := current()
	applyTheme(t)
	d.syncThemeSelect(t.name)

	now := time.Now()
	// The buttons' factory reset count goes over everything, the splash and a dark night included.
	if d.comboFrame(now, on) {
		if err := d.dev.Present(); err != nil {
			slog.Warn("presenting the frame failed", "err", err)
		}
		return comboTick
	}
	d.popupTick(now)
	ring := d.ringing(now)
	call := phone.Get().State()
	_, reminding := remind.Get().Showing()
	night := nightNow(now)
	if !on && shouldWakeDarkScreen(call.Phase != phone.Idle, ring.any(), reminding, night) {
		// Calls and rings are interactive even at night, so they wake a dark panel; relight caps them
		// gently in the night. A reminder alone wakes the panel by day but not in a sleeping room.
		d.apply(true, d.ceilingOrDefault(), false)
		on = true
	}
	if !on && d.popupUp() != nil && !nightNow(now) {
		// A pop-up lights a dark panel by day. At night it waits there, dark, until the screen is woken.
		d.apply(true, d.ceilingOrDefault(), false)
		on = true
	}
	if !on && sunriseProgress(now) > 0 {
		// The light before an alarm: the panel comes on dim and rises, so the room is lit before the
		// sound starts. relight decides how much of the screen's brightness it is using.
		d.apply(true, d.ceilingOrDefault(), false)
		on = true
	}
	if d.night(now, on, view) {
		// The panel put out: nothing to draw. The glow lifted or lowered: this frame shows the change,
		// rather than the old page for a minute at the new brightness.
		d.mu.Lock()
		lit := d.on
		d.mu.Unlock()
		if !lit {
			return time.Minute
		}
		on = lit // the night may have just turned the panel back on: this frame is its first
	}
	if !config.Get().Screen.Welcomed {
		d.mu.Lock()
		page := d.guidePage
		d.mu.Unlock()
		d.r.welcome(scene{now: now}, page)
		if err := d.dev.Present(); err != nil {
			slog.Warn("presenting the frame failed", "err", err)
		}
		d.answerShots()
		return time.Second
	}
	if !on {
		// Dark panel: nothing to draw, and nothing to redraw until told, or until the night ends.
		d.answerShots()
		if config.Get().Screen.Night != "" {
			return time.Minute
		}
		return time.Hour
	}
	d.mu.Lock()
	booting, started := d.booting, d.started
	if booting && now.Sub(started) >= splashMin && voice.Get().Ready() {
		d.booting, booting = false, false
		slog.Info("splash done", "after", now.Sub(started).Round(time.Millisecond))
	}
	// A ring ends the splash whatever else is or is not ready. Waiting on Home Assistant's voice
	// pipeline has no timeout, so a device that never reaches it stays on the logo for ever — and an
	// alarm going off behind a logo is a screen that will not say what is making the noise or where
	// to press to stop it. Nothing the splash is waiting for is needed to draw a ringing page.
	if booting && ring.any() {
		d.booting, booting = false, false
		slog.Info("splash cut short: something is ringing", "after", now.Sub(started).Round(time.Millisecond))
	}
	// So does the Wi-Fi page: it opens by itself on a device with no address, which is a device whose
	// splash would otherwise wait for a Home Assistant it cannot reach, over the one page that fixes it.
	if booting && d.wifiOpen {
		d.booting, booting = false, false
		slog.Info("splash cut short: Wi-Fi setup", "after", now.Sub(started).Round(time.Millisecond))
	}
	// Nothing else ends it, a slow Home Assistant included: a Show that is not connected stays on
	// "Connecting to Home Assistant" rather than showing pages that need Home Assistant to work.
	d.mu.Unlock()
	if booting {
		d.r.drawSplash(d.logo, now.Sub(started), d.bootStep(now, started))
		if err := d.dev.Present(); err != nil {
			slog.Warn("presenting the frame failed", "err", err)
		}
		return 80 * time.Millisecond
	}

	d.assistantTakesOver()
	d.menuIdle(now, view.Phase != "idle")

	s := scene{now: now, phase: view.Phase, heard: view.Heard, reply: view.Reply, since: at, ring: ring, call: call}
	s.snooze = config.Get().Alarms.Snooze()
	s.alarms = alarm.Get().View(now)
	s.timers = timer.Get().List(now)
	s.glance = home.Get().Glance()
	if view.Phase == "idle" && (view.Heard != "" || view.Reply != "") && now.Sub(at) < linger() {
		s.phase = "lingering"
	}
	d.mu.Lock()
	quiet := d.quiet
	d.mu.Unlock()
	if quiet && (s.phase == "thinking" || s.phase == "replying" || s.phase == "lingering") {
		// A screen command: the screen it asked for is the answer, not the words.
		s.phase, s.heard, s.reply = "idle", "", ""
	}
	if s.phase == "listening" || s.phase == "thinking" || s.phase == "replying" || s.phase == "lingering" {
		s.eq = eqFor(s.phase, nightNow(now), now)
		s.eq.wave = waveOn()
		s.eq.glow = glowOn()
		s.eq.classic = !equalizerOn()
	}
	// A stream this player is carrying is the room's when it is what is being heard: the page names it,
	// and says what it is doing, though the audio never passes through this player's own stream. Both,
	// not just playing: the page tests paused first, so a station left paused underneath would label
	// somebody else's track as Paused - and a remote merely holding the speaker, with a station playing
	// underneath it, is not the room's at all (see media.Player.Carried). A remote's track paused from
	// here and since let go is shown paused too (see media.Player.Held).
	s.playing, s.paused = media.Get().ScreenState()
	s.muted, _ = mute.Get().Muted()
	if !volAt.IsZero() && now.Sub(volAt) < volumeShow {
		s.volume, s.showVolume = volume, true
	}
	s.bt = btaudio.Get().State()
	d.mu.Lock()
	s.showSheet = d.sheet
	s.showWifi, s.wifi, s.btScroll = d.wifiOpen, d.wifi, d.btScroll
	restartArm := d.restartArm
	wifiAt := d.wifiAt
	d.mu.Unlock()
	if (s.showSheet || s.showWifi) && now.Sub(wifiAt) > 5*time.Second && wifi.Available() {
		d.mu.Lock()
		d.wifiAt = now
		d.mu.Unlock()
		go d.refreshWifi()
	}
	if s.showSheet {
		s.sheet = d.gather(s, restartArm)
		// The category gather already took under the lock, rather than d.cat again: the sheet is
		// opened on a category from whatever goroutine took the tap or the voice command, and
		// reading it unlocked here is a race that would also let the frame disagree with itself.
		if s.sheet.cat == catSecurity {
			s.security = security.Get().State()
		}
		d.mu.Lock()
		demo := now.Before(d.demoUntil)
		d.mu.Unlock()
		if demo {
			s.sheet.name, s.sheet.wifi, s.sheet.address = "Kitchen", "HomeWiFi  ·  10.0.0.50", "10.0.0.50"
			s.sheet.wifiName, s.sheet.weather, s.sheet.demo = "HomeWiFi", "Home", true
			for i := range s.security.Keys {
				s.security.Keys[i].Comment = "laptop"
			}
		}
		d.mu.Lock()
		if d.draft != nil && d.cat == catAlarms {
			c := *d.draft
			s.draft = &c
		}
		d.mu.Unlock()
	}
	s.camera, s.showCamera = home.Get().Camera()
	s.cameraSound, s.cameraSoundLive = home.Get().CameraSoundOn(), home.Get().CameraSoundLive()
	// Whether the idle screen wants to be what is playing. A Jarvis-routed Music Assistant queue may
	// live on this room's preferred external speaker rather than on this Show, so route state is also
	// a real Now Playing session even when Sendspin is not carrying audio locally.
	s.music = home.Get().MusicPlayback()
	routedMusic := s.music.Playing || s.music.Paused
	wants := (s.phase == "idle") && (d.nowPlaying() || routedMusic)
	d.mu.Lock()
	s.showDrawer, s.drawerTab, s.drawerScroll, s.drawerPick, s.pickScroll = d.drawer && !s.showSheet && !s.showCamera, d.drawerTab, d.drawerScroll, d.drawerPick, d.pickScroll
	d.mu.Unlock()
	d.mu.Lock()
	s.demo = now.Before(d.demoUntil)
	s.redClock = d.nightGlow && config.Get().Screen.NightRed
	s.redStyle = config.Get().Screen.NightClockStyle
	d.mu.Unlock()
	if s.showDrawer && s.drawerTab == drawerCameras {
		s.cameras = home.Get().Cameras()
	}
	if s.showDrawer && s.drawerTab == drawerCall {
		s.callees = phone.Get().Callees()
		d.mu.Lock()
		d.callees = s.callees
		d.mu.Unlock()
	}
	if (s.showDrawer && s.drawerTab == drawerRadio) || wants {
		s.radio = home.Get().Radio()
	}
	if s.showDrawer && s.drawerTab == drawerRadio && !s.demo {
		// Not for screenshots: a list's logos say where the owner lives as its names do.
		s.logos = home.Get().StationLogos(s.radio.Stations)
	}
	if routedMusic && s.radio.Now != "Music Assistant" {
		// Preferred external MA speakers do not send Sendspin audio to this Show. Populate the native
		// page from the resolved HA/MA player instead so the screen still says what and where is playing.
		s.radio.Now, s.radio.Music = "Music Assistant", true
		s.radio.Title, s.radio.Artist, s.radio.Album = s.music.Title, s.music.Artist, s.music.Album
		s.radio.Art, s.radio.Thumb = home.Get().MusicPlaybackArt()
		s.playing, s.paused = s.music.Playing, s.music.Paused
	}
	s.nowPlaying = wants && !d.putAway(s.radio, wants, s.playing)
	if s.radio.Now == "Music Assistant" {
		// Jarvis Show treats Music Assistant as a dedicated full-screen surface for the complete
		// active/paused session. Radio may still use the legacy timed strip setting.
		s.nowPlaying = wants
	}
	// A paused song gives the screen back after a while, strip and all: Music Assistant keeps a speaker
	// paused for good. Play, or another song, brings the page back (see pausedTooLong).
	if d.pausedTooLong(now, s.radio.Title+"\x00"+s.radio.Now+"\x00"+s.music.Entity, wants && s.paused && !s.playing) {
		wants, s.nowPlaying = false, false
	}
	d.mu.Lock()
	// Not under the sunrise light, which draws no strip: taps on a strip nobody can see would still act.
	if wants && s.radio.Now != "Music Assistant" && d.stripDue(now, s.radio, s.nowPlaying) && sunriseProgress(now) == 0 {
		s.nowPlaying, s.strip = false, true
	}
	// The star is the music's own word on it: Music Assistant's mark on the song, or the station on a
	// favorites list. A press here is believed over that until the lists agree or the song changes, as
	// Music Assistant's queue is read a poll behind.
	key := favKeyOf(s.radio)
	if s.radio.Now == "Music Assistant" {
		s.faved = s.music.Favorite && s.music.Title == s.radio.Title
	} else {
		s.faved = home.StationFavorite(s.radio.Now)
	}
	s.faved = d.favPress.shown(key, s.faved)
	if s.nowPlaying {
		d.favShown, d.favShownOn = key, s.faved
	}
	if now.Before(d.favNoteUntil) {
		s.favNote = i18n.T(d.favNote)
	}
	if now.Before(d.pressedUntil) {
		s.pressed = d.pressed
	}
	if l := home.Get().MusicLyrics(); s.nowPlaying && s.radio.Now == "Music Assistant" && l != nil && l.Title == s.music.Title {
		s.lyrics = l
	}
	s.showLyrics = d.lyricsOn && s.lyrics != nil
	d.showingPlaying, d.showingStrip, d.showingWord = s.nowPlaying, s.strip, playingWord(s) != ""
	d.showingLyrics = s.lyrics != nil
	// Music Assistant's own pages, through dashcast, for what this page has no room for: the library,
	// the queue, the groups.
	s.library = s.nowPlaying && s.radio.Now == "Music Assistant" && config.Get().Dashboard.Server != ""
	d.showingLibrary = s.library
	d.mu.Unlock()
	s.weather = home.Get().Weather()
	d.calendarScene(&s, now)
	d.alertScene(&s, now)
	d.mu.Lock()
	s.showWeather = (s.phase == "idle" || s.phase == "lingering") && now.Before(d.weatherUntil)
	s.showRadar = s.showWeather && d.radar
	d.mu.Unlock()
	if s.showWeather {
		s.forecast = home.Get().Forecast()
		// Asked for while the forecast is up, so the rain map is there by the time its button is
		// pressed rather than starting then; it fetches only when due.
		s.radar = home.Get().Radar()
		if !s.showRadar {
			s.sky = skyNow(weatherNow(s.weather, s.forecast))
		}
	}
	if !s.showRadar {
		s.radar = home.RadarView{}
	}

	// boring is the plain idle page — the same set of pages draw() checks before falling through to
	// bigClock/nowPlaying. Background mode rides along with it; Screensaver only takes over once it
	// has held for the configured wait, tracked by how long it has run continuously.
	d.dashScene(&s, ring.any() || call.Phase != phone.Idle)
	boring := s.phase == "idle" && call.Phase == phone.Idle && !ring.any() && !s.bt.Pairing &&
		!s.showWifi && !s.showSheet && !s.showCamera && !s.showRadar && !s.showWeather && !s.showCalendar && !s.showAlert && !s.nowPlaying &&
		!s.showDash && !s.dashBehind
	// A browser waiting to be let in is a page of its own, over whatever is on the screen: asking for
	// the setup page is done from the settings screen, so the answer has to reach somebody who is
	// still standing in it. It was set only on the idle page once, and the press could not be given
	// without leaving settings first.
	s.setupAsking = setup.Get().Waiting()

	// The announcement state is read every frame like the setup page's, for the same reason: the
	// drawer's button, the page an arriving one puts up and the line that says this microphone is
	// open all have to be right wherever the screen happens to be.
	s.announceReady = config.Get().Home.HouseWord != ""
	s.announceRecording = announce.Get().Recording()
	s.announcePeers = len(announce.Peers())
	s.announcement, s.showAnnouncement = announce.Get().Showing()
	s.reminder, s.showReminder = remind.Get().Showing()
	s.popup = d.popupUp()
	if s.reminder.From != config.Get().Device.Name {
		s.reminderFrom = s.reminder.From
	}
	s.missed = missedNote(now, false)
	if s.missed == "" {
		s.update = updateNote(false)
	}
	s.install, s.installAt, _ = installNote()

	if boring {
		s.slideshow = home.Get().SlideshowBackground()
		if s.slideshow == nil {
			s.slideshowTrouble = home.Get().SlideshowTrouble()
		}
		s.sunrise, s.sunriseFace = sunriseProgress(now), config.Get().Alarms.SunriseFace
	}
	d.mu.Lock()
	if !boring {
		d.slideshowIdleSince = time.Time{}
	} else if d.slideshowIdleSince.IsZero() {
		d.slideshowIdleSince = now
	}
	idleSince := d.slideshowIdleSince
	d.mu.Unlock()
	if boring && !idleSince.IsZero() && now.Sub(idleSince) >= home.Get().SlideshowIdleTimeout() {
		s.slideshowScreensaver = home.Get().SlideshowScreensaverPhoto()
		s.slideshowOverlay = home.Get().SlideshowOverlay()
	}

	// The Call button is on the clock itself and only there: on any other page a tap where it would be
	// still does what that page does.
	s.callButton = callButton.Load() && s.phase == "idle" && !s.nowPlaying && !s.strip && !s.showWeather && !s.showCalendar &&
		s.sunrise == 0 && s.slideshowScreensaver == nil && !s.showDrawer && !s.showSheet
	d.mu.Lock()
	d.callShown = s.callButton
	d.mu.Unlock()

	d.r.draw(s)
	d.followLists(d.listsFollow(s, ring.any()))
	if err := d.dev.Present(); err != nil {
		slog.Warn("presenting the frame failed", "err", err)
	}
	d.answerShots()

	if s.redClock && s.redStyle == nightStyleFlip && d.r.flipBusy(time.Now()) {
		return flipFrame // a card is flipping
	}
	if s.install != "" {
		return installFrame // the spinner turns until the restart takes the screen
	}
	if s.showCamera {
		return 250 * time.Millisecond // frames arrive as they are fetched; this keeps up
	}
	if s.showDash {
		return time.Second // a streamed picture wakes it as it arrives
	}
	if ring.any() || call.Phase != phone.Idle {
		return 500 * time.Millisecond
	}
	if s.bt.Pairing || s.showSheet || s.showWifi || s.showDrawer {
		return 500 * time.Millisecond
	}
	if s.showRadar && len(s.radar.Frames) > 1 {
		return radarStep
	}
	if s.showWeather && s.sky != fxNone && !s.setupAsking {
		return fxFrame
	}
	if (s.slideshow != nil || s.slideshowScreensaver != nil) && home.Get().SlideshowTransitioning() {
		return home.SlideshowFrame
	}
	if turnShown(s) && !(s.phase == "lingering" && s.eq.quiet) {
		if s.eq.wave {
			return waveFrame
		}
		return eqFrame // the bars are moving
	}
	if s.showWeather || s.nowPlaying {
		next := time.Until(now.Truncate(idleFrame).Add(idleFrame))
		if s.showLyrics && s.music.Playing {
			// The next line lights when it is sung, not on the second after.
			el := time.Duration(s.music.Elapsed(now) * float64(time.Second))
			if at := s.lyrics.NextAt(el); at > el && at-el < next {
				return at - el
			}
		}
		return next
	}
	if (s.phase == "idle" || s.phase == "lingering") && !s.showVolume {
		// On the next whole second, so the clock changes when the second does.
		return time.Until(now.Truncate(idleFrame).Add(idleFrame))
	}
	return activeFrame
}

func shouldWakeDarkScreen(call, ringing, reminding, night bool) bool {
	return call || ringing || (!night && reminding)
}
