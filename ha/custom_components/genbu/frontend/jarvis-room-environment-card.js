/*
 * Room temperature and humidity, as a glass panel.
 *
 * Every size is a multiple of --u, one pixel of the 1280x800 screen this
 * card was laid out on. On that screen it looks as it always did; on any
 * other it keeps the same proportions, limited by whichever side is
 * shorter, so it never crowds the clock on a wide or a small panel. It is
 * as wide as the weather card in the other corner, so the two sit level
 * and keep the same distance from the clock.
 *
 * Narrower than 294px (a 5" Show, a phone) the words go: no title, an icon
 * for each row, and bigger numbers. The card decides from its own width, so
 * the same config works everywhere and a resized window switches live.
 */
class JarvisRoomEnvironmentCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      temperature: config.temperature_entity,
      humidity: config.humidity_entity,
      title: config.title || "Wohnzimmer"
    };

    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _state(entity) {
    return this._hass?.states?.[entity];
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  _render() {
    const temperature = this._state(this._config?.temperature);
    const humidity = this._state(this._config?.humidity);

    const tempValue = temperature?.state ?? "—";
    const tempUnit = temperature?.attributes?.unit_of_measurement ?? "";
    const humidityValue = humidity?.state ?? "—";
    const humidityUnit = humidity?.attributes?.unit_of_measurement ?? "";

    this.innerHTML = `
      <style>
        jarvis-room-environment-card {
          --u: min(calc(100vw / 1280), calc(100vh / 800));

          display: block;
          width: calc(350 * var(--u)) !important;
          max-width: 100%;
          box-sizing: border-box;
          container-type: inline-size;

          color: var(--primary-text-color, white);
          font-family: inherit;
        }

        jarvis-room-environment-card .glass {
          box-sizing: border-box;
          width: 100%;
          overflow: hidden;

          padding: calc(14 * var(--u)) calc(16 * var(--u));
          border-radius: calc(10 * var(--u));

          background: rgba(255,255,255,.11);
          border: 1px solid rgba(255,255,255,.23);
          box-shadow: 0 calc(6 * var(--u)) calc(20 * var(--u)) rgba(0,0,0,.16);
          backdrop-filter: blur(calc(7 * var(--u)));
          text-shadow: 0 calc(2 * var(--u)) calc(8 * var(--u)) rgba(0,0,0,.6);
        }

        jarvis-room-environment-card .title {
          margin-bottom: calc(7 * var(--u));

          font-size: calc(13.1 * var(--u));
          font-weight: 620;
          letter-spacing: .10em;
          text-transform: uppercase;
          opacity: .64;
        }

        jarvis-room-environment-card .row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
          gap: calc(18 * var(--u));

          min-height: calc(38 * var(--u));
          padding: calc(9 * var(--u)) 0;
        }

        jarvis-room-environment-card .row + .row {
          border-top: 1px solid rgba(255,255,255,.18);
          margin-top: calc(5 * var(--u));
        }

        jarvis-room-environment-card .label {
          min-width: 0;

          font-size: calc(24.5 * var(--u));
          line-height: 1.15;
          font-weight: 500;
        }

        jarvis-room-environment-card .value {
          white-space: nowrap;

          font-size: calc(30.7 * var(--u));
          line-height: 1;
          font-weight: 650;
          font-variant-numeric: tabular-nums;
          text-align: right;
        }

        jarvis-room-environment-card .unit {
          font-size: .78em;
          opacity: .7;
        }

        jarvis-room-environment-card .icon {
          display: none;
          --mdc-icon-size: calc(46 * var(--u));
          opacity: .8;
        }

        @container (max-width: 293px) {
          jarvis-room-environment-card .title,
          jarvis-room-environment-card .label {
            display: none;
          }

          jarvis-room-environment-card .icon {
            display: block;
          }

          jarvis-room-environment-card .row {
            padding: calc(6 * var(--u)) 0;
          }

          jarvis-room-environment-card .value {
            font-size: calc(42 * var(--u));
          }
        }
      </style>

      <div class="glass">
        <div class="title">${this._escape(this._config?.title)}</div>

        <div class="row">
          <ha-icon class="icon" icon="mdi:thermometer"></ha-icon>
          <div class="label">Temperatur</div>
          <div class="value">
            ${this._escape(tempValue)}
            <span class="unit">${this._escape(tempUnit)}</span>
          </div>
        </div>

        <div class="row">
          <ha-icon class="icon" icon="mdi:water-percent"></ha-icon>
          <div class="label">Luftfeuchtigkeit</div>
          <div class="value">
            ${this._escape(humidityValue)}
            <span class="unit">${this._escape(humidityUnit)}</span>
          </div>
        </div>
      </div>
    `;
  }

  getCardSize() {
    return 2;
  }
}

if (!customElements.get("jarvis-room-environment-card")) {
  customElements.define("jarvis-room-environment-card", JarvisRoomEnvironmentCard);
}
