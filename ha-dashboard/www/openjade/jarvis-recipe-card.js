/*
 * The recipe Jarvis last looked up (sensor.jarvis_recipe, from the
 * jarvis_recipe integration): the dish and where it is from along the top,
 * the ingredients on the left, the steps on the right, numbered. Big enough
 * to read from the stove.
 *
 * entity is the sensor, sensor.jarvis_recipe unless you renamed it.
 *
 * Every size is a multiple of --u, one pixel of the 1280x800 screen this
 * card was laid out on, limited by whichever side of the screen is
 * shorter. The card fills the screen below Home Assistant's top bar, or
 * all of it when the Show hides the bar; a long recipe scrolls in its own
 * column, ingredients and steps each on their own.
 */
class JarvisRecipeCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: config.entity || "sensor.jarvis_recipe"
    };
    this._marker = undefined;
    this._render();
  }

  connectedCallback() {
    this._onResize = this._onResize || (() => this._fit());
    window.addEventListener("resize", this._onResize);
    this._fit();
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._onResize);
  }

  // As tall as what is left of the window below where the card starts.
  _fit() {
    requestAnimationFrame(() => {
      const top = this.getBoundingClientRect().top + window.scrollY;
      this.style.height = Math.max(240, window.innerHeight - top) + "px";
    });
  }

  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this._config?.entity];
    const marker = state ? state.state + "|" + state.last_updated : "-";
    if (marker !== this._marker) {
      this._marker = marker;
      this._render();
    }
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  _list(value) {
    return Array.isArray(value) ? value.filter((item) => typeof item === "string" && item) : [];
  }

  // Only a web address goes into the picture's src.
  _image(value) {
    try {
      const url = new URL(String(value || ""));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (e) {
      return "";
    }
  }

  _facts(attributes) {
    const facts = [];
    if (attributes.servings) {
      facts.push(String(attributes.servings));
    }
    const minutes = Number(attributes.minutes);
    if (minutes > 0) {
      facts.push(minutes >= 60
        ? Math.floor(minutes / 60) + " Std" + (minutes % 60 ? " " + (minutes % 60) + " Min" : "")
        : minutes + " Min");
    }
    if (attributes.source) {
      facts.push(String(attributes.source));
    }
    return facts;
  }

  _render() {
    if (!this._config) {
      return;
    }
    const state = this._hass?.states[this._config.entity];
    const empty = !state || ["unknown", "unavailable", ""].includes(state.state);
    const attributes = state?.attributes || {};
    const ingredients = this._list(attributes.ingredients);
    const steps = this._list(attributes.steps);
    const image = this._image(attributes.image);

    let body;
    if (!this._hass) {
      body = `<div class="empty">Lade…</div>`;
    } else if (!state) {
      body = `<div class="empty">${this._escape(this._config.entity)} fehlt<small>Ist Jarvis Rezepte eingerichtet?</small></div>`;
    } else if (empty) {
      body = `<div class="empty">Noch kein Rezept<small>Frag Jarvis nach einem, etwa „Wie mache ich Pfannkuchen?“</small></div>`;
    } else {
      body = `
        <header>
          ${image ? `<img src="${this._escape(image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
          <div class="head">
            <h1>${this._escape(state.state)}</h1>
            <div class="facts">${this._facts(attributes).map((f) => `<span>${this._escape(f)}</span>`).join("")}</div>
          </div>
        </header>
        <div class="columns">
          <section class="ingredients">
            <h2>Zutaten</h2>
            <ul>${ingredients.map((i) => `<li>${this._escape(i)}</li>`).join("")}</ul>
          </section>
          <section class="steps">
            <h2>Zubereitung</h2>
            <ol>${steps.map((s) => `<li>${this._escape(s)}</li>`).join("")}</ol>
          </section>
        </div>`;
    }

    this.innerHTML = `
      <style>
        jarvis-recipe-card {
          --u: min(calc(100vw / 1280), calc(100vh / 800));

          box-sizing: border-box;
          width: 100%;
          height: 100vh;
          padding: calc(28 * var(--u)) calc(36 * var(--u));
          overflow: hidden;

          display: flex;
          flex-direction: column;
          gap: calc(20 * var(--u));

          background: radial-gradient(ellipse at top left, #2b2f36, #121418 70%);
          color: var(--primary-text-color, white);
          font-family: inherit;
        }

        jarvis-recipe-card * {
          box-sizing: border-box;
        }

        jarvis-recipe-card header {
          display: flex;
          align-items: center;
          gap: calc(24 * var(--u));
          flex: none;
        }

        jarvis-recipe-card img {
          width: calc(150 * var(--u));
          height: calc(110 * var(--u));
          object-fit: cover;
          border-radius: calc(14 * var(--u));
          flex: none;
        }

        jarvis-recipe-card .head {
          min-width: 0;
        }

        jarvis-recipe-card h1 {
          margin: 0;
          font-size: calc(50 * var(--u));
          line-height: 1.08;
          font-weight: 600;

          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        jarvis-recipe-card .facts {
          margin-top: calc(8 * var(--u));
          display: flex;
          flex-wrap: wrap;
          gap: calc(8 * var(--u)) calc(26 * var(--u));
          font-size: calc(26 * var(--u));
          opacity: .72;
        }

        jarvis-recipe-card .columns {
          flex: 1;
          min-height: 0;
          display: grid;
          grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
          gap: calc(36 * var(--u));
        }

        jarvis-recipe-card section {
          min-height: 0;
          overflow-y: auto;
          overscroll-behavior: contain;
          scrollbar-width: none;
          padding: 0 calc(8 * var(--u)) calc(44 * var(--u)) 0;
          /* The bottom fades out, so a recipe that goes on below looks it. */
          -webkit-mask-image: linear-gradient(to bottom, black calc(100% - 44 * var(--u)), transparent);
          mask-image: linear-gradient(to bottom, black calc(100% - 44 * var(--u)), transparent);
        }

        jarvis-recipe-card section::-webkit-scrollbar {
          display: none;
        }

        jarvis-recipe-card h2 {
          margin: 0 0 calc(12 * var(--u));
          font-size: calc(24 * var(--u));
          font-weight: 620;
          letter-spacing: .09em;
          text-transform: uppercase;
          opacity: .6;
        }

        jarvis-recipe-card ul,
        jarvis-recipe-card ol {
          margin: 0;
          padding: 0;
          list-style: none;
        }

        jarvis-recipe-card li {
          font-size: calc(30 * var(--u));
          line-height: 1.28;
          overflow-wrap: anywhere;
        }

        jarvis-recipe-card .ingredients li {
          padding: calc(9 * var(--u)) 0;
          border-bottom: 1px solid rgba(255,255,255,.14);
        }

        jarvis-recipe-card .steps ol {
          counter-reset: step;
        }

        jarvis-recipe-card .steps li {
          counter-increment: step;
          position: relative;
          padding: 0 0 calc(18 * var(--u)) calc(58 * var(--u));
        }

        jarvis-recipe-card .steps li::before {
          content: counter(step);
          position: absolute;
          left: 0;
          top: calc(1 * var(--u));
          width: calc(40 * var(--u));
          height: calc(40 * var(--u));
          border-radius: 50%;
          background: rgba(255,255,255,.16);
          font-size: calc(24 * var(--u));
          font-weight: 620;
          line-height: calc(40 * var(--u));
          text-align: center;
        }

        jarvis-recipe-card .empty {
          margin: auto;
          text-align: center;
          font-size: calc(44 * var(--u));
          font-weight: 560;
        }

        jarvis-recipe-card .empty small {
          display: block;
          margin-top: calc(14 * var(--u));
          font-size: calc(26 * var(--u));
          font-weight: 400;
          opacity: .7;
        }
      </style>
      ${body}
    `;
    this._fit();
  }

  getCardSize() {
    return 10;
  }
}

if (!customElements.get("jarvis-recipe-card")) {
  customElements.define("jarvis-recipe-card", JarvisRecipeCard);
}
