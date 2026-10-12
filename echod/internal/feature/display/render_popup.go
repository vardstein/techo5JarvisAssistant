//go:build !dot && !spot

package display

import (
	"time"

	"github.com/HuskerMinion/techo5/echod/internal/feature/home"
	"github.com/HuskerMinion/techo5/echod/internal/i18n"
	"github.com/HuskerMinion/techo5/echod/internal/lib/hass"
)

// popupSoon is how soon an event is, as its pop-up heads it: "In 15 minutes", "Starting now", "Today".
func popupSoon(e hass.Event, now time.Time) string {
	if e.AllDay {
		return "Today"
	}
	switch m := int(e.Start.Sub(now).Round(time.Minute) / time.Minute); {
	case m <= 0 && now.Before(e.End):
		return "Now"
	case m <= 0:
		return "Started"
	case m == 1:
		return "In a minute"
	case m < 60:
		return i18n.Sprintf("In %d minutes", m)
	case m == 60:
		return "In an hour"
	default:
		return i18n.Sprintf("At %s", clockText(e.Start))
	}
}

// popupCalendarName is a calendar's name as Home Assistant gives it.
func popupCalendarName(id string) string {
	for _, c := range home.Get().Calendars() {
		if c.ID == id {
			return c.Name
		}
	}
	return ""
}
