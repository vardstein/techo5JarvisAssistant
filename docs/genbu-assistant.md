# Genbu Assistant: one place in Home Assistant

Status: proposal, 2026-10-12. Jade's decisions go under **Decided** at the end. §1 is built in Home
Assistant and the installer for Genbu 1.4.0 (GENBU-119, see [update-leader.md](update-leader.md)); the
Show's own question about a waiting update comes with the next firmware. The control center from §3 is built
as decided below (GENBU-121): the house card first, then one card per Show in foldable groups, each Show's
own automations through its "Genbu Show: <node>" label, and "Visit" on the integration's device opens it.
The installer asks about Recipes and Updates by voice for each Show, exposes the three music scripts on every
run, and ends with a pointer to the page.

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

- **Genbu Assistant checks the release channel once for the whole house**, every hour (6 hours until
  GENBU-258) and on Check for updates, instead of every
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
| **Breaks** (done in 1.4.0) | The main update Show select (`select.genbu_main_update_show`), the installer's `--main-update-show` and `--follow`, and the main-Show parts of [update-leader.md](update-leader.md). `sensor.genbu_house_version` keeps its id, because the Shows read it. |
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
- the German and English sentence automations and the radio and music scripts (house-wide);
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

## 4. Tools for the AI agent, and who gets them

Jade, 2026-10-12: the Shows have to work in a house that is not set up like ours, for example one on
Home Assistant Cloud by Nabu Casa with no Ollama at all. Recipes should move to where every agent finds
them, but agents that never cook should not get them, and it has to be safe.

### What Home Assistant gives us (checked in the 2026.10.1 source)

- **Sentence triggers come first, whatever the agent.** `assist_pipeline/default_pipeline.py` runs the
  installer's sentence triggers before the conversation agent, with or without "Prefer handling commands
  locally". The German extras answer the same with Ollama, OpenAI or Nabu Casa.
- **Nabu Casa's agent only uses the standard "Assist" tool set.** `cloud/conversation.py` passes
  `LLM_API_ASSIST` and nothing else; it has no option to tick another set. Today's recipe tool is its own
  set ("Genbu Rezepte"), so a Nabu Casa house can never use it.
- **An integration can add tools to the Assist set** with an `llm.py` platform (`components/llm/__init__.py`;
  `assist_satellite/llm.py` adds broadcast this way). It may add a line to the prompt too. Tool names have to
  start with the domain, `genbu__…`; from 2027.3 Home Assistant stops taking them otherwise.
- **HA asks the platform each time it builds the tool list**, and tells it the device that asked, the
  agent's integration (`ollama`, `cloud`, `openai_conversation` …) and the language (`LLMContext`). It does
  not say which agent entity is talking. So "this agent yes, that agent no" can't be done cleanly, but "this
  device yes, that device no" can. The device is also what matters: a recipe needs a screen in a kitchen.
- **Scripts are not exposed to Assist by default** (`DEFAULT_EXPOSED_DOMAINS` in
  `homeassistant/exposed_entities.py`). An agent can only call a script someone exposed. Today the installer
  exposes none: in our house only Play Random Music is, so the agent cannot call "Radiosender abspielen" or
  "Musik nach Stimmung".

### Proposal

**Music scripts.** The installer exposes the three scripts meant for the agent: Radiosender abspielen,
Musik nach Stimmung and Play Random Music. Not the helper Genbu Musik starten. (Decided, see below.)

**Recipes move into Genbu Assistant** as `genbu__recipe`, offered through `llm.py`. Who gets it is decided
for every request, and all of these have to hold:

1. A recipe search is set up in Genbu Assistant's options (the SearXNG address). Without one, nobody gets
   the tool.
2. The request comes from a Genbu Show. A phone, a Voice PE, another satellite or the chat in the
   Home Assistant app never see it. An agent that only serves those never cooks, and never gets the tool.
3. That Show has **Recipes** on. New switch on the Show's card, in the Screen group. On by default only for
   a Show whose recipe page works; the bedroom Show can be turned off.

**Safer than today:**

- **Recipe pages are strangers' text.** In the Assist set the same agent can also unlock doors and switch
  lights, so what a page says must not reach it as words it might follow. The model gets the dish, the site,
  servings, minutes and how many steps. Ingredients and steps only when someone asks for them to be read
  out, and then as plain short lines (no links, no markup, each line cut at 200 characters, at most 12
  steps), marked in the result as page text and not instructions. The screen shows the full recipe anyway.
  Only the page's recipe data (`ld+json`) is read, as today.
- **No way into the home network.** Today the address is checked, and aiohttp then looks the name up again
  when it connects; a name that changes its answer in between gets through (written in the code). The new
  tool uses its own connection with a resolver that drops every address that is not public, so the check
  and the connection are the same lookup. Redirects are checked the same way.
- **Limits.** One lookup at a time per Show, 8 seconds per page, at most 4 pages tried, page size capped
  (as today).
- **Honest labels.** The tool says it changes the screen, is not destructive and reaches the internet
  (`ToolAnnotations`), so an agent that cares can ask first.
- **Language.** The prompt line and the search follow the request's language, German or English.

**The firmware tool** (`genbu__firmware`, §1) gets the same gate: questions ("any updates?") from any Show or
from an admin in the app; anything that changes something asks first, always.

### Optional features: asked by the installer, switched per Show

Jade, after reading the above: the installer asks for each optional feature, and the control center has a
toggle for every one of them. The toggle works per Show, not for all Shows at once.

| Feature | Per Show? | The toggle | The installer asks |
|---|---|---|---|
| Recipes by voice | yes | new Genbu switch on the Show, Screen group | "Recipes on this Show?" only when a recipe search is set up; then on |
| Updates by voice | yes | new Genbu switch on the Show, Updates group | "Ask this Show about updates by voice?" on |
| Turn the room down while it listens | yes | that Show's own automation, its on/off on the card | as today |
| Watchdog (planned) | yes | that Show's own automation, its on/off on the card | when it exists |
| Music scripts for the AI | no, house | on the house card | once per house; always exposed (decided) |
| Voice extras, German and English (Wo sind wir, Wecker …) | no, house | on the house card | once per house, as today |

The tool gate above then reads the Show's own switch: a Show with Recipes off never offers the tool, the
kitchen Show next to it still does.

**Why the voice extras stay per house.** A sentence trigger that matches answers, even when its automation
stops at a condition: `default_agent.py` then says "Erledigt"/"Done", and the AI never gets the request
(checked in 2026.10.1). So "Wo sind wir?" on a Show with the extras off would just get "Erledigt" instead of
an answer from the AI. Turning the automations off for the house does not have that problem, because the
sentence then does not match at all.

**The old "Genbu Rezepte" set** stays for one release and says in Repairs that it is built in now and can be
unticked; then it goes. Our house has no recipe entry set up, so nothing changes here.

### Gains, losses, breaks

| | |
|---|---|
| **Gains** | Recipes and firmware work with any agent that can control the house, Nabu Casa included, with nothing to tick. Every optional feature is asked once and can be switched per Show from the page. Only Shows that cook see the recipe tool, so other agents and devices keep a shorter tool list. Strangers' text no longer reaches the model unasked. The DNS gap is closed. |
| **Losses** | Asking for a recipe from a phone or the app chat no longer works (it never showed anything there). Reading out steps needs one more ask. |
| **Breaks** | A house that ticked "Genbu Rezepte" sees a Repairs note for one release, then the set goes. A house that runs the agent without "Control Home Assistant" gets no Genbu tools, as it gets none of HA's. |

### Replies with nothing behind them (GENBU-139)

Asked "Macht die Musik im Schlafzimmer aus", the model once said the music was off and called no tool. The
prompt already says to report only what a tool reported, so that alone does not hold. Two things now do:

- The local stop sentences take a room ("Mach die Musik im Schlafzimmer aus", "turn off the music in the
  bedroom"), so this one never reaches the model. They also pause a Show's music player standing in that
  room, which Home Assistant's own pause misses when the player has no room.
- Genbu Assistant reads every finished turn of a language model (`claims.py`). When the reply says
  something was switched, paused or set and no tool ran in that turn, it logs a warning and fires
  `genbu_unbacked_claim` with `agent_id`, `asked` and `said`. It can't take the reply back; it shows how
  often this happens and with which sentences, so they can become local ones or prompt lines. An
  automation on the event can send a notification. Home Assistant's own and the local sentences are left
  out.

## 5. A Show's key (GENBU-196)

A Show signs in to Home Assistant with a long-lived token of a user of its own: not an admin, and only
allowed to sign in from the home network (`local_only`). When Home Assistant turns it away, the Show stops
asking, so the failed logins stop too. It then says why in its "Home Assistant key" sensor:

| State | What Home Assistant said |
|---|---|
| `ok` | nothing wrong |
| `none` | the Show has no key yet |
| `gone` | it doesn't know the key ("Invalid access token") |
| `remote` | the user may only sign in from home, and the Show came from outside |
| `off` | the user is switched off |

### What Genbu does about it

Genbu asks the Show which key it holds, over its encrypted ESPHome connection. The Show names the key by
the SHA-256 of its refresh token id, so the key itself never leaves it. Genbu then checks that against
Home Assistant's own records (`keys.py`, `keycare.py`) and puts up one Repairs issue per Show:

| Issue | When | What the admin does |
|---|---|---|
| `key_gone` | Home Assistant doesn't have the key any more | press the button: a new key |
| `no_user` | the Show's user is gone too | press the button: a user like the installer's, and a key |
| `user_off` | the user is switched off | switch it on in Settings → People; the issue then becomes `key_gone` |
| `not_a_show_user` | the user is an admin or may sign in from outside | make it a plain home-only user, or run the installer again |
| `not_encrypted` | the ESPHome connection has no encryption key | run the installer again |
| `home_only` | the Show came from outside | fix the route, then press the button to try again |

There is one case without an issue. If Home Assistant still has the key and the user is fine, the refusal
was a slip on the way, such as a restore or a restart that was only half done. Genbu has the Show try
once more (`home_assistant_retry`). It does that once per situation, because each failed try is a
failed login in Home Assistant's notifications.

Switching a user off deletes all of its tokens. That's in `async_deactivate_user` in Home Assistant's
`auth/__init__.py`. So a Show whose user was switched off can't get back in by trying again. It needs a
new key after the user is switched on.

A new key comes only from an admin: the Repairs button (Home Assistant lets only admins use Repairs
flows) or `genbu.show_key {node, url}` (an admin-only action, which the installer calls since 1.4.2).
Genbu makes the new token first and hands it over the encrypted connection. It deletes the user's other
tokens only after the Show says it stored the new one. If the Show doesn't take it, the new token is
deleted again and nothing else changes.

### Why Genbu never does it by itself, and why there is no "from anywhere"

The first idea was to renew a lost key automatically, and later to offer a switch that lets a Show sign in
from anywhere. Both were dropped after a security pass:

- A Show's user is a normal Home Assistant user, and a normal user's policy is the admin policy. That's
  `USER_POLICY` in `auth/permissions/const.py`. So a Show's key opens the whole house.
- Deleting a token is how an admin locks out a Show that was lost or stolen. Home Assistant keeps nothing
  that tells that apart from a key lost some other way. An automatic renewal would undo the lock.
- Home-only has been bypassed in practice by whatever hides where a request comes from.
  [CVE-2023-41894](https://github.com/home-assistant/core/security/advisories/GHSA-wx3j-3v2j-rf45): the
  cloud's SniTun proxy made outside requests to local-only webhooks look local. Home Assistant now
  refuses a `local_only` user on a cloud connection ("User is local only", `async_user_not_allowed_do_auth`
  in `components/http/auth_util.py`).
- A Show sits in a room and its storage can be read by whoever holds it, so its key has to be worth as
  little as possible outside the home.

So a Show's user always stays home-only, and Genbu never makes users, changes user flags or renews keys
by itself. A `home_only` issue points at the route instead: a Tailscale subnet router that rewrites
addresses (`--snat-subnet-routes=false` keeps them), a proxy missing from `trusted_proxies`, or a Home
Assistant Cloud address. The installer refuses a `*.ui.nabu.casa` address for the Show, and so does
`genbu.show_key`. The full write-up with sources is the security comment on GENBU-196.

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
- **Other houses (§4)** (Jade, later that evening):
  - The installer always exposes its three music scripts to Assist.
  - Recipes move into the Assist set through `llm.py`, as recommended, but only for Shows that cook and
    made safer. Jade asked for that to be fleshed out; it is §4's proposal.
  - The installer asks for each optional feature, and the control center has a toggle per Show for each
    (§4, "Optional features").
  - The voice extras stay per house, for the reason given there.
  - The voice extras also come in English: the same pieces with English sentences and answers, set up
    when the house has an English pipeline (GENBU-120, built for 1.4.0). They are their own automations
    (ids ending in `_en`) and a second volume blueprint, and they call the same music scripts as the
    German ones, so a house with both languages has one set of scripts. Words both languages share
    ("hi", "hey", "stop stop") stay German in such a house. Not in English yet: the genre named in
    the random-music answer, the "found nothing, playing this instead" wording, and the model's
    room-climate tool, which still answers in German words for the model to translate.

### Groups on a Show's card

The groups come from what the Show already tells Home Assistant, not from a list of entity ids. Shows with
other firmware or new features land in the right group or in "More" without a page update.

1. **The Show's sub-devices are groups already.** echod puts related things on their own device under the
   Show: "ring", "microphone", "playback", "assistant 1", "assistant 2". Each one becomes a group named after
   that device.
2. **The rest goes by domain and by word** in the entity's own name, first match wins:

   | Group | What lands there |
   |---|---|
   | On the device | screen switch and brightness, night mode, the main LED ring light, microphone mute, camera, speaker volume |
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
