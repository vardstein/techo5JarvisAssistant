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

The cards live in the Genbu integration, in
[`ha/custom_components/genbu/frontend/`](../ha/custom_components/genbu/frontend/).

| File | What it is |
|---|---|
| `jarvis-room-environment-card.js` | Temperature and humidity, on a glass panel |
| `jarvis-weather-card.js` | Current weather: condition, temperature, feels-like, humidity, wind |
| `jarvis-live-timer-card.js` | The voice satellite's timer that runs out first, counting down. Takes no space when no timer runs |
| `jarvis-calendar-card.js` | The next event from one calendar (`entity`) or several (`entities`), however far off: it looks two weeks ahead first, then further, up to a year (`maxDaysAhead`). A missing or unreadable calendar is said, not shown as "Keine Termine" |
| `jarvis-recipe-card.js` | The recipe Jarvis last looked up (`sensor.jarvis_recipe`): ingredients left, steps right, a long one scrolls. For the recipe page, see [docs/recipes.md](../docs/recipes.md) |
| `jarvis-dashboard.yaml` | The dashboard itself, with example entity ids |
| `jarvis-recipe-dashboard.yaml` | The recipe page, a dashboard of its own at `jarvis-rezept` |

## Installing

The installer can make this dashboard and the recipe page for you, from what your house has, once
Genbu 1.3.1 or newer and wall-clock-card are in ([docs/jarvis-crown-installer.md](../docs/jarvis-crown-installer.md),
`--jarvis-dashboard`). By hand:

1. Install [wall-clock-card](https://github.com/rkotulan/ha-wall-clock-card), through HACS or by
   hand from its releases. The dashboard was built against v3.17.3.
2. Add Genbu 1.3.1 or newer ([docs/update-leader.md](../docs/update-leader.md)). It brings the cards
   and loads them on every page, so there is nothing to copy and no resource to add. When Genbu is
   updated, browsers pick up any card that changed on their next reload.

   Cards copied to `/config/www/openjade/` by hand before this can go, with their resources. Until
   they do they are harmless: Genbu's cards load first, and a card is only defined once.
3. **Settings → Dashboards → Add dashboard → New dashboard from scratch**, open it, **⋮ → Edit
   dashboard → ⋮ → Raw configuration editor**, and paste `jarvis-dashboard.yaml`.
4. Put your own entities in: the two room sensors, your weather entity, your calendar
   ([docs/calendars.md](../docs/calendars.md) if it isn't in Home Assistant yet), and for the
   timer card the voice satellite's entity and device id. The device id is the last part of the
   address when you open the device under **Settings → Devices & services → Devices**.
5. On the Show, set **Dashboard to show** to this dashboard, and **Dashboard when idle** if it should
   replace the clock.

The timer card asks Home Assistant for the satellite's timers every five seconds, through the
`HassTimerStatus` intent.
