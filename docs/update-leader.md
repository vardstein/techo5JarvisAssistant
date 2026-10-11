# One update point per house (job 51)

Status: design agreed with Jade on 2026-10-11, not built yet.

Today every Genbu Show checks GitHub on its own every 10 minutes, and each one speaks up about an update every
4 hours. With several Shows in one house you hear the same question from every room, and the Shows drift
apart: one says yes, another is off for a week, a third was installed from a newer build.

This design gives a house one place that decides the version: Home Assistant. One Show in the house, the main
update Show, looks for updates and asks about them. The other Shows follow.

## Who decides what

| Who | Job |
|---|---|
| **Home Assistant** (the Genbu integration) | The source of truth. It knows every Genbu Show in the house and which of them are main update Shows. It publishes the **house version**, and it asks you when a main Show goes missing. It is the only one that ever picks a main Show, so two Shows can never race for the job. |
| **Main update Show** | Works like a Show does today. It checks its channel, tells and asks, and installs at night when auto-update is on. Its version, once its update has committed, is what the house version is made from. |
| **Following Show** | Never checks the channel for offers and never speaks about updates on its own. It reads the house version and moves to it. |

Most houses have one main update Show. More are allowed. HA warns before a second one is added, and the
house version then is the **newest** version any main Show runs. So main Shows can only pull the house up,
never back and forth (Jade's choice: "follow one, leaders sync").

Dots will become following Shows later. The integration finds them the same way.

## The Genbu integration in Home Assistant

This is a small custom integration, `genbu`, of the same kind as `jarvis_recipe`. *Jade: we could merge the
two into one Genbu integration later.*

**What it finds.** Every device from the ESPHome integration whose manufacturer is `OpenJade`. That is what
echod reports for the Show today. It keys them by HA device id, so renaming a Show (job 41) changes nothing.

**What it holds,** under fixed entity ids so a Show never has to be told where to look:

| Entity | What it is |
|---|---|
| `sensor.genbu_house_version` | The house version, e.g. `v1.0.3-stable.52`. Attributes: `main_shows` (ESPHome node names), `since`, `shows` (each Show's version and whether it is online). |
| `select.genbu_main_update_show` | The main update Show, picked from the Genbu Shows. A second main is added in the integration's options, with the warning. |

**Where the house version comes from.** Each Show already reports its running version through its ESPHome
`firmware` update entity (`installed_version`). The house version is the newest `installed_version` among
the main Shows. It only counts after that Show's update has committed. The Show reports the new version only
after its 5-minute trial, so the main Show is also the canary: a release that breaks it rolls back, and the
house never moves.

**What it never does.** It never downloads anything and never tells a Show *what* to install beyond a tag.
Each Show fetches that tag's signed manifest from GitHub and checks it itself.

## On the Show

A Show reads two values over its existing ESPHome link (hastate), the same way it gets the weather: the
state of `sensor.genbu_house_version` and its `main_shows` attribute. **No HA token is needed.**

- If its own node name is in `main_shows`, it is a main update Show.
- If the integration is there and its name is not in `main_shows`, it is a following Show. Its
  `update_channel` select shows **House** and does not offer Stable, Staging or Dev.
- If the integration is not there (the sensor does not exist), it is a free Show on its channel, as today.
  Nothing changes for a house without the integration.

### When a following Show moves

| Situation | What happens |
|---|---|
| Fresh install, its version differs from the house (older **or newer**) | It moves to the house version without asking. The installer has already said so. |
| It was off and boots on a different version than the house | It asks once by voice and on screen: "The house runs 1.0.3. Update now?" "Yes" installs. "No" asks again on the next boot or in 24 hours, never during quiet hours. |
| It is running when the house version changes | It follows straight away once nobody is using it: no music, call, or timer or alarm due soon. It does not ask, because your "yes" on the main Show counts for the house (Jade's choice). |
| The house version is not installable here | It stays and says so in `update_status`, for example when the release has no manifest for its product or board. |
| The main Show is missing | It stays where it is. See below. |

A following Show's HA update card shows `installed = its version` and `latest = house version`. Its Install
button means "go to the house version", so it also works when that means going back.

### How a following Show installs

- It uses the tag's own manifest, not the channel's: `releases/download/<tag>/manifest.json` and `.sig`.
  Every release already has these.
- The same checks run as today: release key signature, `product = genbu-show-v1`, the board in `boards`,
  sha256 and size.
- The same slot install, trial and rollback run as today.
- **New: going back a version is allowed, but only on this path.** The channel path stays "newer only".
- Settings survive going back: `config/unknown.go` keeps keys an older build does not know.
- A floor stops a house version from taking a Show below a release with a known hole. The `min_align`
  field is set by CI in each signed manifest and is empty for now.

## When the main update Show goes missing

Jade asked for all of these together without a race: stay put, tell me in HA, let me pick the new one, and
take over by itself if nobody answers. One decider (HA) makes that safe.

| After | What happens |
|---|---|
| **0–1 h** | Nothing. Restarts and updates take minutes. |
| **1 h** | Following Shows keep their version. `update_status` on each says "Main update Show <name> not reachable since 14:02". Nothing is spoken. |
| **24 h** | HA asks you. The integration opens a **Repairs** issue: "<name> has been offline since … Pick a new main update Show". The fix flow lists the Shows that are online, with their versions. It recommends the one already on the house version that was online most recently. Other choices are "keep waiting 7 more days" and "this Show is gone for good" (it is removed from the main list). If you set a phone in the integration options, the same goes there as a push notification with a link. |
| **7 days, no answer** | HA picks the recommended Show itself. The house version stays, and the Repairs issue says what it did and how to change it. |
| **Old main Show comes back** | It sees it is no longer in `main_shows` and is a following Show from then on. If you want it back as main, pick it in the select. |

Why this cannot race:

- Only the integration ever changes `main_shows`, and HA runs it in one place.
- The Shows only read.
- Repairs has one issue per missing main Show (fixed issue id), so ten following Shows do not make ten
  issues.
- "Missing" is HA's own view: the main Show's entities are `unavailable`. A following Show that lost its own
  connection to HA does not count time at all.

The Show screens stay quiet by default, with HA as the one place to look. The update page on each Show shows
one line: "Following <main> · house version 1.0.3", or "Main update Show <name> not reachable since …".

## Installer flow

After the HA sign-in (job 40):

1. **Check for the integration** (config entries of domain `genbu`).
   - **Missing:** say how to add it: copy the folder, restart HA, add the integration. The installer then
     sets it up through the config flow over REST, which it already uses for ESPHome. The user can skip;
     the Show then stays a free Show on its channel.
2. **No main update Show yet:** ask "Make <name> the main update Show for this house? It will be the only
   one that looks for and asks about updates. (Recommended for your first Show.)" The default is yes.
3. **One exists:** say "<main> is this house's main update Show (house version <tag>). <name> will follow
   it." Offer "follow" (default) or "make this one a main update Show too".
4. **Making a second main** gets the warning: "<main> is already the main update Show. With two, the house
   follows whichever of them is newest. They cannot fight, but you get two Shows that ask about updates.
   Continue?" The default is no.
5. **After the Show is added,** the installer sets the choice through the integration's select or options
   over REST. A following Show then aligns on its own (the first row of the table above). The installer
   waits until the versions match or a failure is reported, and shows the result.

`--main-update-show` and `--follow` skip the questions for scripted installs.

## What changes where

- **`ha/custom_components/genbu`** (new):
  - device discovery (manufacturer OpenJade);
  - the house version sensor and the main select;
  - options flow for more mains, with the warning;
  - Repairs issue and fix flow;
  - the 7-day auto-pick;
  - optional push target;
  - tests with HA's test harness if it is available, otherwise pure logic tests.
- **echod `feature/firmware`:**
  - follow the two values via hastate;
  - role from `main_shows`;
  - the House channel;
  - the following loop (align, boot ask, idle follow);
  - `update.FetchTag`;
  - going back allowed only on the align path;
  - the status line.
- **echod `feature/firmware/tell.go`:** following Shows stay quiet. The boot ask reuses `remind.Ask`.
- **`tools/jarvis_crown`:** integration check and setup, the questions and warning, setting the choice,
  waiting for alignment.
- **Tests:**
  - house version is the newest main's committed version;
  - following Show aligns up and down;
  - boot ask once per boot or 24 hours;
  - no ask in quiet hours;
  - no install while busy;
  - floor refused;
  - channel path still refuses going back;
  - missing-main timeline;
  - one Repairs issue, however many Shows;
  - old main comes back as a follower;
  - installer with 0, 1 or 2 mains and without the integration.

## Decided

Jade's answers on 2026-10-11:

- **Two mains:** follow one, mains sync upward. The house version is the newest main's version.
- **Following Shows** follow straight away when idle.
- **Missing main:** all of it together without a race. HA decides, as described above.
- **Floor:** CI-set. Each signed manifest can carry `min_align`, empty for now. A Show refuses a house
  version below its own manifest's floor.
