# Genbu Assistant: one place in Home Assistant

Status: proposal, 2026-10-12. Nothing here is built yet. Jade's decisions go under **Decided** at the end.

Jade asked for three things after the 1.3.0 tests:

1. Stop having the Shows act as the updater. Home Assistant updates them, through the Genbu integration or
   through something ESPHome already has, without reinventing the wheel.
2. Rename the integration "Genbu Assistant" and give it a new icon. Name it the way Home Assistant names its
   own integrations.
3. Make everything the installer adds findable in the Genbu Assistant, as a control center, so nobody has to
   hunt for it in Home Assistant.

## 1. Updates: Home Assistant picks the version, the Shows install it

### What ESPHome and Home Assistant have today

- **ESPHome Device Builder.** The new builder (public beta since
  [2026.5](https://esphome.io/changelog/2026.5.0/)) has a firmware job queue, bulk actions and labels. Since
  [2026.7](https://esphome.io/blog/2026/07/15/esphome-2026-7/) it also queues an update for a device that is
  offline. It compiles ESPHome YAML for ESP chips and flashes the result. A Show runs echod, a Go program on
  Linux, so the builder cannot build or flash it. **Not usable for us.**
- **ESPHome `update` with `platform: http_request`.** The device reads a manifest and Home
  Assistant gets an update entity for it. Install tells the device to fetch and flash.
  ([docs](https://esphome.io/components/update/http_request/))
  **echod already works this way.** It speaks the ESPHome native API and reports an update entity (firmware
  class, with progress and release notes). The Install button in Home Assistant already calls echod's own
  install. That is the wheel, and we already use it.
- **Its limit.** The ESPHome update entity in Home Assistant 2026.10 can install, show progress and show
  release notes. It cannot install a *named* version. The API only has "install" and "check", so Home
  Assistant cannot say which version (`components/esphome/update.py`). Home Assistant has no "update all"
  either. `update.install` can target several entities, and `update.became_available` can start an
  automation. ([update integration](https://www.home-assistant.io/integrations/update/),
  [update entity](https://developers.home-assistant.io/docs/core/entity/update/))

So the missing piece is not installing. It is *who picks the version*. Today a Show picks it: the main update
Show (job 51, [update-leader.md](update-leader.md)). That choice can move into Home Assistant.

### Proposal

- **Genbu Assistant checks the release channel once for the whole house**, every 6 hours, instead of every
  Show checking every 10 minutes. The channel (stable, beta) is a setting under Configure.
- **One update card for all Shows**, "Genbu Show firmware", on a new "Genbu Assistant" service device that also
  carries the house-wide sensor and settings (today they sit on no device). It shows the version your
  Shows run and the newest one on the channel, with release notes. It can **install a chosen version**, so
  going back works from Home Assistant too. Its progress counts how many Shows are done.
- **Install sets the house version.** Each Show then moves to it the way following Shows already do: the
  tag's signed manifest, the same checks, slot install, trial and rollback, the CI floor, and not while
  music, a call or an alarm is going.
- **Install at night by itself** is a switch on that device, off by default. It installs in the night
  window from Home Assistant's own clock.
- Each Show keeps its own update card, and its Install button keeps meaning "go to the house version".
- **The Shows decide nothing and do not speak about updates.** When an update is there, Home Assistant
  says so in Settings › Updates, like every other update.

**Already in place.** A Show already follows whenever the house version is valid and its name is not in
`main_shows` (`roleOf` in `echod/internal/feature/firmware/house.go`). If Genbu Assistant sends no main Shows,
every Show follows, and **the Show side needs no change** for this part. A Show without Home Assistant or
without the integration stays a free Show on its channel, as today.

**Safety stays on the Show.** Home Assistant only names a version. The Show still checks the release key
signature, product, board, sha256, size and floor. So a Home Assistant that someone else controls can pick
among our signed releases, but cannot push its own files.

### Gains, losses, breaks

| | |
|---|---|
| **Gains** | One check per house instead of one per Show every 10 minutes. One place that picks the version, and it is the place people already look. Going back from Home Assistant. HA's skip and `update.became_available` work. A lot of code goes: picking a main Show, the missing-main Repairs timeline, the 7-day auto-pick and the spoken update questions. |
| **Losses** | No "want me to update?" spoken on the Show. That could come back later as one announcement from Home Assistant if Jade wants it. A house whose Home Assistant is down does not move. Its Shows stay where they are, and a Show that never reaches Home Assistant is free after 10 minutes, as today. |
| **Breaks** | The main update Show select (`select.genbu_main_update_show`), the installer's `--main-update-show` and `--follow`, and the main-Show parts of [update-leader.md](update-leader.md). `sensor.genbu_house_version` keeps its id, because the Shows read it. |
| **Moving over** | On the first start of the new version, the house version is whatever the current main update Show runs. Nothing moves, and nobody gets asked anything. |

## 2. Name and icon

- **How Home Assistant names things.** An integration carries the product's own name, with nothing added:
  "Music Assistant", "ESPHome", "Wyoming Protocol". Only a cloud variant of a local product gets a suffix
  ("Cloud"). ([manifest docs](https://developers.home-assistant.io/docs/creating_integration_manifest/))
  So the name is **Genbu Assistant**, without "Integration".
- **The domain stays `genbu`.** Renaming it would orphan the config entry, every `genbu.*` entity id and the
  sensor the Shows read. Only `name` in the manifest, the sidebar title and the texts change.
- The Assist pipeline from Q9 is also called "Genbu Assistant". That is on purpose: for the person using it,
  they are one thing.
- **Icon.** Home Assistant 2026.3 and later takes the icon from the integration's own `brand/` folder
  ([blog](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)), so a new icon is just new
  files there: `icon.png` 256×256, `@2x` 512×512, and a `dark_` pair for each. The brands rules say a
  custom integration must not look like an official Home Assistant one.

## 3. Everything in one place

What the installer adds to a Home Assistant today:

- each Show's ESPHome entry and devices;
- Genbu's settings device per Show;
- the per-Show "turn the room down" automation;
- the German sentence automations and the radio and music scripts (house-wide);
- the shared Music Assistant script;
- the Assist pipeline;
- the labels;
- later, the watchdog blueprint and its automations.

**What Home Assistant does not allow.** In 2026.10 a device belongs to exactly one config entry
(`helpers/device_registry.py`), so a Show's ESPHome device cannot also be listed under Genbu Assistant.
Automations and scripts always belong to the automation and script integrations. The control center is
therefore the Genbu page plus the integration page. It is not a single list of owned things.

**Proposal:**

- **The Genbu page opens with a "Genbu Assistant" card for the house:**
  - the firmware update and the Shows' version;
  - the install-at-night switch;
  - a link to the Assist pipeline;
  - every automation and script that carries "Genbu Show installed", as rows that open them;
  - open Repairs issues from Genbu, if there are any.

  The labels already reach the page (`hass.entities[...].labels`), so nothing has to be copied.
- **Each Show's card** also lists that Show's own automations, through its "Genbu Show: <node>" label.
- **On the integration page**, the Genbu Assistant device's "Visit" link opens the Genbu page.
- **When the installer is done**, it says: "Everything is on the Genbu Assistant page in the sidebar."

## Decided

(open)
