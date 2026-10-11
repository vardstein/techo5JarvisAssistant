# Jarvis dashboard

A Home Assistant dashboard for the screen, made to be the Show's idle page: a big clock and the
date, the room's temperature and humidity top left, the weather top right, and along the bottom the
next timer and the next calendar event.

It scales to the screen it is on. It was laid out on a 1280×800 Echo Show 8, and every size is
written relative to the viewport, so a Show 5 (960×480) or a browser window gets the same picture,
just smaller. The shorter side decides, so nothing runs into the clock on a wide screen.

On a small screen the two top cards drop their words: no titles or labels, just an icon next to
each number (temperature and humidity on the left; the weather, humidity and wind on the right).
Each card decides from its own width, so there is nothing to configure, and a browser window
switches back and forth as you resize it.

It uses custom cards, so the Show has to **stream** it (dashcast). Drawn mode would show placeholder
tiles. See [docs/dashboards.md](../docs/dashboards.md).

The cards' text is German ("Wetter", "Kalender", "Luftfeuchtigkeit", "+2 weitere") and dates are
formatted for de-DE. Change the strings in the card files if you need another language.

## What's here

| File | What it is |
|---|---|
| `www/openjade/jarvis-room-environment-card.js` | Temperature and humidity, on a glass panel |
| `www/openjade/jarvis-weather-card.js` | Current weather: condition, temperature, feels-like, humidity, wind |
| `www/openjade/jarvis-live-timer-card.js` | The voice satellite's timer that runs out first, counting down. Takes no space when no timer runs |
| `www/openjade/jarvis-calendar-card.js` | The next event from one calendar (`entity`) or several (`entities`), however far off: it looks two weeks ahead first, then further, up to a year (`maxDaysAhead`). A missing or unreadable calendar is said, not shown as "Keine Termine" |
| `www/openjade/jarvis-recipe-card.js` | The recipe Jarvis last looked up (`sensor.jarvis_recipe`): ingredients left, steps right, a long one scrolls. For the recipe page, see [docs/recipes.md](../docs/recipes.md) |
| `jarvis-dashboard.yaml` | The dashboard itself, with example entity ids |
| `jarvis-recipe-dashboard.yaml` | The recipe page, a dashboard of its own at `jarvis-rezept` |

## Installing

1. Install [wall-clock-card](https://github.com/rkotulan/ha-wall-clock-card), through HACS or by
   hand from its releases. The dashboard was built against v3.17.3.
2. Copy the four cards in `www/openjade/` to `/config/www/openjade/` on Home Assistant (the File
   editor or Samba add-on will do). Home Assistant serves them under `/local/openjade/`. The recipe
   card is only for the recipe page ([docs/recipes.md](../docs/recipes.md)).
3. Add them as resources, type *JavaScript module*: **Settings → Dashboards → ⋮ → Resources**, or
   under `lovelace: resources:` in `configuration.yaml` if you keep resources in YAML.

   ```yaml
   - url: /local/openjade/jarvis-room-environment-card.js?v=1
     type: module
   - url: /local/openjade/jarvis-weather-card.js?v=1
     type: module
   - url: /local/openjade/jarvis-live-timer-card.js?v=1
     type: module
   - url: /local/openjade/jarvis-calendar-card.js?v=3
     type: module
   ```

   When you update a card file later, raise its `?v=`. Home Assistant tells browsers to keep these
   files for a month, so without a new `?v=` a browser that has the old one keeps showing it,
   dashcast's included.
4. **Settings → Dashboards → Add dashboard → New dashboard from scratch**, open it, **⋮ → Edit
   dashboard → ⋮ → Raw configuration editor**, and paste `jarvis-dashboard.yaml`.
5. Put your own entities in: the two room sensors, your weather entity, your calendar
   ([docs/calendars.md](../docs/calendars.md) if it isn't in Home Assistant yet), and for the
   timer card the voice satellite's entity and device id. The device id is the last part of the
   address when you open the device under **Settings → Devices & services → Devices**.
6. On the Show, set **Dashboard to show** to this dashboard, and **Dashboard when idle** if it should
   replace the clock.

The timer card asks Home Assistant for the satellite's timers every five seconds, through the
`HassTimerStatus` intent.
