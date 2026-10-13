/*
 * The timer running out soonest on one voice satellite, counting down.
 * With no timer running the card takes no space at all.
 *
 * Every size is a multiple of --u, one pixel of the 1280x800 screen this
 * card was laid out on. On that screen it looks as it always did; on any
 * other it keeps the same proportions, limited by whichever side is
 * shorter.
 */
class JarvisLiveTimerCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      deviceId: config.device_id,
      satelliteId: config.satellite_id
    };

    if (!this._config.deviceId) {
      throw new Error("device_id required");
    }

    this._timers = [];
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

  async _refreshTimers() {
    if (!this._hass || !this._config || this._loading) {
      return;
    }

    this._loading = true;

    try {
      const response = await this._hass.callApi("POST", "intent/handle", {
        name: "HassTimerStatus",
        data: {},
        language: "de",
        device_id: this._config.deviceId,
        satellite_id: this._config.satelliteId
      });

      const timers = response?.speech_slots?.timers;

      this._timers = Array.isArray(timers)
        ? timers.filter(timer => timer.is_active !== false)
        : [];

      this._timers.sort(
        (left, right) =>
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

    if (!this._fetchedAt) {
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

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  _render() {
    const timer = this._timers[0];
    const seconds = timer ? this._secondsLeft(timer) : 0;

    // No timer, or one that reached 00:00 before the next poll: nothing on screen.
    if (!timer || seconds <= 0) {
      this.style.display = "none";
      this.innerHTML = "";
      return;
    }

    this.style.display = "block";

    const name = timer.name || "Timer";
    const extra = Math.max(0, this._timers.length - 1);

    this.innerHTML = `
      <style>
        jarvis-live-timer-card {
          --u: min(calc(100vw / 1280), calc(100vh / 800));

          width: 100%;

          color: var(--primary-text-color, white);
          font-family: inherit;
        }

        jarvis-live-timer-card .timer {
          width: max-content;
          max-width: 100%;
          margin-right: auto;
          text-align: left;

          text-shadow: 0 calc(2 * var(--u)) calc(9 * var(--u)) rgba(0,0,0,.8);
        }

        jarvis-live-timer-card .name {
          max-width: calc(300 * var(--u));

          font-size: calc(20.8 * var(--u));
          line-height: 1.12;
          font-weight: 620;

          overflow-wrap: anywhere;
        }

        jarvis-live-timer-card .remaining {
          margin-top: calc(5 * var(--u));

          font-size: calc(35.8 * var(--u));
          line-height: 1;
          font-weight: 590;
          font-variant-numeric: tabular-nums;
        }

        jarvis-live-timer-card .more {
          margin-top: calc(4 * var(--u));

          font-size: calc(12.5 * var(--u));
          opacity: .55;
        }
      </style>

      <div class="timer">
        <div class="name">${this._escape(name)}</div>
        <div class="remaining">${this._format(seconds)}</div>
        ${extra > 0 ? `<div class="more">+${extra} weitere</div>` : ""}
      </div>
    `;
  }

  getCardSize() {
    return 2;
  }
}

if (!customElements.get("jarvis-live-timer-card")) {
  customElements.define("jarvis-live-timer-card", JarvisLiveTimerCard);
}
