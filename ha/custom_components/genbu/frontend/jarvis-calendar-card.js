/*
 * The next calendar event, large, with the calendar's name and the event's
 * day beside it. However far off it is: the card looks two weeks ahead
 * first, and further when those are empty, up to a year.
 *
 * entity is one calendar, entities a list of them (the next event of any).
 * A calendar Home Assistant doesn't have, or can't read, is said as such
 * rather than as "Keine Termine": a calendar renamed or removed used to
 * look like an empty week.
 *
 * Every size is a multiple of --u, one pixel of the 1280x800 screen this
 * card was laid out on. On that screen it looks as it always did; on any
 * other it keeps the same proportions, limited by whichever side is
 * shorter.
 */
class JarvisCalendarCard extends HTMLElement {
  setConfig(config) {
    const entities = [].concat(config.entities || [], config.entity || []).filter(Boolean);
    if (!entities.length) {
      throw new Error("calendar entity required");
    }

    this._config = {
      entities: [...new Set(entities)],
      daysAhead: Number(config.daysAhead || 14),
      // Where it stops looking. daysAhead used to be both, which hid an
      // appointment three weeks out behind "Keine Termine".
      maxDaysAhead: Math.max(Number(config.maxDaysAhead || 366), Number(config.daysAhead || 14)),
      title: config.title || "Kalender"
    };

    this._events = [];
    this._loading = true;

    this._render();

    if (!this._timer) {
      this._timer = setInterval(() => this._load(), 60000);
    }
  }

  set hass(hass) {
    this._hass = hass;

    const marker = (this._config?.entities || []).map((entity) => {
      const state = hass.states[entity];
      return state ? state.state + "|" + state.last_updated : "-";
    }).join(",");

    if (marker !== this._marker) {
      this._marker = marker;
      this._load();
    }
  }

  disconnectedCallback() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  async _load() {
    if (!this._hass || !this._config) {
      return;
    }

    const there = this._config.entities.filter((entity) => this._hass.states[entity]);
    this._missing = this._config.entities.filter((entity) => !this._hass.states[entity]);
    this._unreadable = false;
    this._events = [];
    this._loading = false;
    if (!there.length) {
      this._render();
      return;
    }

    // A short look first, as most days there is something soon, and a
    // year of a busy calendar is a big answer to ask for every minute.
    const start = new Date();
    for (const days of this._horizons()) {
      const end = new Date(start.getTime() + days * 86400000);
      const query = new URLSearchParams({
        start: start.toISOString(),
        end: end.toISOString()
      }).toString();

      const answers = await Promise.allSettled(there.map((entity) =>
        this._hass.callApi("GET", "calendars/" + encodeURIComponent(entity) + "?" + query)
      ));
      if (answers.every((a) => a.status === "rejected")) {
        this._unreadable = true;
        break;
      }

      const events = answers.flatMap((a) =>
        a.status === "fulfilled" && Array.isArray(a.value) ? a.value : []
      );
      events.sort((a, b) => this._start(a) - this._start(b));
      if (events.length) {
        this._events = events;
        break;
      }
    }

    this._render();
  }

  // daysAhead, then a quarter, then maxDaysAhead: the windows the card
  // asks for in turn until one has an event.
  _horizons() {
    const first = this._config.daysAhead;
    const last = this._config.maxDaysAhead;
    return [...new Set([first, Math.min(Math.max(first, 92), last), last])];
  }

  _start(event) {
    const value = new Date(event?.start?.dateTime || event?.start?.date || 0).getTime();
    return Number.isNaN(value) ? 0 : value;
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  _when(event) {
    const raw = event?.start?.dateTime || event?.start?.date;
    if (!raw) {
      return "";
    }

    const value = new Date(raw);
    if (Number.isNaN(value.getTime())) {
      return raw;
    }

    if (event?.start?.date) {
      return new Intl.DateTimeFormat("de-DE", {
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        timeZone: "Europe/Berlin"
      }).format(value);
    }

    return new Intl.DateTimeFormat("de-DE", {
      weekday: "short",
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Europe/Berlin"
    }).format(value);
  }

  _render() {
    const event = this._events[0];
    let eventName = this._loading ? "Lade…" : event?.summary || "Keine Termine";
    let date = event ? this._when(event) : "";
    if (!this._loading && !event) {
      // Only when nothing else is there to show: one calendar of three
      // gone still leaves the others' next event on the card.
      if (this._missing?.length === this._config.entities.length) {
        eventName = "Kalender fehlt";
        date = this._missing.join(", ");
      } else if (this._unreadable) {
        eventName = "Kalender nicht erreichbar";
      }
    }

    this.innerHTML = `
      <style>
        jarvis-calendar-card {
          --u: min(calc(100vw / 1280), calc(100vh / 800));

          display: block;
          width: 100%;

          color: var(--primary-text-color, white);
          font-family: inherit;
        }

        jarvis-calendar-card .calendar {
          /* As wide as its words, pushed to the right. */
          width: max-content;
          max-width: 100%;
          margin-left: auto;

          display: grid;
          grid-template-columns: minmax(calc(180 * var(--u)), calc(440 * var(--u))) 1px max-content;
          grid-template-rows: auto auto;
          column-gap: calc(23 * var(--u));
          row-gap: calc(3 * var(--u));
          align-items: center;

          text-shadow: 0 calc(2 * var(--u)) calc(9 * var(--u)) rgba(0,0,0,.8);
        }

        jarvis-calendar-card .event-name {
          grid-column: 1;
          grid-row: 1 / span 2;
          align-self: center;
          min-width: 0;
          text-align: right;

          font-size: calc(44 * var(--u));
          line-height: 1.12;
          font-weight: 560;

          overflow-wrap: anywhere;
        }

        jarvis-calendar-card .divider {
          grid-column: 2;
          grid-row: 1 / span 2;
          align-self: stretch;

          width: 1px;
          min-height: calc(82 * var(--u));

          background: rgba(255,255,255,.55);
          box-shadow: 0 1px 4px rgba(0,0,0,.35);
        }

        jarvis-calendar-card .title {
          grid-column: 3;
          grid-row: 1;
          align-self: end;

          font-size: calc(27.5 * var(--u));
          font-weight: 620;
          letter-spacing: .09em;
          text-transform: uppercase;
          opacity: .72;
          white-space: nowrap;
        }

        jarvis-calendar-card .date {
          grid-column: 3;
          grid-row: 2;
          align-self: start;

          font-size: calc(28.8 * var(--u));
          font-weight: 500;
          opacity: .88;
          white-space: nowrap;
        }
      </style>

      <div class="calendar">
        <div class="event-name">${this._escape(eventName)}</div>
        <div class="divider"></div>
        <div class="title">${this._escape(this._config?.title || "Kalender")}</div>
        <div class="date">${this._escape(date)}</div>
      </div>
    `;
  }

  getCardSize() {
    return 2;
  }
}

// Genbu loads this before Home Assistant's app, which then puts its own
// registry (the scoped-elements polyfill) in place of window.customElements.
// A card defined only before that is "Custom element doesn't exist"; which
// cards it hit depended on load order. So it is defined again once the app
// has defined <home-assistant>, into whatever registry is there by then.
{
  const defineCard = () => {
    if (!window.customElements.get("jarvis-calendar-card")) {
      window.customElements.define("jarvis-calendar-card", JarvisCalendarCard);
    }
  };
  defineCard();
  customElements.whenDefined("home-assistant").then(defineCard);
}
