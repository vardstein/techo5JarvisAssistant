// The Genbu Control Center in the sidebar: a card for the house, then one card per Genbu Show. Everything
// on it is Home Assistant's own (the Shows' ESPHome devices, the devices under them, their Genbu settings
// devices, and what the installer labeled), so nothing is copied and a renamed or hidden entity looks the
// same here as anywhere else in Home Assistant. Rows open the usual more-info dialog. The text is English
// only, like the entity names the Shows report.
//
// A Show's card is split into groups by what things do (docs/genbu-assistant.md, "Groups on a Show's
// card"). The groups come from the Show's sub-devices and from the entities' own names, not from a list of
// entity ids, so a Show with newer firmware still lands in the right group, or in "More". Every group
// folds, and the browser remembers which are open.

// Controls first, in this order; anything else after them, then the settings.
const ORDER = ["update", "media_player", "assist_satellite", "light", "switch", "select", "number", "button",
  "sensor", "binary_sensor", "automation", "script"];
const SKIP = new Set(["camera", "event"]);

// The settings device Genbu adds under each Show: its rows go by name like the Show's own.
const SETTINGS_MODEL = "Genbu Show settings";
// The house's own device, with the update card, the channel and the night install.
const HOUSE_MODEL = "Genbu Shows";
const INSTALLED = "Genbu Show installed";
const SHOW_LABEL = "Genbu Show: ";

export const ON_DEVICE = "On the device";
export const DIAGNOSTICS = "Diagnostics";
export const MORE = "More";

// The few controls people reach for on the Show itself, wherever echod put them.
const ON_DEVICE_NAMES = /^(screen|screen brightness|night mode|led ring|microphone mute|camera|lens cover|speaker|speaker volume|volume)$/i;

// The rest by word, in the order they are tried; the first that matches wins.
const RULES = [
  ["Updates", ["update"], /update|firmware|committed version|channel/i],
  ["Night", [], /night|quiet hours/i],
  ["Voice", ["assist_satellite"], /wake|last heard|last reply|assistant|follow-up|listening|thinking|stop word|reply|speech|near miss|stranger/i],
  ["Music and radio", ["media_player"], /music|radio|white noise|sendspin|bluetooth audio|now playing|sleep timer|equalizer|bass|treble|speaker eq|ducking/i],
  ["Calls and alarms", [], /answer|hang up|snooze|alarm|phone|call|missed|drop in|intercom|timer|ring/i],
  ["Screen", [], /dashboard|theme|clock|slideshow|screensaver|pop-up|pop up|weather|calendar|menu|screen|date color|logo|radar|events/i],
  ["Network and access", [], /bluetooth|ssh|web access|usb|setup page|wi-?fi|ip address|home assistant/i],
];

// How the groups follow each other on the card; a sub-device's group comes after "On the device".
const SHOWN = [ON_DEVICE, "Voice", "Music and radio", "Calls and alarms", "Screen", "Night", "Updates",
  "Network and access", "Automations", MORE, DIAGNOSTICS];

const FOLDS = "genbu-panel-open";

// groupOf is where one row goes: entity is the entity registry entry, device the device it is on, show
// the Show's device, row the name without the Show's in front.
export function groupOf(entity, device, show, row) {
  const d = domain(entity.entity_id);
  if (entity.entity_category === "diagnostic") return DIAGNOSTICS;
  if (d === "automation" || d === "script") return "Automations";
  if (!entity.entity_category && ((d === "light" && !/segment/i.test(row)) || ON_DEVICE_NAMES.test(row))) {
    return ON_DEVICE;
  }
  if (device && device.id !== show.id && device.model !== SETTINGS_MODEL) return subGroup(device, show);
  for (const [group, domains, words] of RULES) {
    if (domains.includes(d) || words.test(row)) return group;
  }
  return MORE;
}

// A sub-device's group is its name without the Show's: "Kitchen Show ring" is "Ring".
export function subGroup(device, show) {
  const full = name(device);
  const bare = full.startsWith(`${name(show)} `) ? full.slice(name(show).length + 1) : full;
  return bare.charAt(0).toUpperCase() + bare.slice(1);
}

// The groups in the order they are shown, each with its rows in order.
export function arrange(rows) {
  const groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.group)) groups.set(row.group, []);
    groups.get(row.group).push(row);
  }
  const place = (g) => {
    const at = SHOWN.indexOf(g);
    return at < 0 ? 0.5 : at; // sub-devices right after "On the device"
  };
  return [...groups.entries()]
    .sort(([a], [b]) => place(a) - place(b) || a.localeCompare(b))
    .map(([title, list]) => ({
      title,
      rows: list.sort((a, b) => a.config - b.config || rank(a.entity) - rank(b.entity) || a.name.localeCompare(b.name)),
    }));
}

class GenbuPanel extends HTMLElement {
  constructor() {
    super();
    this._cards = [];
    this._key = "";
    this._labels = null; // label id -> name, once loaded
    this._repairs = 0;
    this._open = readFolds();
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
        ha-card { padding-bottom: 8px; }
        ha-card h2 { margin: 0; padding: 16px 16px 8px; font-size: 20px; font-weight: 400; }
        .fold { display: flex; align-items: center; gap: 8px; width: 100%; padding: 10px 16px; border: 0;
          background: none; color: var(--primary-text-color); font: inherit; font-weight: 500;
          cursor: pointer; text-align: left; border-top: 1px solid var(--divider-color); }
        .fold .count { margin-left: auto; color: var(--secondary-text-color); font-weight: 400; }
        .fold ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); transition: transform .15s; }
        .fold[aria-expanded="false"] ha-icon { transform: rotate(-90deg); }
        .body { padding: 0 16px 8px; }
        .body[hidden] { display: none; }
        .body > * { display: block; margin: 8px 0; }
        .inner .fold { border-top: 0; padding: 6px 0; font-weight: 400; }
        .inner .body { padding: 0; }
        .row { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; cursor: pointer; }
        .row span:last-child { color: var(--secondary-text-color); text-align: right; }
        .section { padding: 8px 0 0; font-weight: 500; color: var(--secondary-text-color); }
        a.link { color: var(--primary-color); text-decoration: none; }
      </style>
      <header><button title="Menu" aria-label="Menu"><ha-icon icon="mdi:menu"></ha-icon></button><span class="title">Genbu Control Center</span></header>
      <main></main>`;
    this.shadowRoot.querySelector("button").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    this.addEventListener("ll-rebuild", (ev) => { ev.stopPropagation(); this._key = ""; this._render(); });
  }

  set narrow(value) { this.toggleAttribute("narrow", !!value); }

  set panel(value) {
    this._panel = value;
    this.shadowRoot.querySelector(".title").textContent = value?.config?.title ?? "Genbu Control Center";
    this._render();
  }

  set hass(value) {
    const first = !this._hass;
    this._hass = value;
    if (first) this._look();
    if (this._plainRows) this._key = ""; // plain rows show states, so they are drawn again
    this._render();
    for (const row of this._rows ?? []) row.hass = value;
  }

  // Labels and Repairs are not in the hass object: asked for once, and again now and then.
  async _look() {
    const hass = this._hass;
    try {
      const labels = await hass.callWS({ type: "config/label_registry/list" });
      this._labels = new Map(labels.map((l) => [l.label_id, l.name]));
    } catch (err) {
      this._labels = new Map(); // the page works without them
    }
    try {
      const { issues } = await hass.callWS({ type: "repairs/list_issues" });
      this._repairs = issues.filter((i) => i.domain === "genbu" && !i.ignored).length;
    } catch (err) {
      this._repairs = 0;
    }
    this._key = "";
    this._render();
    clearTimeout(this._again);
    this._again = setTimeout(() => this._look(), 5 * 60 * 1000);
  }

  disconnectedCallback() { clearTimeout(this._again); this._again = undefined; }

  connectedCallback() { if (this._hass && this._again === undefined) this._look(); }

  // The house and the Shows, worked out from the registries Home Assistant already sends.
  _cardsData() {
    const hass = this._hass;
    const makers = new Set(this._panel?.config?.manufacturers ?? ["OpenJade"]);
    const devices = Object.values(hass.devices ?? {});
    const entities = Object.values(hass.entities ?? {}).filter((e) => !e.hidden && hass.states[e.entity_id]
      && !SKIP.has(domain(e.entity_id)));
    const labels = this._labels ?? new Map();
    const labeled = (e, ids) => (e.labels ?? []).some((l) => ids.has(l));
    const installed = new Set([...labels].filter(([, n]) => n === INSTALLED).map(([id]) => id));
    const perShow = new Set([...labels].filter(([, n]) => n.startsWith(SHOW_LABEL)).map(([id]) => id));
    const automations = entities.filter((e) => ["automation", "script"].includes(domain(e.entity_id)));

    const houseDevice = devices.find((d) => d.model === HOUSE_MODEL && makers.has(d.manufacturer));
    const house = { id: houseDevice?.id ?? "house", title: houseDevice ? name(houseDevice) : "Genbu Assistant",
      device: houseDevice?.id, rows: [] };
    if (houseDevice) {
      for (const e of entities.filter((x) => x.device_id === houseDevice.id)) {
        house.rows.push(row(hass, e, house.title, "Updates"));
      }
    }
    // The house-wide pieces: labeled as installed, and not one Show's own.
    for (const e of automations.filter((x) => labeled(x, installed) && !labeled(x, perShow))) {
      house.rows.push(row(hass, e, "", "Automations and scripts"));
    }

    const shows = devices.filter((d) => makers.has(d.manufacturer) && !d.via_device_id && d.model !== HOUSE_MODEL
      && d.entry_type !== "service").sort((a, b) => name(a).localeCompare(name(b)));
    return { house, shows: shows.map((show) => {
      const byId = new Map(devices.filter((d) => d.id === show.id || d.via_device_id === show.id).map((d) => [d.id, d]));
      const rows = entities.filter((e) => byId.has(e.device_id)).map((e) => {
        const r = row(hass, e, name(show));
        r.group = groupOf(e, byId.get(e.device_id), show, r.name);
        return r;
      });
      // This Show's own automations, through its "Genbu Show: <node>" label on its device.
      const mine = new Set((show.labels ?? []).filter((l) => perShow.has(l)));
      for (const e of automations.filter((x) => mine.size && labeled(x, mine))) {
        rows.push(row(hass, e, "", "Automations"));
      }
      return { id: show.id, title: name(show), device: show.id, rows };
    }) };
  }

  async _render() {
    if (!this._hass || !this._panel) return;
    const data = this._cardsData();
    const key = JSON.stringify([data, this._repairs]);
    if (key === this._key) return;
    this._key = key;
    const main = this.shadowRoot.querySelector("main");
    const helpers = await cardHelpers();
    if (key !== this._key) return; // the house changed while the helpers loaded
    this._plainRows = !helpers;
    this._rows = [];
    const cards = [this._houseCard(helpers, data.house)];
    if (!data.shows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No Genbu Show found. Add one with the installer, or in Settings › Devices & services › ESPHome.";
      cards.push(empty);
    }
    for (const show of data.shows) cards.push(this._card(helpers, show, arrange(show.rows)));
    this._cards = cards;
    main.replaceChildren(...cards);
  }

  _houseCard(helpers, house) {
    const groups = arrange(house.rows);
    const links = [
      { type: "weblink", name: "Voice assistants", icon: "mdi:account-voice", url: "/config/voice-assistants/assistants" },
      { type: "weblink", name: "Genbu Assistant integration", icon: "mdi:puzzle", url: "/config/integrations/integration/genbu" },
    ];
    if (this._repairs) {
      links.unshift({ type: "weblink", name: `Repairs: ${this._repairs} open`, icon: "mdi:alert-circle", url: "/config/repairs" });
    }
    return this._card(helpers, house, groups, links, true);
  }

  // A card: its title, then each group folded or open, then the links.
  _card(helpers, card, groups, links = [], house = false) {
    const el = document.createElement("ha-card");
    const title = document.createElement("h2");
    title.textContent = card.title;
    el.append(title);
    for (const group of groups) {
      const open = this._isOpen(card.id, group.title, house || group.title === ON_DEVICE);
      const [head, body] = this._fold(card.id, group.title, group.rows.length, open);
      const ring = group.rows.filter((r) => /segment \d+$/i.test(r.name));
      const rest = group.rows.filter((r) => !ring.includes(r));
      this._fill(helpers, body, rest);
      if (ring.length) {
        // Nobody sets the ring's segments one by one: they sit folded inside the ring's group.
        const inner = document.createElement("div");
        inner.className = "inner";
        const [h, b] = this._fold(card.id, `${group.title} segments`, ring.length,
          this._isOpen(card.id, `${group.title} segments`, false), "Segments");
        this._fill(helpers, b, ring);
        inner.append(h, b);
        body.append(inner);
      }
      el.append(head, body);
    }
    const tail = document.createElement("div");
    tail.className = "body";
    const device = card.device ? [{ type: "weblink", name: "Device page", icon: "mdi:devices", url: `/config/devices/device/${card.device}` }] : [];
    for (const link of [...links, ...device]) tail.append(this._element(helpers, link));
    el.append(tail);
    return el;
  }

  _fold(cardId, group, count, open, text = group) {
    const head = document.createElement("button");
    head.className = "fold";
    head.setAttribute("aria-expanded", String(open));
    const icon = document.createElement("ha-icon");
    icon.setAttribute("icon", "mdi:chevron-down");
    const label = document.createElement("span");
    label.textContent = text;
    const n = document.createElement("span");
    n.className = "count";
    n.textContent = String(count);
    head.append(icon, label, n);
    const body = document.createElement("div");
    body.className = "body";
    body.hidden = !open;
    head.addEventListener("click", () => {
      body.hidden = !body.hidden;
      head.setAttribute("aria-expanded", String(!body.hidden));
      this._open[`${cardId}|${group}`] = !body.hidden;
      writeFolds(this._open);
    });
    return [head, body];
  }

  _isOpen(cardId, group, byDefault) {
    const saved = this._open[`${cardId}|${group}`];
    return typeof saved === "boolean" ? saved : byDefault;
  }

  // A group's rows: controls, then the settings after a "Settings" line.
  _fill(helpers, body, rows) {
    let settings = false;
    for (const r of rows) {
      if (r.config && !settings) {
        settings = true;
        body.append(this._element(helpers, { type: "section", label: "Settings" }));
      }
      body.append(this._element(helpers, { entity: r.entity, name: r.name }));
    }
  }

  // One row through Home Assistant's own row elements; without them, plain text that opens more-info.
  _element(helpers, config) {
    if (helpers) {
      const el = helpers.createRowElement(config);
      el.hass = this._hass;
      this._rows.push(el);
      return el;
    }
    if (config.type === "section") {
      const head = document.createElement("div");
      head.className = "section";
      head.textContent = config.label;
      return head;
    }
    if (config.type === "weblink") {
      const a = document.createElement("a");
      a.className = "link";
      a.href = config.url;
      a.textContent = config.name;
      return a;
    }
    const line = document.createElement("div");
    line.className = "row";
    const left = document.createElement("span");
    const right = document.createElement("span");
    left.textContent = config.name;
    const state = this._hass.states[config.entity];
    right.textContent = this._hass.formatEntityState?.(state) ?? state.state;
    line.append(left, right);
    line.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-more-info",
      { detail: { entityId: config.entity }, bubbles: true, composed: true })));
    return line;
  }
}

function row(hass, entry, show, group) {
  return { entity: entry.entity_id, name: label(hass, entry, show), config: entry.entity_category === "config", group };
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
  return show && full.startsWith(`${show} `) ? full.slice(show.length + 1) : full;
}

// Which groups are open, per browser. A browser that keeps nothing starts from the defaults every time.
function readFolds() {
  try {
    return JSON.parse(window.localStorage.getItem(FOLDS) || "{}") || {};
  } catch (err) {
    return {};
  }
}

function writeFolds(open) {
  try {
    window.localStorage.setItem(FOLDS, JSON.stringify(open));
  } catch (err) {
    // not kept; the page still works
  }
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
