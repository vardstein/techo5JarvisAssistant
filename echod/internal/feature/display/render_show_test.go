//go:build !dot && !spot

package display

import (
	"fmt"
	"image"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/config"
	"github.com/HuskerMinion/techo5/echod/internal/feature/alarm"
	"github.com/HuskerMinion/techo5/echod/internal/feature/announce"
	"github.com/HuskerMinion/techo5/echod/internal/feature/dashboard"
	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/feature/phone"
	"github.com/HuskerMinion/techo5/echod/internal/feature/remind"
	"github.com/HuskerMinion/techo5/echod/internal/feature/security"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// The panel's own size, so a preview is what the device draws rather than something like it.
const (
	showWide = 960
	showHigh = 480

	// The Echo Show 8's panel, which draws the same layout scaled.
	show8Wide = 1280
	show8High = 800
)

// Every scene draws without panicking; with SHOW_PREVIEW set to a directory, each is written there
// as a PNG to look at. The Spot has had this since its screen was built (render_spot_test.go); the
// Show never did, so a change to its layout was argued about in words.
func TestShowScenesDraw(t *testing.T) {
	at := time.Date(2026, 9, 16, 14, 7, 0, 0, time.Local)
	sky := home.Weather{Condition: "partlycloudy", Temp: "72°"}
	var week []hass.Day
	for i, c := range []string{"partlycloudy", "rainy", "lightning-rainy", "sunny", "snowy", "cloudy"} {
		week = append(week, hass.Day{When: at.AddDate(0, 0, i), Condition: c, High: float64(78 - 3*i), Low: float64(55 - 2*i), Rain: 10 * i})
	}

	scenes := map[string]scene{
		"clock":            {now: at, phase: "idle", weather: sky},
		"clock-sunny":      {now: at, phase: "idle", weather: home.Weather{Condition: "sunny", Temp: "88°"}},
		"clock-rainy":      {now: at, phase: "idle", weather: home.Weather{Condition: "rainy", Temp: "54°"}},
		"clock-night":      {now: at, phase: "idle", weather: home.Weather{Condition: "clear-night", Temp: "58°"}},
		"clock-no-weather": {now: at, phase: "idle"},
		// Twelve minutes on, the clock and the weather have gone four steps round the ring (burnin.go).
		"clock-shifted":     {now: at.Add(4 * shiftEvery), phase: "idle", weather: sky},
		"clock-call-button": {now: at, phase: "idle", weather: sky, callButton: true},
		"drawer-call": {now: at, phase: "idle", weather: sky, showDrawer: true, drawerTab: drawerCall, demo: true,
			callees: []phone.Callee{{Name: "a", Device: true}, {Name: "b", Device: true}, {Name: "c", Device: true},
				{Name: "d", Number: "15551234567"}, {Name: "e", Number: "106"}}},
		"drawer-call-empty": {now: at, phase: "idle", weather: sky, showDrawer: true, drawerTab: drawerCall},
		// Stations with their logos, one playing, one with none to show.
		"drawer-radio-logos": {now: at, phase: "idle", weather: sky, showDrawer: true, drawerTab: drawerRadio,
			radio: home.Radio{Configured: true, Source: "local", Sources: 2, Playing: true, Now: "Jazz Lounge",
				Stations: []string{"KXYZ 101.1", "Jazz Lounge", "Talk at Nine", "Nordic Pop", "No Picture Here"}},
			logos: map[string]*image.RGBA{"KXYZ 101.1": testCover(192, true), "Jazz Lounge": testCover(192, false),
				"Talk at Nine": testCover(64, true), "Nordic Pop": testCover(120, false)}},
		// Music Assistant's stations, one of them playing through it.
		"drawer-radio-ma": {now: at, phase: "idle", weather: sky, showDrawer: true, drawerTab: drawerRadio,
			radio: home.Radio{Configured: true, Source: "music_assistant", Sources: 3, Playing: true, Music: true,
				Now: "Music Assistant", Listed: "Hitradio FFH", Stations: []string{"Radio Paradise", "Hitradio FFH", "SWR3"}},
			logos: map[string]*image.RGBA{"Radio Paradise": testCover(192, false)}},
		"drawer-radio-ma-empty": {now: at, phase: "idle", weather: sky, showDrawer: true, drawerTab: drawerRadio,
			radio: home.Radio{Configured: true, Source: "music_assistant", Sources: 3}},
		// Radio Browser did not answer, and the stations near home came from TuneIn instead.
		"drawer-radio-tunein": {now: at, phase: "idle", weather: sky, showDrawer: true, drawerTab: drawerRadio,
			radio: home.Radio{Configured: true, Source: "local", Sources: 3, Via: "TuneIn",
				Stations: []string{"Hitradio FFH", "hr3", "YOU FM"}}},
		"nowplaying": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Now: "KXYZ 101.1", Title: "Take It Easy", Artist: "Eagles"}},
		// Music Assistant's track, carried over Sendspin: named, with the three buttons.
		"nowplaying-music-assistant": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Playing: true, Now: "Music Assistant", Title: "Some Jazz", Artist: "The Quartet", Music: true}},
		"nowplaying-music-assistant-paused": {now: at, phase: "idle", nowPlaying: true, paused: true, weather: sky,
			radio: home.Radio{Now: "Music Assistant", Title: "Some Jazz", Artist: "The Quartet", Music: true}},
		// A duet's artist and a queue whose next song has a long name, as a Show had them: both were cut.
		"nowplaying-long-names": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Playing: true, Now: "Music Assistant", Title: "All I Have", Artist: "LL Cool J/Jennifer Lopez",
				Album: "This Is Me...Then", Music: true},
			music: home.MusicPlaybackView{Next: "Christina Aguilera – Genie in a Bottle"}},
		"weather":         {now: at, phase: "idle", weather: sky, forecast: week, showWeather: true},
		"weather-rain":    {now: at, phase: "idle", weather: home.Weather{Condition: "rainy", Temp: "54°"}, forecast: week, showWeather: true, sky: fxRain},
		"weather-pouring": {now: at, phase: "idle", weather: home.Weather{Condition: "pouring", Temp: "54°"}, forecast: week, showWeather: true, sky: fxPour},
		"weather-snow":    {now: at, phase: "idle", weather: home.Weather{Condition: "snowy", Temp: "28°"}, forecast: week, showWeather: true, sky: fxSnow},
		"weather-storm":   {now: at, phase: "idle", weather: home.Weather{Condition: "lightning-rainy", Temp: "68°"}, forecast: week, showWeather: true, sky: fxStorm},
		"weather-fog":     {now: at, phase: "idle", weather: home.Weather{Condition: "fog", Temp: "48°"}, forecast: week, showWeather: true, sky: fxFog},
		"setup-ask":       {now: at, phase: "idle", weather: sky, setupAsking: true},
		"announcement": {now: at, phase: "idle", weather: sky, showAnnouncement: true,
			announcement: announce.Message{From: "Kitchen", Text: "dinner is ready"}},
		"announcement-voice": {now: at, phase: "idle", weather: sky, showAnnouncement: true,
			announcement: announce.Message{From: "Laundry Room"}},
		// A long room name with words as well. The words used to start at a fixed offset from the
		// edge, so a name wider than that was drawn straight through by them.
		"announcement-long": {now: at, phase: "idle", weather: sky, showAnnouncement: true,
			announcement: announce.Message{From: "Laundry Room", Text: "the washing is finished"}},
		// As long as an automation is allowed to send, to see where it stops.
		"announcement-longest": {now: at, phase: "idle", weather: sky, showAnnouncement: true,
			announcement: announce.Message{From: "Laundry Room",
				Text: "the washing machine has finished its cycle and the door is unlocked now"}},
		"announce-recording": {now: at, phase: "idle", weather: sky, announceRecording: true, announcePeers: 3},
		"reminder": {now: at, phase: "idle", weather: sky, showReminder: true,
			reminder: remind.Reminder{Label: "Take the medication"}},
		"reminder-from-elsewhere": {now: at, phase: "idle", weather: sky, showReminder: true, reminderFrom: "Kitchen",
			reminder: remind.Reminder{Label: "Check the boiling eggs — they've been on for 10 minutes now"}},
		// Longer than three lines, to see the ellipsis rather than a sentence that stops.
		"reminder-longest": {now: at, phase: "idle", weather: sky, showReminder: true, reminderFrom: "Laundry Room",
			reminder: remind.Reminder{Label: "Move the washing to the dryer, then put the towels on the rack by the back door " +
				"so they are dry by the time everybody is back from the pool this afternoon"}},
		"reminder-and-announcement": {now: at, phase: "idle", weather: sky, showReminder: true,
			reminder:         remind.Reminder{Label: "Take the trash out"},
			showAnnouncement: true, announcement: announce.Message{From: "Kitchen", Text: "dinner is ready"}},
		"announce-drawer": {now: at, phase: "idle", weather: sky, showDrawer: true,
			drawerTab: drawerAnnounce, announceReady: true, announcePeers: 3},
		// The three pages that put two large answers at the foot of the screen. They are the only
		// thing on those pages somebody has to press, so how they look is worth a picture.
		"ringing-alarm": {now: at, phase: "idle",
			ring: ringState{alarm: &alarm.Ring{Label: "Wake up"}, snoozable: true}, snooze: 9},
		"ringing-timer": {now: at, phase: "idle", ring: ringState{timer: "Pasta"}},
		// Silenced by a button press and waiting to be told what that meant. It looks like a ring
		// that stopped and is not one, so the page has to say so.
		"ringing-silenced": {now: at, phase: "idle", snooze: 9,
			ring: ringState{alarm: &alarm.Ring{Label: "Wake up"}, snoozable: true, silenced: true}},
		"ringing-silenced-timer": {now: at, phase: "idle", snooze: 9,
			ring: ringState{timer: "Pasta", silenced: true}},
		// Muted, on the pages the header has to carry it onto. The ringing one is why the header
		// exists: an alarm sounding on a device that cannot hear "stop" used to look exactly like one
		// that could.
		"muted-clock": {now: at, phase: "idle", weather: sky, muted: true},
		"muted-ringing-alarm": {now: at, phase: "idle", muted: true,
			ring: ringState{alarm: &alarm.Ring{Label: "Wake up"}, snoozable: true}, snooze: 9},
		"muted-call": {now: at, phase: "idle", muted: true,
			call: phone.State{Phase: phone.Talking, Peer: "104", Since: at.Add(-90 * time.Second)}},
		// Muted while a browser is asking to be let in. The footer used to show one of these instead
		// of the other; now they are on different edges and neither hides the other.
		"muted-setup-ask": {now: at, phase: "idle", weather: sky, muted: true, setupAsking: true},
		"call-ringing":    {now: at, phase: "idle", call: phone.State{Phase: phone.Ringing, Peer: "104"}},
		// A timer finishing during a call. This page is drawn over the ringing one, so without a
		// line here the noise has nothing on screen to explain it, and the buttons below belong to
		// the call.
		"call-with-timer-ringing": {now: at, phase: "idle", snooze: 9,
			call: phone.State{Phase: phone.Talking, Peer: "104", Since: at.Add(-90 * time.Second)},
			ring: ringState{timer: "Pasta"}},
		"call-talking": {now: at, phase: "idle", call: phone.State{Phase: phone.Talking, Peer: "104", Since: at.Add(-90 * time.Second)}},
		"settings-sound": {now: at, phase: "idle", showSheet: true,
			sheet: settings{cat: catSound, volume: 15, wakeWord: "Okay Nabu"}},
		"settings-sound-tone": {now: at, phase: "idle", showSheet: true,
			sheet: settings{cat: catSound, volume: 15, wakeWord: "Okay Nabu", cardScroll: 210}},
		// A row per key: its comment, with type and fingerprint end beside it (security.Key).
		"settings-security": {now: at, phase: "idle", showSheet: true, sheet: settings{cat: catSecurity},
			security: security.State{SSHAvailable: true, SSH: true, SSHRunning: true, Encrypted: true,
				Keys: []security.Key{{Comment: "openWRTAdminToken", Kind: "ED25519 …bZQxtE"}, {Kind: "RSA …Vq3s0A"}}}},
		// A turn under the sheet, said in the strip (render_turnstrip.go).
		"settings-listening": {now: at, phase: "listening", showSheet: true,
			sheet: settings{cat: catSound, volume: 15, wakeWord: "Okay Nabu"}},
		"settings-thinking": {now: at, phase: "thinking", heard: "Ja, bitte", showSheet: true,
			sheet: settings{cat: catSound, volume: 15, wakeWord: "Okay Nabu"}},
		// The turn's own page, in the classic style (the other styles are added below).
		"turn-classic-listening": {now: at, since: at.Add(-400 * time.Millisecond), phase: "listening", weather: sky,
			eq: classicVoice(0.7)},
		"turn-classic-thinking": {now: at, since: at.Add(-400 * time.Millisecond), phase: "thinking", weather: sky,
			heard: "Wie wird das Wetter morgen"},
		"turn-classic-replying": {now: at, phase: "replying", weather: sky, heard: "Wie wird das Wetter morgen",
			reply: "Morgen wird es wechselhaft, bis 17 Grad, am Nachmittag ab und zu Regen.", eq: classicVoice(0.5)},
		"turn-classic-long": {now: at, phase: "replying", weather: sky, heard: "Was koche ich heute",
			reply: "Wie wäre es mit einer Gemüsepfanne: Paprika, Zucchini und Zwiebeln anbraten, mit Sojasoße und etwas Honig ablöschen, dazu Reis. Dauert etwa 25 Minuten, und du brauchst nur eine Pfanne. Soll ich dir das Rezept auf den Bildschirm legen?"},
		"clock-music-strip": {now: at, phase: "idle", weather: sky, strip: true, playing: true,
			radio: home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Music: true}},
		"clock-music-strip-paused": {now: at, phase: "idle", weather: sky, strip: true, paused: true,
			radio: home.Radio{Now: "Music Assistant", Title: "A Very Long Song Title That Has To Be Cut Short", Artist: "Somebody", Music: true}},
		// With the pictures the page is built round: a cover, a station's logo, and a group with the
		// queue and the position filled in, the way Music Assistant sends it.
		"nowplaying-cover": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Album: "Lights Over the Water", Music: true, Thumb: testCover(400, false)},
			music: home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 74, Duration: 213, Next: "Harbour Song – Release"}},
		"nowplaying-cover-group-paused": {now: at, phase: "idle", nowPlaying: true, paused: true, faved: true, weather: sky,
			radio: home.Radio{Now: "Music Assistant", Title: "A Very Long Song Title That Has To Be Wrapped Over Two Lines And Then Cut", Artist: "Somebody With A Long Name", Music: true, Thumb: testCover(400, false)},
			music: home.MusicPlaybackView{Route: "wohnung", Rooms: []string{"Living Room", "Kitchen", "Bath"}, Paused: true, Position: 30, Duration: 240, Next: "Another One"}},
		// Titles as they come from a library: a take in brackets that goes with the album, and a
		// typographer's hyphen the font has no glyph for.
		"nowplaying-cover-take": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Now: "Music Assistant", Title: "My Love Is Like...Wo (All Star Mix – Main Pass)", Artist: "Mýa/John Doe", Album: "Moodring", Music: true, Thumb: testCover(400, false)},
			music: home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 10, Duration: 234}},
		"nowplaying-cover-hyphen": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Now: "Music Assistant", Title: "Un\u2010Break My Heart", Artist: "Toni Braxton", Album: "Secrets", Music: true, Thumb: testCover(400, false)},
			music: home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 195, Duration: 268}},
		// A song the music server has words for: the Lyrics pill in the head, then the words in place of
		// the song's details, timed with the line being sung lit, and untimed.
		"nowplaying-lyrics-button": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio:  home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Album: "Lights Over the Water", Music: true, Thumb: testCover(400, false)},
			music:  home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 74, Duration: 213, Next: "Harbour Song – Release"},
			lyrics: testLyrics(true)},
		// Music Assistant's own pages: the books on the cover, and the pages themselves while they come
		// up, with the tab back at the left edge.
		"nowplaying-library": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio:  home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Album: "Lights Over the Water", Music: true, Thumb: testCover(400, false)},
			music:  home.MusicPlaybackView{Rooms: []string{"Wohnzimmer"}, Playing: true, Position: 74, Duration: 213, Next: "Harbour Song – Release"},
			lyrics: testLyrics(true), library: true},
		"library-connecting": {now: at, phase: "idle", showDash: true, dashMode: config.DashboardStreamed, dashPage: dashboard.PageMusic},
		"nowplaying-lyrics": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio:  home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Music: true, Thumb: testCover(400, false)},
			music:  home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 21, Duration: 213},
			lyrics: testLyrics(true), showLyrics: true},
		"nowplaying-lyrics-plain": {now: at, phase: "idle", nowPlaying: true, paused: true, weather: sky,
			radio:  home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Music: true, Thumb: testCover(400, false)},
			music:  home.MusicPlaybackView{Rooms: []string{"Living Room"}, Paused: true, Position: 100, Duration: 213},
			lyrics: testLyrics(false), showLyrics: true},
		// The footer's own messages still show under the page.
		"nowplaying-cover-missed": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky, missed: "Missed: timer \"Pasta\" at 2:03 PM",
			radio: home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Music: true, Thumb: testCover(400, false)}},
		"nowplaying-logo": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky,
			radio: home.Radio{Now: "KXYZ 101.1", Title: "Morning Show", Logo: true, Thumb: testCover(400, true)}},
		"nowplaying-faved": {now: at, phase: "idle", nowPlaying: true, playing: true, faved: true, weather: sky,
			radio: home.Radio{Now: "Music Assistant", Title: "Some Jazz", Artist: "The Quartet", Music: true}},
		// The note a press of the star leaves on the cover for a few seconds: saved, with the star,
		// clear of the books; and for a station that could not be saved.
		"nowplaying-faved-note": {now: at, phase: "idle", nowPlaying: true, playing: true, faved: true, weather: sky, favNote: "Saved to favorites",
			radio: home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Album: "Lights Over the Water", Music: true, Thumb: testCover(400, false)},
			music: home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 74, Duration: 213, Favorite: true}},
		"nowplaying-faved-note-library": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky, favNote: "Removed from favorites",
			radio:  home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Album: "Lights Over the Water", Music: true, Thumb: testCover(400, false)},
			music:  home.MusicPlaybackView{Rooms: []string{"Wohnzimmer"}, Playing: true, Position: 74, Duration: 213},
			lyrics: testLyrics(true), library: true},
		"nowplaying-faved-note-station": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky, favNote: "Your stations are full",
			radio: home.Radio{Now: "KXYZ 101.1", Title: "Morning Show", Logo: true, Thumb: testCover(400, true)}},
		"nowplaying-faved-note-french": {now: at, phase: "idle", nowPlaying: true, playing: true, weather: sky, favNote: "Impossible de l'enregistrer",
			radio:  home.Radio{Now: "Music Assistant", Title: "Reason That I Sing", Artist: "Release", Music: true, Thumb: testCover(400, false)},
			music:  home.MusicPlaybackView{Rooms: []string{"Living Room"}, Playing: true, Position: 74, Duration: 213},
			lyrics: testLyrics(true), library: true},
		"nowplaying-faved-note-bare": {now: at, phase: "idle", nowPlaying: true, playing: true, faved: true, weather: sky, favNote: "Saved to favorites",
			radio: home.Radio{Now: "Music Assistant", Title: "Some Jazz", Artist: "The Quartet", Music: true}},
		"clock-missed": {now: at, phase: "idle", missed: "Missed: timer \"Pasta\" at 2:03 PM yesterday · and 1 more"},
		"settings-alarms": {now: at, phase: "idle", showSheet: true, snooze: 9,
			sheet: settings{cat: catAlarms}},
		"settings-alarms-end": {now: at, phase: "idle", showSheet: true, snooze: 9,
			sheet: settings{cat: catAlarms, cardScroll: 1000}},
		// The editor with the alarm's own wake light, scrolled to it.
		"settings-alarm-editor": {now: at, phase: "idle", showSheet: true, snooze: 9,
			sheet: settings{cat: catAlarms, cardScroll: 1000}, draft: &alarmDraft{
				alarm: config.Alarm{ID: "a", Hour: 6, Minute: 45, Days: config.DaysWeekdays, Label: "Wake up", Sunrise: 10, On: true}}},
	}

	// Weather alerts: the clock's badge, the page, a long one scrolled, and the pills over the rain map.
	wind := home.Alert{ID: "a", Event: "Wind Advisory", Severity: "Moderate", Sender: "NWS Omaha/Valley NE",
		Area: "Douglas; Sarpy; Cass", Ends: at.Add(20 * time.Hour), Color: home.AlertColor("Wind Advisory", ""), Here: true,
		Description: "What: North winds 15 to 25 mph with gusts up to 45 mph.\nWhere: Douglas, Sarpy and Cass counties.\nWhen: Until 6 AM Sunday.\nImpacts: Gusty winds will blow around unsecured objects.",
		Instruction: "Winds this strong can make driving difficult. Secure outdoor objects."}
	storm := home.Alert{ID: "b", Event: "Severe Thunderstorm Warning", Severity: "Severe", Sender: "NWS Omaha/Valley NE",
		Area: "Douglas", Ends: at.Add(40 * time.Minute), Color: home.AlertColor("Severe Thunderstorm Warning", ""), Here: true, Storm: true,
		Description: "At 2:05 PM, a severe thunderstorm was located near Elkhorn, moving east at 30 mph.", Instruction: "Move to an interior room."}
	alerts := home.AlertView{Here: []home.Alert{storm, wind}, Near: []home.Alert{storm, wind}}
	scenes["clock-alert"] = scene{now: at, phase: "idle", weather: sky, alerts: alerts}
	scenes["clock-alert-muted"] = scene{now: at, phase: "idle", weather: sky, alerts: alerts, muted: true}
	// An update on its way in: the download with its bar, then the slot being written with the sweep,
	// under the microphone's pill when that is up too.
	scenes["install-download"] = scene{now: at, phase: "idle", weather: sky, install: "Downloading 1.0.2 · 42%", installAt: 0.42}
	scenes["install-writing-muted"] = scene{now: at, phase: "idle", weather: sky, muted: true, install: "Installing 1.0.2…", installAt: -1}
	later := wind
	later.ID, later.Onset, later.Ends = "c", at.Add(26*time.Hour), at.Add(40*time.Hour)
	scenes["alert-page-later"] = scene{now: at, phase: "idle", showAlert: true, alerts: home.AlertView{Here: []home.Alert{later}}}
	many := home.AlertView{Here: []home.Alert{storm, wind,
		{ID: "d", Event: "Flood Watch", Color: home.AlertColor("Flood Watch", ""), Here: true},
		{ID: "e", Event: "Heat Advisory", Color: home.AlertColor("Heat Advisory", ""), Here: true},
		{ID: "f", Event: "Air Quality Alert", Color: home.AlertColor("Air Quality Alert", ""), Here: true}}}
	scenes["radar-alerts-many"] = scene{now: at, phase: "idle", showWeather: true, showRadar: true, alerts: many,
		radar: home.RadarView{Frames: []home.RadarFrame{{Image: image.NewRGBA(image.Rect(0, 0, showWide, showHigh)), At: at}},
			Home: image.Pt(showWide/2, showHigh/2)}}
	scenes["alert-page"] = scene{now: at, phase: "idle", showAlert: true, alerts: alerts}
	scenes["alert-page-2"] = scene{now: at, phase: "idle", showAlert: true, alertIdx: 1, alertScroll: 3, alerts: alerts}
	scenes["radar-alerts"] = scene{now: at, phase: "idle", showWeather: true, showRadar: true, alerts: alerts,
		radar: home.RadarView{Frames: []home.RadarFrame{{Image: image.NewRGBA(image.Rect(0, 0, showWide, showHigh)), At: at}},
			Home: image.Pt(showWide/2, showHigh/2), Places: []home.RadarPlace{{Name: "Omaha", At: image.Pt(470, 250), Pop: 480000}}}}

	// The turn's page in the other styles, at each step of a turn.
	for _, style := range []string{"bars", "wave", "glow"} {
		for _, step := range []struct {
			name, phase, heard, reply string
			amp                       float64
		}{
			{"listening", "listening", "", "", 0.7},
			{"thinking", "thinking", "Wie wird das Wetter morgen", "", 0},
			{"replying", "replying", "Wie wird das Wetter morgen", "Morgen wird es wechselhaft, bis 17 Grad, am Nachmittag ab und zu Regen.", 0.5},
		} {
			v := &eqView{wave: style == "wave", glow: style == "glow", thinking: step.phase == "thinking"}
			if step.phase == "thinking" {
				v.level, v.peak = make([]float64, eqBands), make([]float64, eqBands)
				eqThinking(at, v.level, v.peak)
			} else {
				v.level, v.peak = eqVoice(step.amp, 3)
			}
			scenes["turn-"+style+"-"+step.name] = scene{now: at, phase: step.phase, weather: sky,
				heard: step.heard, reply: step.reply, eq: v}
		}
	}

	// The light before an alarm, frame by frame: the same curve the panel follows, with the sun's
	// face on, for looking at away from a device at six in the morning.
	wake := at.Add(20 * time.Minute)
	for i := 0; i <= 20; i++ {
		p := float64(i) / 20
		scenes[fmt.Sprintf("sunrise-%02d", i)] = scene{
			now: wake.Add(-time.Duration((1-p)*20) * time.Minute), phase: "idle",
			sunrise: p, sunriseFace: true,
		}
	}

	dir := os.Getenv("SHOW_PREVIEW")
	// Both panels this build draws on. The Show 8 is not a second layout: it is this one scaled, and
	// the point of drawing it here is that the scaling can be looked at without a device.
	for _, panel := range []struct {
		name       string
		wide, high int
	}{
		{"", showWide, showHigh},
		{"-show8", show8Wide, show8High},
	} {
		for name, s := range scenes {
			img := image.NewRGBA(image.Rect(0, 0, panel.wide, panel.high))
			newRenderer(img).draw(s)
			if panel.name == "" {
				timeDraw(name, img, s)
			}
			if dir == "" {
				continue
			}
			f, err := os.Create(filepath.Join(dir, name+panel.name+".png"))
			if err != nil {
				t.Fatal(err)
			}
			if err := png.Encode(f, img); err != nil {
				t.Fatal(err)
			}
			if err := f.Close(); err != nil {
				t.Fatal(err)
			}
		}
	}
}

// A size written as a literal in this package is in the Show 5's pixels, and the Show 8 scales them.
// These are the two ends of that: 4:3 across, so 40 becomes 53 and the clock's 230 becomes 307.
func TestFixedSizesScaleToTheShow8(t *testing.T) {
	show5 := &paint{w: showWide, h: showHigh, sNum: showWide, sDen: drawnFor}
	show8 := &paint{w: show8Wide, h: show8High, sNum: show8Wide, sDen: drawnFor}

	if show5.scaled() {
		t.Error("the panel the layout was drawn for reports itself as scaled")
	}
	if !show8.scaled() {
		t.Error("the Show 8 reports itself as unscaled")
	}
	for _, c := range []struct{ in, want int }{
		{0, 0}, {1, 1}, {3, 4}, {40, 53}, {230, 307}, {-40, -53},
	} {
		if got := show5.s(c.in); got != c.in {
			t.Errorf("a Show 5 scaled %d to %d; it must not scale at all", c.in, got)
		}
		if got := show8.s(c.in); got != c.want {
			t.Errorf("a Show 8 scaled %d to %d, want %d", c.in, got, c.want)
		}
	}
}

// The sound control is drawn and found in the same frame: what is drawn is what is tappable, and a
// frame that does not draw it leaves nothing tappable behind. A control that moved while its tap
// stayed where it was would silence nothing, or take the view down when somebody reached for the
// sound.
func TestTheCameraSoundControlIsWhereItIsDrawn(t *testing.T) {
	at := time.Date(2026, 9, 16, 14, 7, 0, 0, time.Local)
	img := image.NewRGBA(image.Rect(0, 0, 1280, 800))
	r := newRenderer(img)
	cam := scene{now: at, phase: "idle", showCamera: true,
		camera: home.CameraView{Entity: "camera.deck", Name: "Deck"}}

	r.draw(cam)
	if r.cameraSoundTapped(image.Pt(r.w/2, r.h/2)) {
		t.Fatal("a tap found a sound control where the page draws none")
	}

	// The widest the control ever says, so this is inside the box whatever it is carrying.
	box := r.cameraSoundBox("Unmute")
	centre := box.Min.Add(image.Pt(box.Dx()/2, box.Dy()/2))

	cam.cameraSound, cam.cameraSoundLive = true, true
	r.draw(cam)
	if !r.cameraSoundTapped(centre) {
		t.Fatalf("a tap on the sound control, at %v, was missed", centre)
	}
	if r.cameraSoundTapped(image.Pt(r.margin, r.h-11)) {
		t.Fatal("a tap on the hint was taken for the sound control")
	}

	// Silenced, taken by an announcement, or never arrived: the control is still there, offering to ask
	// for the sound again. Drawn only while it is playing, muting would be a door that only closes.
	cam.cameraSoundLive = false
	r.draw(cam)
	if !r.cameraSoundTapped(centre) {
		t.Fatal("a sound that is not playing lost its control, so it could not be asked for again")
	}

	cam.cameraSound = false
	r.draw(cam)
	if r.cameraSoundTapped(centre) {
		t.Fatal("the control was left tappable for a view with no sound")
	}
}

// The control is measured from the words it carries and the face they are drawn in rather than being a
// fixed size: it has to hold "Unmute" on the panel the page is drawn for and on a wider one, and both to
// be on the page at all.
func TestTheCameraSoundControlFitsWhatItSays(t *testing.T) {
	for _, size := range []image.Rectangle{image.Rect(0, 0, drawnFor, 600), image.Rect(0, 0, 1280, 800)} {
		r := newRenderer(image.NewRGBA(size))
		for _, label := range []string{"Mute", "Unmute"} {
			b := r.cameraSoundBox(label)
			if b.Dx() < r.width(r.tiny, label) {
				t.Errorf("%dx%d: %q is %d wide in a box of %d", size.Dx(), size.Dy(), label, r.width(r.tiny, label), b.Dx())
			}
			if b.Max.X > r.w-r.margin || b.Min.Y < 0 || b.Max.Y > r.h {
				t.Errorf("%dx%d: %q is drawn off the page at %v", size.Dx(), size.Dy(), label, b)
			}
		}
	}
}

// testCover is a made-up picture for the scenes: a cover's worth of color and lines, or a logo on
// nothing with room round it, as home makes a station's.
func testCover(side int, logo bool) *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, side, side))
	for y := 0; y < side; y++ {
		for x := 0; x < side; x++ {
			if logo {
				dx, dy := x-side/2, y-side/2
				if dx*dx+dy*dy < side*side/9 {
					img.Pix[img.PixOffset(x, y)+0] = 0xc0
					img.Pix[img.PixOffset(x, y)+1] = 0x20
					img.Pix[img.PixOffset(x, y)+2] = 0x30
					img.Pix[img.PixOffset(x, y)+3] = 0xff
				}
				continue
			}
			i := img.PixOffset(x, y)
			img.Pix[i] = uint8(30 + 120*y/side)
			img.Pix[i+1] = uint8(60 + 80*x/side)
			img.Pix[i+2] = uint8(140 - 60*y/side)
			img.Pix[i+3] = 0xff
			if (x+y)%40 < 3 {
				img.Pix[i], img.Pix[i+1], img.Pix[i+2] = 240, 200, 120
			}
		}
	}
	return img
}

// testLyrics is a made-up song's words, timed every four seconds or not at all, with an instrumental
// gap and a line long enough to wrap.
func testLyrics(synced bool) *home.Lyrics {
	words := []string{"", "Down by the water where the lights come on", "Somebody's singing an older song",
		"", "And I keep walking because the night is long and the harbour wall is the only thing I know",
		"Reason that I sing", "Reason that I sing", "Lights over the water", "Carry me home", "Carry me home"}
	l := &home.Lyrics{Title: "Reason That I Sing", Synced: synced}
	for i, w := range words {
		l.Lines = append(l.Lines, home.LyricLine{At: time.Duration(i*4) * time.Second, Text: w})
	}
	return l
}
