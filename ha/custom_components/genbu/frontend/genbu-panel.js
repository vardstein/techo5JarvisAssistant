// The Genbu page in the sidebar: one card per Genbu Show. Everything on it is the Show's own entities
// (its ESPHome device, the devices under it and its Genbu settings device), so nothing is copied and a
// renamed or hidden entity looks the same here as anywhere else in Home Assistant. Rows open the usual
// more-info dialog. The text is English only, like the entity names the Shows report.

// Controls first, in this order; anything else after them, then the settings.
const ORDER = ["media_player", "assist_satellite", "light", "switch", "select", "number", "button",
  "sensor", "binary_sensor", "update"];
const SKIP = new Set(["camera", "event"]);

class GenbuPanel extends HTMLElement {
  constructor() {
    super();
    this._cards = [];
    this._key = "";
    this.attachShadow({ mode: "open" }).innerHTML = `
      <style>
        :host { display: block; min-height: 100%; background: var(--primary-background-color); }
        header { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px);
          padding: 0 12px; box-sizing: border-box; background: var(--app-header-background-color);
          color: var(--app-header-text-color, var(--text-primary-color));
          border-bottom: var(--app-header-border-bottom, none); font-size: 20px; }
        header button { display: none; background: none; border: 0; color: inherit; padding: 8px;
          cursor: pointer; border-radius: 50%; }
        :host([narrow]) header button { display: inline-flex; }
        main { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px;
          padding: 16px; align-items: start; }
        .empty { padding: 32px 16px; color: var(--secondary-text-color); }
        .row { display: flex; justify-content: space-between; gap: 12px; padding: 8px 16px; cursor: pointer; }
        .row span:last-child { color: var(--secondary-text-color); text-align: right; }
        .section { padding: 12px 16px 4px; font-weight: 500; color: var(--secondary-text-color); }
        ha-card h2 { margin: 0; padding: 16px 16px 8px; font-size: 20px; font-weight: 400; }
      </style>
      <header><button title="Menu" aria-label="Menu"><ha-icon icon="mdi:menu"></ha-icon></button>Genbu</header>
      <main></main>`;
    this.shadowRoot.querySelector("button").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    this.addEventListener("ll-rebuild", (ev) => { ev.stopPropagation(); this._key = ""; this._render(); });
  }

  set narrow(value) { this.toggleAttribute("narrow", !!value); }

  set panel(value) { this._panel = value; this._render(); }

  set hass(value) {
    this._hass = value;
    if (this._plainRows) this._key = ""; // plain rows show states, so they are drawn again
    this._render();
    for (const card of this._cards) card.hass = value;
  }

  // The Shows and what goes on each card, worked out from the registries Home Assistant already sends.
  _shows() {
    const hass = this._hass;
    const makers = new Set(this._panel?.config?.manufacturers ?? ["OpenJade"]);
    const devices = Object.values(hass.devices ?? {});
    const shows = devices.filter((d) => makers.has(d.manufacturer) && !d.via_device_id)
      .sort((a, b) => name(a).localeCompare(name(b)));
    return shows.map((show) => {
      const own = new Set([show.id, ...devices.filter((d) => d.via_device_id === show.id).map((d) => d.id)]);
      const rows = Object.values(hass.entities ?? {})
        .filter((e) => own.has(e.device_id) && !e.hidden && e.entity_category !== "diagnostic"
          && !SKIP.has(domain(e.entity_id)) && hass.states[e.entity_id])
        .map((e) => ({ entity: e.entity_id, name: label(hass, e, name(show)), config: e.entity_category === "config" }))
        .sort((a, b) => rank(a.entity) - rank(b.entity) || a.name.localeCompare(b.name));
      return { id: show.id, title: name(show), rows };
    });
  }

  async _render() {
    if (!this._hass || !this._panel) return;
    const shows = this._shows();
    const key = JSON.stringify(shows);
    if (key === this._key) return;
    this._key = key;
    const main = this.shadowRoot.querySelector("main");
    if (!shows.length) {
      this._cards = [];
      main.innerHTML = `<div class="empty">No Genbu Show found. Add one with the installer, or in
        Settings › Devices &amp; services › ESPHome.</div>`;
      return;
    }
    const helpers = await cardHelpers();
    if (key !== this._key) return; // the house changed while the helpers loaded
    this._plainRows = !helpers;
    this._cards = shows.map((show) => (helpers ? this._card(helpers, show) : this._plain(show)));
    main.replaceChildren(...this._cards);
  }

  _card(helpers, show) {
    const controls = show.rows.filter((r) => !r.config).map(({ entity, name }) => ({ entity, name }));
    const settings = show.rows.filter((r) => r.config).map(({ entity, name }) => ({ entity, name }));
    const card = helpers.createCardElement({
      type: "entities", title: show.title, state_color: true,
      entities: [
        ...controls,
        ...(settings.length ? [{ type: "section", label: "Settings" }, ...settings] : []),
        { type: "weblink", name: "Device page", icon: "mdi:devices", url: `/config/devices/device/${show.id}` },
      ],
    });
    card.hass = this._hass;
    return card;
  }

  // Without Home Assistant's card helpers: the same rows as plain text, each opening more-info.
  _plain(show) {
    const card = document.createElement("ha-card");
    const title = document.createElement("h2");
    title.textContent = show.title;
    card.append(title);
    let settings = false;
    for (const row of show.rows) {
      if (row.config && !settings) {
        settings = true;
        const head = document.createElement("div");
        head.className = "section";
        head.textContent = "Settings";
        card.append(head);
      }
      const line = document.createElement("div");
      line.className = "row";
      const left = document.createElement("span");
      const right = document.createElement("span");
      left.textContent = row.name;
      const state = this._hass.states[row.entity];
      right.textContent = this._hass.formatEntityState?.(state) ?? state.state;
      line.append(left, right);
      line.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-more-info",
        { detail: { entityId: row.entity }, bubbles: true, composed: true })));
      card.append(line);
    }
    return card;
  }
}

function name(device) { return device.name_by_user || device.name || ""; }

function domain(entityId) { return entityId.split(".", 1)[0]; }

function rank(entityId) {
  const at = ORDER.indexOf(domain(entityId));
  return at < 0 ? ORDER.length : at;
}

// The row name without the Show's name in front, which the card title already says.
function label(hass, entry, show) {
  const full = hass.states[entry.entity_id]?.attributes.friendly_name || entry.name || entry.entity_id;
  return full.startsWith(`${show} `) ? full.slice(show.length + 1) : full;
}

// Home Assistant only sets loadCardHelpers once a dashboard has been opened. If none has, loading the
// dashboard panel's code sets it, without showing that panel.
let helpersPromise;
function cardHelpers() {
  helpersPromise ??= (async () => {
    try {
      if (!window.loadCardHelpers) {
        const resolver = document.createElement("partial-panel-resolver");
        resolver.hass = { panels: [{ url_path: "tmp", component_name: "lovelace" }] };
        resolver._updateRoutes();
        await resolver.routerOptions.routes.tmp.load();
        await customElements.whenDefined("ha-panel-lovelace");
      }
      return window.loadCardHelpers ? await window.loadCardHelpers() : null;
    } catch (err) {
      console.warn("Genbu: card helpers did not load, showing plain rows", err);
      return null;
    }
  })();
  return helpersPromise;
}

if (!customElements.get("genbu-panel")) customElements.define("genbu-panel", GenbuPanel);
