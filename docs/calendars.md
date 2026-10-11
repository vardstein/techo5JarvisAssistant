# Calendars

Two things on a Show show calendar events: the calendar card along the bottom of the Jarvis dashboard
([ha-dashboard](../ha-dashboard/README.md)), and the Show's own calendar page, which opens with a tap
on the date under the clock. Both read from Home Assistant, so the Show never holds a calendar
password. The calendar has to be in Home Assistant first, as a `calendar.*` entity.

If the card says "Keine Termine" for weeks, or the date under the clock does nothing when you tap
it, the calendar either isn't in Home Assistant yet or the card and the Show are pointed at a name
that doesn't exist. The dashboard ships with `calendar.personal` as a placeholder.

## 1. Get the calendar into Home Assistant

Pick the integration for wherever the calendar lives. Each one adds its calendars as entities.

### CalDAV: Nextcloud, iCloud, mailbox.org, Radicale and others

**Settings → Devices & services → Add integration → CalDAV.** It asks for a URL, a user name and a
password.

| Server | URL | Password |
| --- | --- | --- |
| Nextcloud | `https://cloud.example.org/remote.php/dav` | An app password: in Nextcloud, **Settings → Security → Devices & sessions → Create new app password** |
| iCloud | `https://caldav.icloud.com` | An app-specific password from [account.apple.com](https://account.apple.com), **Sign-In and Security → App-Specific Passwords**. Your normal Apple password won't work |
| Anything else | The server's CalDAV address, as a calendar app would use it | Whatever that server gives apps |

Use an app password wherever the server has them. It only opens the calendar, and you can revoke it
without changing your real password.

Leave **Verify SSL** on unless the server uses a certificate you made yourself.

Every calendar on the account turns up as its own entity, named after the calendar: Nextcloud's
"Personal" becomes `calendar.personal`, a "Familie" calendar `calendar.familie`. A calendar shared
with you by someone else shows up too, if your account can see it.

### Other places

- **Google:** the Google Calendar integration. It needs a Google Cloud project, and Home Assistant's
  docs for that integration walk through it.
- **A published ICS link** (Outlook can publish one, and so can most calendar apps): the Remote
  Calendar integration. Read-only, and it refreshes on its own schedule.
- **Nowhere yet:** the Local Calendar integration keeps a calendar inside Home Assistant itself.

## 2. Find the entity id

**Settings → Devices & services → Entities**, search for `calendar.`. Open the calendar and check
that it lists your next event: if Home Assistant can't see it, the Show can't either.

## 3. Point the dashboard card at it

In the Jarvis dashboard, **⋮ → Edit dashboard → ⋮ → Raw configuration editor**, find
`custom:jarvis-calendar-card` and put your entity in:

```yaml
type: custom:jarvis-calendar-card
entity: calendar.personal   # yours
title: Kalender
daysAhead: 14
```

The card shows one calendar: the next event on it, however far off. It looks `daysAhead` days ahead
first, and keeps looking up to a year (`maxDaysAhead`, 366 by default) when those are empty.

## 4. Choose the calendars for the Show's own calendar page

Each Show keeps its own list, so a Show on a desk can show one person's calendar and one in the
kitchen the family's. Choose them in any of these places:

- on the Show: **Settings → General → Calendars**;
- from Home Assistant, with the [`calendar_sources`](actions.md#choose-the-calendars-shown) action:

```yaml
action: esphome.kitchen_show_calendar_sources
data:
  calendars: calendar.personal, calendar.familie
```

With none chosen there is no calendar page, and a tap on the date does nothing. Once one is chosen,
the tap opens the month with the events on their days. Event pop-ups are a separate setting and stay
off until turned on (see [actions.md](actions.md#choose-the-calendars-that-pop-up)).

## A calendar Home Assistant doesn't have

The Show's calendar page can also read a calendar straight from its iCal link, without Home
Assistant: on the Show's setup page, **Weather & Calendar → Calendars from a link**. The page says
where Google, iCloud and Outlook hide that link. Anyone who has the link can read the calendar, so
treat it like a password. This only feeds the Show's own page and pop-ups. The dashboard card still
needs the calendar in Home Assistant.
