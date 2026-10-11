//go:build spot

package all

// notOnThisDevice is what the Echo Spot leaves out of the Show's list: night mode, themes, calendar
// pop-ups and the menu size (its round card is sized to its circle).
var notOnThisDevice = []string{"calendar_popup_all_day", "calendar_popup_before", "calendar_popup_chime", "calendar_popups", "screen_now_playing", "screen_theme", "screen_at_night", "screen_night_clock_style", "screen_night_end", "screen_night_hours", "screen_night_light_level", "screen_clock_position", "screen_date_color", "screen_menu_size", "screen_night_mode", "screen_night_start"}
