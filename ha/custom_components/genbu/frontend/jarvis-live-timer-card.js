/*
 * The timers of the Show this dashboard runs on, counting down, as large
 * as the calendar card and laid out like it, mirrored for the left side:
 * the title and the timer's name, a line, then the time left. More than
 * one timer: the one ringing next is big, the others listed under it.
 * With no timer the card takes no space at all.
 *
 * Which Show that is, Genbu says (genbu/shows, GENBU-164): a Show's
 * dashboard is signed in as the Show's own user, so the one dashboard all
 * Shows share still shows each Show only its own timers. Opened anywhere
 * else, it shows every Show's timers with the Show's name. device_id is
 * only used when Genbu knows no Show at all; the installer no longer
 * writes one.
 *
 * Home Assistant's HassTimerStatus answers with every timer in the house,
 * each with the device it was set on; the card keeps those of its Shows.
 *
 * Every size is a multiple of --u, one pixel of the 1280x800 screen this
 * card was laid out on. On that screen it looks as it always did; on any
 * other it keeps the same proportions, limited by whichever side is
 * shorter.
 */
const SHOWS_EVERY = 300000;  // Shows come and go rarely; timers every 5 s.

class JarvisLiveTimerCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      deviceId: config.device_id || "",
      satelliteId: config.satellite_id || "",
      title: config.title || "Timer"
    };

    this._timers = [];
    this._shows = null;
    this._own = false;
    this._showsAt = 0;
    this._fetchedAt = 0;
    this._loading = false;

    this._render();

    if (!this._clock) {
      this._clock = setInterval(() => this._render(), 250);
    }

    if (!this._poller) {
      this._poller = setInterval(() => this._refreshTimers(), 5000);
    }
  }

  set hass(hass) {
    this._hass = hass;

    if (!this._fetchedAt && !this._loading) {
      this._refreshTimers();
    }
  }

  disconnectedCallback() {
    if (this._clock) {
      clearInterval(this._clock);
      this._clock = null;
    }

    if (this._poller) {
      clearInterval(this._poller);
      this._poller = null;
    }
  }

  _german() {
    const language = this._hass?.locale?.language || this._hass?.language || "de";
    return language.startsWith("de");
  }

  // The Shows whose timers this card shows; [] until Genbu answered.
  async _refreshShows() {
    if (this._shows && Date.now() - this._showsAt < SHOWS_EVERY) {
      return;
    }

    try {
      const answer = await this._hass.callWS({ type: "genbu/shows" });
      this._own = Boolean(answer?.own);
      this._shows = Array.isArray(answer?.shows) ? answer.shows : [];
    } catch (error) {
      console.warn("Jarvis timer: Genbu did not say which Show this is", error);
      this._own = false;
      this._shows = this._shows || [];
    }

    // No Show Genbu knows: the device the card was given, if any.
    if (!this._shows.length && this._config.deviceId) {
      this._own = true;
      this._shows = [{ device: this._config.deviceId, name: "", satellite: this._config.satelliteId }];
    }

    this._showsAt = Date.now();
  }

  async _refreshTimers() {
    if (!this._hass || !this._config || this._loading) {
      return;
    }

    this._loading = true;

    try {
      await this._refreshShows();

      const here = this._shows[0] || {};
      const response = await this._hass.callApi("POST", "intent/handle", {
        name: "HassTimerStatus",
        data: {},
        language: this._german() ? "de" : "en",
        device_id: here.device || null,
        satellite_id: here.satellite || null
      });

      const timers = response?.speech_slots?.timers;
      const names = new Map(this._shows.map(show => [show.device, show.name]));

      // On a Show only its own timers; elsewhere every Show's, with its name.
      // Genbu knowing no Show, or not answering: every timer, rather than none.
      this._timers = (Array.isArray(timers) ? timers : [])
        .filter(timer => !this._shows.length || names.has(timer.device_id))
        .map(timer => ({ ...timer, show: this._own ? "" : names.get(timer.device_id) || "" }));

      // Running ones by time left, paused ones after them.
      this._timers.sort(
        (left, right) =>
          Number(left.is_active === false) - Number(right.is_active === false) ||
          Number(left.total_seconds_left ?? 0) - Number(right.total_seconds_left ?? 0)
      );

      this._fetchedAt = Date.now();
    } catch (error) {
      console.warn("Jarvis timer status failed", error);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _secondsLeft(timer) {
    const base = Number(timer?.total_seconds_left ?? 0);

    if (!this._fetchedAt || timer?.is_active === false) {
      return Math.max(0, base);
    }

    const elapsed = Math.floor((Date.now() - this._fetchedAt) / 1000);
    return Math.max(0, base - elapsed);
  }

  _format(seconds) {
    seconds = Math.max(0, Math.floor(seconds));

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    const two = n => String(n).padStart(2, "0");

    return hours > 0 ? two(hours) + ":" + two(minutes) + ":" + two(secs) : two(minutes) + ":" + two(secs);
  }

  // What a timer is called on the card: its name, else how long it was set for.
  _label(timer) {
    const german = this._german();
    let label = timer.name;

    if (!label) {
      const parts = [];
      if (timer.start_hours) parts.push(timer.start_hours + (german ? " Std." : " h"));
      if (timer.start_minutes) parts.push(timer.start_minutes + " Min.");
      if (timer.start_seconds) parts.push(timer.start_seconds + (german ? " Sek." : " s"));
      label = parts.join(" ") || "Timer";
    }

    if (timer.show) {
      label = timer.show + " · " + label;
    }

    if (timer.is_active === false) {
      label += german ? " (pausiert)" : " (paused)";
    }

    return label;
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  _render() {
    // A timer at 00:00 before the next poll has rung; it leaves the card.
    const timers = this._timers.filter(timer => this._secondsLeft(timer) > 0);
    const timer = timers[0];

    if (!timer) {
      this.style.display = "none";
      this.innerHTML = "";
      return;
    }

    this.style.display = "block";

    const german = this._german();
    const others = timers.slice(1, 4);
    const extra = timers.length - 1 - others.length;

    this.innerHTML = `
      <style>
        jarvis-live-timer-card {
          --u: min(calc(100vw / 1280), calc(100vh / 800));

          width: 100%;

          color: var(--primary-text-color, white);
          font-family: inherit;
        }

        jarvis-live-timer-card .timer {
          /* As wide as its words, on the left: the calendar card mirrored. */
          width: max-content;
          max-width: 100%;
          margin-right: auto;

          display: grid;
          grid-template-columns: max-content 1px minmax(0, max-content);
          grid-template-rows: auto auto;
          column-gap: calc(23 * var(--u));
          row-gap: calc(3 * var(--u));
          align-items: center;

          text-shadow: 0 calc(2 * var(--u)) calc(9 * var(--u)) rgba(0,0,0,.8);
        }

        jarvis-live-timer-card .title {
          grid-column: 1;
          grid-row: 1;
          align-self: end;

          font-size: calc(27.5 * var(--u));
          font-weight: 620;
          letter-spacing: .09em;
          text-transform: uppercase;
          opacity: .72;
          white-space: nowrap;
        }

        jarvis-live-timer-card .name {
          grid-column: 1;
          grid-row: 2;
          align-self: start;
          max-width: calc(360 * var(--u));

          font-size: calc(28.8 * var(--u));
          font-weight: 500;
          opacity: .88;
          overflow-wrap: anywhere;
        }

        jarvis-live-timer-card .divider {
          grid-column: 2;
          grid-row: 1 / span 2;
          align-self: stretch;

          width: 1px;
          min-height: calc(82 * var(--u));

          background: rgba(255,255,255,.55);
          box-shadow: 0 1px 4px rgba(0,0,0,.35);
        }

        jarvis-live-timer-card .remaining {
          grid-column: 3;
          grid-row: 1 / span 2;
          align-self: center;

          font-size: calc(44 * var(--u));
          line-height: 1.12;
          font-weight: 560;
          font-variant-numeric: tabular-nums;
          white-space: nowrap;
        }

        jarvis-live-timer-card .others {
          grid-column: 1 / span 3;
          grid-row: 3;
          margin-top: calc(6 * var(--u));

          font-size: calc(20.8 * var(--u));
          line-height: 1.3;
          font-variant-numeric: tabular-nums;
          opacity: .72;
        }
      </style>

      <div class="timer">
        <div class="title">${this._escape(this._config?.title || "Timer")}</div>
        <div class="name">${this._escape(this._label(timer))}</div>
        <div class="divider"></div>
        <div class="remaining">${this._format(this._secondsLeft(timer))}</div>
        ${others.length ? `<div class="others">
          ${others.map(other =>
            `<div>${this._escape(this._label(other))} · ${this._format(this._secondsLeft(other))}</div>`
          ).join("")}
          ${extra > 0 ? `<div>+${extra} ${german ? "weitere" : "more"}</div>` : ""}
        </div>` : ""}
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
    if (!window.customElements.get("jarvis-live-timer-card")) {
      window.customElements.define("jarvis-live-timer-card", JarvisLiveTimerCard);
    }
  };
  defineCard();
  customElements.whenDefined("home-assistant").then(defineCard);
}
