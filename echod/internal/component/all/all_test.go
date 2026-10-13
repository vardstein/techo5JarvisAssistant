package all

import (
	"slices"
	"strings"
	"testing"

	esphome "github.com/ygelfand/go-esphome-device"

	"github.com/HuskerMinion/techo5/echod/internal/component"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/buttons"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/led"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/metrics"
	"github.com/HuskerMinion/techo5/echod/internal/hardware/privacy"
)

// registered is every entity the components put up, by object id.
//
// This is an inventory, not a contract: renaming one is fine, and the list is meant to be edited
// deliberately when that happens. What it catches is a component that stopped registering — which
// costs nothing at build time and shows up as an entity quietly missing from Home Assistant.
// Jarvis exposes one wake slot and the J46 microphone mute-status sensor.
var registered = []string{
	"alarm_snooze",
	"alarm_snooze_length",
	"alarm_sound",
	"alarm_stop",
	"bass",
	"ble_advertisements",
	"bluetooth_audio",
	"bluetooth_disconnect",
	"bluetooth_pairing",
	"bluetooth_proxy",
	"bluetooth_reconnect",
	"button_action",
	"button_mute",
	"button_volume_down",
	"button_volume_up",
	"cached_data",
	"calendar_popup_all_day",
	"calendar_popup_before",
	"calendar_popup_chime",
	"calendar_popups",
	"camera_sound",
	"camera_web_access",
	"check_for_updates",
	"committed_version",
	"cpu_cores",
	"cpu_cores_online",
	"cpu_temperature",
	"duck_on_near_miss",
	"failure_effect",
	"firmware",
	"follow_up_1",
	"follow_up_after_1",
	"follow_up_tone_1",
	"follow_ups_1",
	"free_space",
	"hardware_color",
	"headphones",
	"home_assistant_key",
	"home_assistant_sounds",
	"intercom_do_not_disturb",
	"intercom_drop_in",
	"ip_address",
	"keep_recordings_1",
	"keep_recordings_2",
	"last_heard",
	"last_reply",
	"last_wake_word",
	"load_average",
	"lux",
	"max_listen_1",
	"max_think_1",
	"media_duck_level",
	"media_on_turn",
	"memory_available",
	"metrics_interval",
	"mic_mute",
	"microphone_cancel_echo",
	"microphone_echo_canceller",
	"microphone_end_of_speech",
	"microphone_gain",
	"microphone_leveling",
	"microphone_mixing",
	"microphone_mute_status",
	"microphone_noise_reduction",
	"microphone_sensitivity",
	"min_cores",
	"missed_ring",
	"mute_led_brightness",
	"next_alarm",
	"noise_layer_1",
	"noise_layer_2",
	"phone",
	"phone_answer",
	"phone_hangup",
	"phone_peer",
	"purge_cache",
	"quiet_hours",
	"radar_source",
	"radio_artist",
	"radio_station",
	"radio_temperature",
	"radio_title",
	"reply_buffer_1",
	"reply_delivery_1",
	"replying_effect_1",
	"restart",
	"ring",
	"ring_muted",
	"ring_volume",
	"room_floor",
	"room_level",
	"room_reaction",
	"screen",
	"screen_answer_time",
	"screen_at_night",
	"screen_auto_brightness",
	"screen_brightness",
	"screen_call_button",
	"screen_camera_time",
	"screen_clock_format",
	"screen_clock_position",
	"screen_dashboard",
	"screen_dashboard_idle",
	"screen_dashboard_kiosk",
	"screen_dashboard_own_user",
	"screen_dashboard_view",
	"screen_date_color",
	"screen_language",
	"screen_menu_size",
	"screen_night_clock_style",
	"screen_night_end",
	"screen_night_hours",
	"screen_night_light_level",
	"screen_night_mode",
	"screen_night_start",
	"screen_now_playing",
	"screen_theme",
	"screen_turn_style",
	"screen_weather_animation",
	"screen_web_access",
	"segment_1",
	"segment_10",
	"segment_11",
	"segment_12",
	"segment_2",
	"segment_3",
	"segment_4",
	"segment_5",
	"segment_6",
	"segment_7",
	"segment_8",
	"segment_9",
	"sendspin",
	"sendspin_state",
	"setup_page",
	"sleep_timer",
	"slideshow_folder",
	"slideshow_interval",
	"slideshow_mode",
	"slideshow_screensaver_idle",
	"slideshow_screensaver_overlay",
	"slideshow_shuffle",
	"slideshow_subfolders",
	"speaker",
	"speaker_eq",
	"stop_word_sensitivity",
	"test_playback",
	"thinking_effect_1",
	"timers",
	"treble",
	"update_automatically",
	"update_channel",
	"update_outcome",
	"update_status",
	"voice_resampling",
	"wake_assistant_1",
	"wake_effect_1",
	"wake_near_miss",
	"wake_threshold_1",
	"wake_tone_1",
	"weather_alerts",
	"weather_source",
	"wifi_received",
	"wifi_sent",
	"wifi_signal",
}

// ringOnly are the entities a device without a ring leaves out: the light, its segments and every effect.
var ringOnly = []string{"failure_effect", "replying_effect_1", "ring", "ring_muted", "room_reaction", "thinking_effect_1", "wake_effect_1"}

// Each device reports only the hardware it has (GENBU-142), so the list is checked with a ring and
// without one, and with what this machine's buttons, mute LED and thermal zones are.
func TestEveryComponentStillRegisters(t *testing.T) {
	was := led.HasRing
	defer func() { led.HasRing = was }()
	zones := metrics.Reader{}.Temperatures()

	for _, ring := range []bool{true, false} {
		led.HasRing = func() bool { return ring }
		var got []string
		for _, e := range component.Default().Entities() {
			got = append(got, objectID(e))
		}
		slices.Sort(got)

		want := slices.DeleteFunc(slices.Clone(registered), func(id string) bool {
			_, cpu := zones["mtktscpu"]
			_, radio := zones["mtktswmt"]
			switch {
			case slices.Contains(notOnThisDevice, id):
			case !ring && (slices.Contains(ringOnly, id) || strings.HasPrefix(id, "segment_")):
			case id == "button_action" && !buttons.HasAction():
			case id == "mute_led_brightness" && !privacy.LightDims:
			case id == "cpu_temperature" && !cpu, id == "radio_temperature" && !radio:
			default:
				return false
			}
			return true
		})
		if !slices.Equal(got, want) {
			t.Errorf("ring %v: registered entities changed\n got: %s\nwant: %s",
				ring, strings.Join(got, " "), strings.Join(want, " "))
		}
	}
}

// Two components claiming one object id is a collision Home Assistant resolves by keeping one of
// them, silently.
func TestNoDuplicateObjectIDs(t *testing.T) {
	seen := map[string]bool{}
	for _, e := range component.Default().Entities() {
		id := objectID(e)
		if seen[id] {
			t.Errorf("two components registered %q", id)
		}
		seen[id] = true
	}
}

// Every component has a name, which is what its log lines and its status are keyed on.
func TestEveryComponentIsNamed(t *testing.T) {
	for _, c := range component.Default().All() {
		if c.Name() == "" {
			t.Errorf("%T has no name", c)
		}
	}
}

// objectID reads the id off whatever kind of entity it is, through the Base every one embeds.
func objectID(e esphome.Entity) string {
	type based interface{ Object() string }
	if b, ok := e.(based); ok {
		return b.Object()
	}
	return ""
}
