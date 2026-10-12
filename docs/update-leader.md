# One update point per house (job 51)

Status: design agreed with Jade on 2026-10-11. The Show side (echod) and the HA integration
(`ha/custom_components/genbu`) are built, and so is the installer step (`tools/jarvis_crown/genbu.py`).

Today every Genbu Show checks GitHub on its own every 10 minutes, and each one speaks up about an update every
4 hours. With several Shows in one house you hear the same question from every room, and the Shows drift
apart: one says yes, another is off for a week, a third was installed from a newer build.

This design gives a house one place that decides the version: Home Assistant. One Show in the house, the main
update Show, looks for updates and asks about them. The other Shows follow.

"House" and "house version" are words for this doc and the code only. What you see and hear says
"your Shows", "your other Shows" or "Genbu", the way Home Assistant talks: "Your Shows are on 1.0.3",
not "The house runs 1.0.3". The sensor keeps its id, `sensor.genbu_house_version`, because the Shows
read it; only its name changed, to "Genbu Show version".

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
echod reports for the Show today. It names them by ESPHome node name, because that is what a Show looks
for in `main_shows`. It also remembers each Show's HA device id, so a main Show that is renamed (job 41)
comes back under its new node name and stays main.

### Setting it up

1. Copy `ha/custom_components/genbu/` to `/config/custom_components/genbu/` on Home Assistant (the File
   editor or Samba add-on will do), and restart Home Assistant.
   The folder brings the Genbu mark along in `brand/`; Home Assistant 2026.3 and later shows it in
   Devices & services (older versions show a blank tile, nothing else changes).
2. **Settings → Devices & services → Add integration → Genbu.** It lists the Shows it found, with their
   versions. Pick the main update Show, or leave it empty and pick it later in
   `select.genbu_main_update_show`. Until a main is picked, the Shows stay where they are, and Repairs
   says so.
3. Optional, under the integration's **Configure**: more main update Shows (after the warning), and a
   `notify` service, for example `notify.mobile_app_phone`, for the push when a main Show is gone for a day.

Once the folder is in place the installer does step 2 itself and adds more mains when asked to. The
`notify` service stays a choice under **Configure**; the installer keeps whatever is set there.

**A Show taken out for good.** The action `genbu.forget_show` (from 1.1.0) drops one Show by its node name:
from the main list, from the music choice, and from what Genbu remembers of it, with its Repairs issues.
When it was the only main update Show, the Show Genbu recommends becomes main, and the answer says which
(`mains`, `new_main`). The installer's `remove-show` calls it after removing the Show's ESPHome entry, so
Genbu does not find it again first.

**A Show's controls on its Genbu device** (from 1.2.0). Devices & services → Genbu → a Show lists
everything you would set on the Show: screen and LED ring lights, night mode, microphone mute, white
noise, alarm buttons, the Show's settings, volume, which assistant and wake word it uses, and the
firmware version. Home Assistant lets a device belong to one integration only, so these are copies:
each follows its ESPHome original and passes on what is done to it, like Home Assistant's own "Change
device type of a switch" helper. Their entity ids start with `genbu_<node>_`.

- Diagnostics, the camera, the events, the voice satellite and the firmware update stay on the ESPHome
  device only. The update is offered there, once; the copy only says which version runs.
- The everyday originals (lights, switches, white noise, buttons, text sensors) are hidden once their copy
  is there, so dashboards and the device list don't show them twice. Hidden entities still work in
  automations and on dashboards that already use them. One you show again by hand stays shown.
- Assist keeps using the originals. Before an original is hidden, Genbu writes down what Assist did with
  it, so hiding does not take it away from Assist; the copies are kept from Assist when they are made.
  Exposing a copy by hand afterwards sticks.
- **Configure → Hide the ESPHome originals** off shows them all again. Removing Genbu does the same.

### Tests

`tests/test_genbu_house.py`, `tests/test_genbu_music.py` and `tests/test_genbu_controls.py` test the logic without Home Assistant and run in CI. `ha/tests` runs the
integration in a real Home Assistant through `pytest-homeassistant-custom-component`, by hand. That needs
the Python version current Home Assistant needs (3.14 for 2026.10):

```sh
python3.14 -m venv /tmp/havenv && /tmp/havenv/bin/pip install pytest-homeassistant-custom-component
cd ha && /tmp/havenv/bin/python -m pytest
```

**What it holds,** under fixed entity ids so a Show never has to be told where to look:

| Entity | What it is |
|---|---|
| `sensor.genbu_house_version` | The house version, e.g. `v1.0.3-stable.52`. Attributes: `main_shows` (ESPHome node names), `main_missing_since` (ISO time since HA has seen the main Show unavailable, empty while it is there), `since`, `shows` (each Show's version and whether it is online). |
| `select.genbu_main_update_show` | The main update Show, picked from the Genbu Shows. A second main is added in the integration's options, with the warning. |
| `select.<show>_music_plays_on` | Per Show: where music asked for at that Show plays. "Automatic" is a Music Assistant player in the Show's room that is not a Show, else the Show's own player; never another room's. Its label says which and why: "Automatic: speaker in Living Room (Wohnzimmer)", or "Automatic: no speaker in Kitchen (genbuShow5)". A speaker that is offline is passed over, a picked one too, and the music goes to the next one in line until it is back; Automatic then says "Automatic: Wohnzimmer is offline (genbuShow5)", and an offline player reads "Fairphone 5 (offline)" in the list. Or pick any player. It sits on a small genbu device named like the Show and connected via its ESPHome device, since Home Assistant lets a device belong to one integration only; that device goes when the Show's own device is removed. |
| `sensor.<show>_music_falls_back_to` | Per Show, on the same genbu device: where its music goes when the player it plays on now is offline. The next one in line that is up (a pick falls back to the room's speaker), else the Show itself, which is the end of the line. Its `player` attribute is the entity. |

`sensor.genbu_house_version` also has `music_players`: where each Show's music plays, by its device id and
by its assist satellite. The installer's music scripts and sentences read it, so music asked for in the
bedroom plays in the bedroom, and a Show moved to another room plays there from the next look on.

**Where the house version comes from.** Each Show reports the version it runs once its update has
committed, in the diagnostic text sensor `committed_version`. That is set only after the slot trial says
`good` (5 minutes up), so it lags `installed_version` on the `firmware` update entity. The house version is
the newest `committed_version` among the main Shows. So the main Show is also the canary: a release that
breaks it rolls back, and the house never moves.

**What it never does.** It never downloads anything and never tells a Show *what* to install beyond a tag.
Each Show fetches that tag's signed manifest from GitHub and checks it itself.

## On the Show

A Show reads three values over its existing ESPHome link (hastate), the same way it gets the weather: the
state of `sensor.genbu_house_version` and its `main_shows` and `main_missing_since` attributes. **No HA token
is needed.**

- If its own node name is in `main_shows`, it is a main update Show.
- If the integration is there and its name is not in `main_shows`, it is a following Show. Its
  `update_channel` select stays, but it is not read while the Show follows. The update card's title says
  "follows <main>" instead of the channel.
- If HA has sent nothing about the sensor 30 seconds after connecting (HA sends nothing for an entity that
  does not exist), it is a free Show on its channel, as today. Nothing changes for a house without the
  integration. A Show that has not reached HA at all is free after 10 minutes, so a Show without HA still
  installs overnight.
- Until then, or while the sensor's state is not a version (`unavailable`), the Show stays where it is and
  says nothing about updates.

### When a following Show moves

| Situation | What happens |
|---|---|
| Fresh install, its version differs from the house (older **or newer**) | It moves to the house version without asking. The installer has already said so. |
| It was off and boots on a different version than the house | It asks once by voice and on screen: "This Show is on version 1.0.2, your other Shows are on 1.0.3. Want me to switch to it now?" "Yes" installs. "No" asks again on the next boot or in 24 hours, never during quiet hours. |
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
| **1 h** | Following Shows keep their version. `update_status` on each says "Main update Show <name> not reachable since 14:02", from `main_missing_since`. Nothing is spoken. |
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
one line: "Following <main> · version 1.0.3", or "Main update Show <name> not reachable since …".

## Installer flow

After the HA sign-in (job 40):

1. **Check for the integration** (config entries of domain `genbu`).
   - **Missing:** say how to add it: copy the folder, restart HA, add the integration. The installer then
     sets it up through the config flow over REST, which it already uses for ESPHome. The user can skip;
     the Show then stays a free Show on its channel.
2. **No main update Show yet:** ask "Make <name> the main update Show for this house? It will be the only
   one that looks for and asks about updates. (Recommended for your first Show.)" The default is yes.
3. **One exists:** say "<main> is your main update Show (version <tag>). <name> will follow
   it." Offer "follow" (default) or "make this one a main update Show too".
4. **Making a second main** gets the warning: "<main> is already the main update Show. With two, the other
   Shows follow whichever of them is newer. They cannot fight, but you get two Shows that ask about updates.
   Continue?" The default is no.
5. **After the Show is added,** the installer sets the choice through the integration's select or options
   over REST. A following Show then aligns on its own (the first row of the table above). The installer
   waits until the versions match or a failure is reported, and shows the result. It waits up to 15
   minutes; a fresh Show lets its own version settle for 5 of them first. After that the Show carries on
   by itself and its update card shows how far it is.

`--main-update-show` and `--follow` skip the questions for scripted installs.

## What changes where

- **`ha/custom_components/genbu`** (new):
  - device discovery (manufacturer OpenJade);
  - the house version sensor (from each main's `committed_version`) and the main select;
  - options flow for more mains, with the warning;
  - Repairs issue and fix flow;
  - the 7-day auto-pick;
  - optional push target;
  - tests: the logic in CI (`tests/test_genbu_house.py`), the integration in HA's test harness (`ha/tests`).
- **echod `feature/firmware`** (built, `house.go`):
  - follow the three values via hastate;
  - the `committed_version` sensor;
  - role from `main_shows`;
  - the following loop (align, boot ask, idle follow);
  - `update.FetchTag`;
  - going back allowed only on the align path;
  - the status line.
- **echod `feature/firmware/tell.go`:** following Shows stay quiet. The boot ask reuses `remind.Ask`.
- **`tools/jarvis_crown/genbu.py`** (built): integration check and setup, the questions and warning,
  setting the choice, waiting for alignment. `tools/jarvis-show.py` runs it after the Show is added
  (`install` and `home-assistant`). Tests in `tests/test_jarvis_crown_genbu.py`.
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
