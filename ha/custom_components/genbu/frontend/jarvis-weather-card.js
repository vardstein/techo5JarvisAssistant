/*
 * Current weather, as a glass panel: the condition and temperature, then
 * humidity and wind. The daily forecast was hidden on this dashboard, so
 * it is no longer fetched; forecast_days is accepted and ignored.
 *
 * Every size is a multiple of --u, one pixel of the 1280x800 screen this
 * card was laid out on. On that screen it looks as it always did; on any
 * other it keeps the same proportions, limited by whichever side is
 * shorter.
 *
 * Narrower than 245px (a 5" Show, a phone) the words go: the condition's
 * icon and the temperature, then humidity and wind with an icon each. The
 * card decides from its own width, so the same config works everywhere and
 * a resized window switches live.
 */
class JarvisWeatherCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: config.entity,
      title: config.title || "Wetter"
    };

    this._render();
  }

  set hass(hass) {
    this._hass = hass;

    const weather = hass.states[this._config?.entity];
    const marker = weather ? weather.state + "|" + weather.last_updated : "";

    if (marker !== this._marker) {
      this._marker = marker;
      this._render();
    }
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  _icon(condition) {
    const icons = {
      "clear-night": "mdi:weather-night",
      "cloudy": "mdi:weather-cloudy",
      "fog": "mdi:weather-fog",
      "hail": "mdi:weather-hail",
      "lightning": "mdi:weather-lightning",
      "lightning-rainy": "mdi:weather-lightning-rainy",
      "partlycloudy": "mdi:weather-partly-cloudy",
      "pouring": "mdi:weather-pouring",
      "rainy": "mdi:weather-rainy",
      "snowy": "mdi:weather-snowy",
      "snowy-rainy": "mdi:weather-snowy-rainy",
      "sunny": "mdi:weather-sunny",
      "windy": "mdi:weather-windy",
      "windy-variant": "mdi:weather-windy-variant"
    };

    return icons[condition] || "mdi:weather-cloudy";
  }

  _condition(condition) {
    const names = {
      "clear-night": "Klare Nacht",
      "cloudy": "Bewölkt",
      "fog": "Nebel",
      "hail": "Hagel",
      "lightning": "Gewitter",
      "lightning-rainy": "Gewitterregen",
      "partlycloudy": "Teilweise bewölkt",
      "pouring": "Starkregen",
      "rainy": "Regen",
      "snowy": "Schnee",
      "snowy-rainy": "Schneeregen",
      "sunny": "Sonnig",
      "windy": "Windig",
      "windy-variant": "Windig"
    };

    return names[condition] || condition || "Unbekannt";
  }

  _render() {
    const weather = this._hass?.states?.[this._config?.entity];
    const attrs = weather?.attributes || {};

    const condition = weather?.state || "";
    const temperature = attrs.temperature ?? "—";
    const temperatureUnit = attrs.temperature_unit || "°C";
    const apparent = attrs.apparent_temperature;
    const humidity = attrs.humidity;
    const wind = attrs.wind_speed;
    const windUnit = attrs.wind_speed_unit || "";

    this.innerHTML = `
      <style>
        jarvis-weather-card {
          --u: min(calc(100vw / 1280), calc(100vh / 800));

          display: block;
          width: calc(350 * var(--u)) !important;
          max-width: 100%;
          box-sizing: border-box;
          container-type: inline-size;

          color: var(--primary-text-color, white);
          font-family: inherit;
        }

        jarvis-weather-card .glass {
          box-sizing: border-box;
          width: 100%;
          overflow: hidden;

          padding: calc(14 * var(--u)) calc(16 * var(--u)) calc(12 * var(--u));
          border-radius: calc(10 * var(--u));

          background: rgba(255,255,255,.11);
          border: 1px solid rgba(255,255,255,.23);
          box-shadow: 0 calc(6 * var(--u)) calc(20 * var(--u)) rgba(0,0,0,.16);
          backdrop-filter: blur(calc(7 * var(--u)));
          text-shadow: 0 calc(2 * var(--u)) calc(8 * var(--u)) rgba(0,0,0,.6);
        }

        jarvis-weather-card .title {
          margin-bottom: calc(8 * var(--u));

          font-size: calc(19.2 * var(--u));
          line-height: 1.15;
          font-weight: 620;
          letter-spacing: .10em;
          text-transform: uppercase;
          opacity: .64;
        }

        jarvis-weather-card .current {
          display: grid;
          grid-template-columns: calc(42 * var(--u)) minmax(0, 1fr) auto;
          align-items: center;
          column-gap: calc(11 * var(--u));
          min-width: 0;
        }

        jarvis-weather-card .current ha-icon {
          --mdc-icon-size: calc(40 * var(--u));
        }

        /* German conditions such as "Bewölkt" stay on one line; the panel
           is sized to give them the room. */
        jarvis-weather-card .condition-block {
          min-width: 0;
          white-space: nowrap;
        }

        jarvis-weather-card .condition {
          font-size: calc(20.5 * var(--u));
          line-height: 1.2;
          font-weight: 540;
        }

        jarvis-weather-card .apparent {
          margin-top: calc(2 * var(--u));

          font-size: calc(12.2 * var(--u));
          opacity: .60;
        }

        jarvis-weather-card .temperature {
          white-space: nowrap;

          font-size: calc(30.4 * var(--u));
          line-height: 1;
          font-weight: 660;
          font-variant-numeric: tabular-nums;
        }

        jarvis-weather-card .metric {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
          gap: calc(10 * var(--u));

          min-height: calc(29 * var(--u));
        }

        jarvis-weather-card .metric + .metric {
          margin-top: calc(3 * var(--u));
          padding-top: calc(3 * var(--u));
        }

        jarvis-weather-card .metric-label {
          min-width: 0;

          font-size: calc(16.3 * var(--u));
          line-height: 1.2;
          font-weight: 520;
          letter-spacing: .035em;
          text-transform: uppercase;
          opacity: .58;

          overflow-wrap: anywhere;
        }

        jarvis-weather-card .metric-value {
          white-space: nowrap;

          font-size: calc(18.6 * var(--u));
          line-height: 1.2;
          font-weight: 590;
        }

        jarvis-weather-card .metric-icon {
          display: none;
          --mdc-icon-size: calc(38 * var(--u));
          opacity: .8;
        }

        @container (max-width: 244px) {
          jarvis-weather-card .title,
          jarvis-weather-card .condition-block,
          jarvis-weather-card .metric-label {
            display: none;
          }

          jarvis-weather-card .metric-icon {
            display: block;
          }

          jarvis-weather-card .current {
            grid-template-columns: auto minmax(0, 1fr);
            margin-bottom: calc(4 * var(--u));
          }

          jarvis-weather-card .current ha-icon {
            --mdc-icon-size: calc(54 * var(--u));
          }

          jarvis-weather-card .temperature {
            font-size: calc(44 * var(--u));
            text-align: right;
          }

          jarvis-weather-card .metric {
            min-height: calc(42 * var(--u));
          }

          jarvis-weather-card .metric-value {
            font-size: calc(32 * var(--u));
          }
        }
      </style>

      <div class="glass">
        <div class="title">${this._escape(this._config?.title)}</div>

        <div class="current">
          <ha-icon icon="${this._icon(condition)}"></ha-icon>

          <div class="condition-block">
            <div class="condition">${this._escape(this._condition(condition))}</div>
            ${
              apparent !== undefined
                ? `<div class="apparent">Gefühlt ${this._escape(apparent)}${this._escape(temperatureUnit)}</div>`
                : ""
            }
          </div>

          <div class="temperature">${this._escape(temperature)}${this._escape(temperatureUnit)}</div>
        </div>

        <div class="metric">
          <ha-icon class="metric-icon" icon="mdi:water-percent"></ha-icon>
          <div class="metric-label">Luftfeuchtigkeit</div>
          <div class="metric-value">
            ${humidity !== undefined ? this._escape(humidity) + " %" : "—"}
          </div>
        </div>

        <div class="metric">
          <ha-icon class="metric-icon" icon="mdi:weather-windy"></ha-icon>
          <div class="metric-label">Wind</div>
          <div class="metric-value">
            ${wind !== undefined ? this._escape(wind) + " " + this._escape(windUnit) : "—"}
          </div>
        </div>
      </div>
    `;
  }

  getCardSize() {
    return 3;
  }
}

if (!customElements.get("jarvis-weather-card")) {
  customElements.define("jarvis-weather-card", JarvisWeatherCard);
}
