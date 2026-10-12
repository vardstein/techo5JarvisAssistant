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
  Show checking every 10 minutes.
- **One update card for all Shows**, "Genbu Show firmware", on a new "Genbu Assistant" service device that also
  carries the house-wide sensor and settings (today they sit on no device). Its progress counts how many
  Shows are done.
- **Install sets the house version.** Each Show then moves to it the way following Shows already do: the
  tag's signed manifest, the same checks, slot install, trial and rollback, the CI floor, and not while
  music, a call or an alarm is going.
- **Install at night by itself** is a switch on that device, off by default. It installs in the night
  window from Home Assistant's own clock.
- Each Show keeps its own update card, and its Install button keeps meaning "go to the house version".
- **The Shows decide nothing and do not ask about updates on their own.** When an update is there, Home
  Assistant says so in Settings › Updates, like every other update. You can still ask a Show by voice (below).

### Like Home Assistant's own update cards

Jade: "look at how the Home Assistant Core update cards look, with their release notes, backup before
updating and so on". The card uses the same parts Core's card uses, so it looks and works the same:

- **Release notes.** A short summary on the card, the full notes behind "Release notes", and a link to the
  release. The notes come from the release itself, as a file CI adds next to `manifest.json`, so the house still
  never calls the rate-limited GitHub API.
- **Back up before updating.** The same toggle Core and add-ons have, with the last backup time. Home
  Assistant shows it once an update card supports backups. It is off by default, and Home Assistant's own
  backup settings decide the default ([2025.2](https://home-assistant.io/blog/2025/02/05/release-20252/)).
  When it is on, Genbu Assistant first saves every Show's settings into its own store, then has Home Assistant
  make its usual automatic backup through the backup manager, the same one Core's card uses. That backup
  includes the saved settings. Only then does it install.
- **Progress, skip, and the "update available" trigger** work as on every other card.
- **What the dialog cannot do.** Home Assistant's update dialog has no version picker. It only sends
  `entity_id` and `backup` (checked in the 2026.10 frontend). Picking an older version is therefore on the
  Genbu page and in the `update.install` action, not in the dialog.

### Switching channels

Jade: "make it so the user can switch through the branches if they want". CI builds three channels from
three branches, and Genbu Assistant offers all of them:

- **"Release channel" select** on the Genbu Assistant device: Stable, Staging, Dev. Staging is for testing
  the next release; Dev is for people who help test and accept breakage. Changing it only changes what the
  card offers. Nothing installs until you press Install, or the night switch does it.
- **Going to an older build.** From Dev back to Stable usually means an older version. Home Assistant's card
  calls that "up to date", because it only offers newer versions. So the Genbu page then says "Your Shows run
  1.4.0-dev.12, Stable is 1.3.2" with a "Switch to 1.3.2" button. That runs the same install, and going back
  is allowed on this path, as it already is for following Shows.
- **"Pick a version"** on the Genbu page lists the last releases of the chosen channel with their notes.
  Every choice is a signed release. Feature branches have no release, so they are not offered.

### By voice: understood by meaning, not by fixed sentences

Jade: any request clearly meant for the Genbu Show should work. "Update the thermostat" or "update the
readings of …" are not meant for it. It should be understood, not matched against hardcoded sentences.

**How.** Since 2026.10 an integration can give the AI its own tools through an `llm.py` platform
(`components/llm` in Home Assistant). Genbu Assistant adds one tool, `genbu__firmware`. Its description tells
the model when it applies: the Show you are talking to ("you", "yourself"), "the Shows", "Genbu", its software
or version. It also says when it does not apply: other devices, their readings or settings, news or weather
"updates", lists. The model decides from what was meant, in German or English, and in words nobody wrote
down. Anything that changes something asks first ("1.4.0 is ready. Install it on all Shows now?") and uses
the follow-up from GENBU-117 for the yes.

| Said | What happens |
|---|---|
| "Gibt es ein Update für dich?" / "Any updates?" | Version now, whether one is waiting, the one-line summary |
| "Update dich" / "Install the update" / "Update the Shows" / "Spiel die neue Version ein" | Asks, then installs on all Shows (when nobody is using them) |
| "Was ist neu?" / "What's new in the update?" | Reads the summary of the waiting release |
| "Welche Version hast du?" | Says the version and the channel |
| "Mach das Update heute Nacht" | Installs once in tonight's window |
| "Überspring das Update" / "Skip this one" | Skips that version, like the card's Skip |
| "Geh zurück auf die alte Version" | Asks, then goes back to the version before |
| "Wechsel auf Staging" / "Switch to the dev channel" | Asks (warns for Dev), then changes the channel; installs only if you say so |
| "Schalt automatische Updates ein" | The night switch |
| "Update das Thermostat auf 21 Grad", "Aktualisier den Zählerstand", "Gib mir ein Update zum Wetter", "Update the shopping list" | Not Genbu. The tool is not used; HA's own tools or the normal answer handle it |
| "Update" on its own | Only when nothing else fits: "Do you mean the Genbu Show software?" |

**Testing it.** A list of about 60 sentences, German and English, meant and not meant, run against the local
model on the Genbu Assistant pipeline. It counts how often the tool is picked rightly and wrongly. That is a
measure and not a match list: the sentences are not in the code.

**Without an AI agent** (a house whose pipeline uses only Home Assistant's built-in sentences), voice updates
are not there. The card and the page work the same.

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
| **Gains** | One check per house instead of one per Show every 10 minutes. One place that picks the version, and it is the place people already look. Release notes and backup like Core. Channels and going back from Home Assistant. Voice by meaning. HA's skip and `update.became_available` work. A lot of code goes: picking a main Show, the missing-main Repairs timeline, the 7-day auto-pick and the spoken update questions. |
| **Losses** | The Show no longer asks "want me to update?" by itself; you ask it instead. A house whose Home Assistant is down does not move. Its Shows stay where they are, and a Show that never reaches Home Assistant is free after 10 minutes, as today. |
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
- **The icon stays.** Jade only meant the name. The tortoise and snake already has a dark pair: Home Assistant
  shows the teal snake on a light theme and the silver one on a dark theme. A browser add-on like Dark Reader
  only recolors the page and does not switch Home Assistant's theme, so the teal one stays there. The dark one
  shows with Profile › Theme › Dark, or with "Auto" when the system is dark. (Checked on the live 1.3.0, both
  files served and rendered.)

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

Jade's answers on 2026-10-12:

- **Updates (§1): yes, as proposed**, plus: make the card like Home Assistant Core's (release notes, backup
  before updating), let people switch channels, and make spoken update requests work by meaning, not fixed
  sentences. All three are written into §1.
- **Name and icon (§2): name only.** "Genbu Assistant"; the tortoise and snake icon stays as it is.
- **Control center (§3): yes, as proposed.** The page's address stays `/genbu`.
- **The page after test 9/20** (Jade, same evening): it looks right. Three changes:
  - The sidebar entry and the page title read **"Genbu Control Center"**. This replaces the "Genbu Assistant"
    title from the answer before. The integration keeps the name Genbu Assistant.
  - Each Show's card is split into groups by what the things do, starting with what is on the device itself:
    screen, camera, mute, LED ring and so on.
  - Every group can be folded in and out, and the page remembers that per browser.

  How the groups are found is below, under "Groups on a Show's card".

### Groups on a Show's card

The groups come from what the Show already tells Home Assistant, not from a list of entity ids. Shows with
other firmware or new features land in the right group or in "More" without a page update.

1. **The Show's sub-devices are groups already.** echod puts related things on their own device under the
   Show: "ring", "microphone", "playback", "assistant 1", "assistant 2". Each one becomes a group named after
   that device.
2. **The rest goes by domain and by word** in the entity's own name, first match wins:

   | Group | What lands there |
   |---|---|
   | On the device | screen light, night mode, the main LED ring light, microphone mute, camera, speaker volume |
   | Voice | assist satellite, wake buttons, "Last heard", "Last reply", "Last wake word", wake word and assistant selects |
   | Music and radio | media player, "Music plays on", white noise, radio sensors |
   | Calls and alarms | answer, hang up, snooze, stop, next alarm, phone, missed ring, drop in, intercom |
   | Screen | dashboard, theme, clock, slideshow, screensaver, pop-ups, weather, calendar |
   | Night | night hours, start and end, night clock, screen at night, night light |
   | Updates | firmware update, channel, install automatically |
   | Network and access | Bluetooth, SSH, web access, USB debugging, setup page |
   | More | anything else |
3. **Inside each group**, controls come first and settings after a "Settings" line, as today.
4. **Diagnostics** (temperatures, Wi-Fi, memory and the like) get their own last group, folded in by default.
   Today they are left out.
5. **What is open by default:** "On the device" is open; everything else is folded in until opened once.
   The LED ring segments 1–12 sit folded inside the ring group, since nobody sets them one by one.
