# One update point per house (job 51)

Status: design agreed with Jade on 2026-10-11, the main update Show dropped in Genbu 1.4.0 (GENBU-119).
The Show side (echod), the HA integration (`ha/custom_components/genbu`) and the installer step
(`tools/jarvis_crown/genbu.py`) are built.

Today every Genbu Show checks GitHub on its own every 10 minutes, and each one speaks up about an update every
4 hours. With several Shows in one house you hear the same question from every room, and the Shows drift
apart: one says yes, another is off for a week, a third was installed from a newer build.

This design gives a house one place that decides the version: Home Assistant. Up to 1.3, one Show in the
house, the main update Show, looked for updates and asked about them, and the others followed it. Since
Genbu 1.4.0 (GENBU-119) there is no main update Show any more: Genbu Assistant reads the release channel
itself and offers the update on one card, **Genbu Show firmware** (`update.genbu_show_firmware`), and every
Show follows. See [genbu-assistant.md](genbu-assistant.md), part 1, for why.

"House" and "house version" are words for this doc and the code only. What you see and hear says
"your Shows", "your other Shows" or "Genbu", the way Home Assistant talks: "Your Shows are on 1.0.3",
not "The house runs 1.0.3". The sensor keeps its id, `sensor.genbu_house_version`, because the Shows
read it; only its name changed, to "Genbu Show version".

## Who decides what

| Who | Job |
|---|---|
| **Home Assistant** (the Genbu integration) | The source of truth. It knows every Genbu Show in the house, reads the release channel, and publishes the **house version**. It moves only when somebody installs: the card's Install, a version picked by hand, the voice, `genbu.install_updates`, or the night install when that is on. |
| **Every Show** | Never checks the channel for offers on its own. It reads the house version and moves to it. |

Dots will follow the same way later. The integration finds them the same way.

## The Genbu integration in Home Assistant

This is a small custom integration, `genbu`, of the same kind as `jarvis_recipe`. *Jade: we could merge the
two into one Genbu integration later.*

**What it finds.** Every device from the ESPHome integration whose manufacturer is `OpenJade`. That is what
echod reports for the Show today. It names them by ESPHome node name, and it also remembers each Show's HA
device id, so a Show that is renamed (job 41) keeps what Genbu knows about it.

### Setting it up

1. Copy `ha/custom_components/genbu/` to `/config/custom_components/genbu/` on Home Assistant (the File
   editor or Samba add-on will do), and restart Home Assistant.
   The folder brings the Genbu mark along in `brand/`; Home Assistant 2026.3 and later shows it in
   Devices & services (older versions show a blank tile, nothing else changes).
2. **Settings → Devices & services → Add integration → Genbu.** It asks nothing. A new house starts at the
   version of its newest Show once that has settled, so nothing moves.
3. Optional, under the integration's **Configure**: the SearXNG address for the recipe search.

Once the folder is in place the installer does step 2 itself.

### The update card

On the Genbu Assistant device, next to two controls:

| Entity | What it is |
|---|---|
| `update.genbu_show_firmware` | Installed is the house version, latest is what the release channel has. **Install** moves the house; each Show then updates itself. A version picked by hand goes back or to another build the same way, and the release left behind is skipped, so neither the night install nor a Show brings it back; a newer release is offered again. **Skip** works as on any card. With a backup ticked, Home Assistant makes one first. After an install the card shows how far the Shows are, until every Show that is online has arrived or 30 minutes have passed; nothing else can be installed in that time. Release notes come from the release. |
| `select.genbu_release_channel` | stable, staging or dev. Until one is picked, the channel of the house version. |
| `switch.genbu_install_at_night` | Off by default. When on, a waiting update installs by itself at 03:00. |

**A Show taken out for good.** The action `genbu.forget_show` (from 1.1.0) drops one Show by its node name:
its last version, its music choice and its switches. The installer's `--remove-show` calls it after
removing the Show's ESPHome entry, so Genbu does not find it again first. A Genbu before 1.4.0 also picked
a new main update Show and named it in the answer (`new_main`).

**Moving over from 1.3.** On the first start of 1.4.0 the house version stays what it was. The main update
Show select, the Repairs issues about a missing main Show and what Genbu kept about main Shows are removed.
Nothing moves, and nobody gets asked anything.

**Updating the whole house** (from 1.3.2). The action `genbu.install_updates` installs the waiting house
update, so every Show follows; without one, it starts any Show still behind the house on its own update.
It answers with who started (`updating`) and who has nothing to install (`current`); a Show that is
offline is left out. The voice kit's sentence calls it too: "installiere die Updates" or "aktualisiere die
Shows" at any Show starts every update, even when that Show has none of its own, and says which Shows
started.

**Asking the assistant** (from 1.4.0). With an AI conversation agent, Genbu Assistant gives it the tool
`genbu__firmware`, so it understands what was meant rather than fixed sentences: "Gibt es ein Update für
dich?", "What's new?", "Welche Version hast du?", "Update the Shows". It answers from the Shows' update
entities: each version and channel, the waiting release and its summary. Installing always asks first and
then starts every Show with an update, as above. A Show gets the tool while its **Updates by voice** switch
is on (on until you turn it off); in the app only an admin does. Other voice devices never get it. The
voice kit's fixed sentences still answer first where they match.

**The dashboard cards** (from 1.3.1). Genbu brings the Jarvis dashboard's cards (weather, room, timer,
calendar, recipe) and loads them on every page, so a dashboard that uses them needs nothing copied and no
resource. See [ha-dashboard](../ha-dashboard/README.md).

**The Genbu Control Center** (from 1.3.0, named so in 1.4.0). Genbu Assistant adds **Genbu Control Center** to
the sidebar, at /genbu, with one card per Show. A card has
what you would use on the Show: the media player, the voice satellite, screen and LED ring lights, night
mode, microphone mute, white noise, the alarm buttons and the music choice; then under **Settings** the
Show's settings, like volume and which assistant and wake word it uses; and a link to the device page.
Every row is the Show's own ESPHome entity, so there is nothing to keep in step: rename or hide an entity
and the card follows. Diagnostics, the camera and the events stay on the device page. Rows open the usual
dialog. The page is in English, like the entity names the Shows report.

1.2.0 put copies of these controls on each Show's Genbu device and hid the ESPHome originals. 1.3.0
removes the copies and shows the originals again on its first start, except ones someone hid by hand
since. Dashboards that used a copy (`genbu_<node>_...`) need the original instead.

### Tests

`tests/test_genbu_house.py` and `tests/test_genbu_music.py` test the logic without Home Assistant and run in CI. `ha/tests` runs the
integration in a real Home Assistant through `pytest-homeassistant-custom-component`, by hand. That needs
the Python version current Home Assistant needs (3.14 for 2026.10):

```sh
python3.14 -m venv /tmp/havenv && /tmp/havenv/bin/pip install pytest-homeassistant-custom-component home-assistant-frontend
cd ha && /tmp/havenv/bin/python -m pytest
```

**What it holds,** under fixed entity ids so a Show never has to be told where to look:

| Entity | What it is |
|---|---|
| `sensor.genbu_house_version` | The house version, e.g. `v1.0.3-stable.52`. Attributes: `update_waiting` (the release the card offers and nobody skipped, which a Show asks about out loud), `since`, `shows` (each Show's version and whether it is online), and for Shows from before 1.4.0 `main_shows` (always empty) and `main_missing_since` (always empty). |
| `select.<show>_music_plays_on` | Per Show: where music asked for at that Show plays. "Automatic" is a Music Assistant player in the Show's room that is not a Show, else the Show's own player; never another room's. Its label says which and why: "Automatic: speaker in Living Room (Wohnzimmer)", or "Automatic: no speaker in Kitchen (genbuShow5)". A speaker that is offline is passed over, a picked one too, and the music goes to the next one in line until it is back; Automatic then says "Automatic: Wohnzimmer is offline (genbuShow5)", and an offline player reads "Fairphone 5 (offline)" in the list. Or pick any player. It sits on a small genbu device named like the Show and connected via its ESPHome device, since Home Assistant lets a device belong to one integration only; that device goes when the Show's own device is removed. |
| `sensor.<show>_music_falls_back_to` | Per Show, on the same genbu device: where its music goes when the player it plays on now is offline. The next one in line that is up (a pick falls back to the room's speaker), else the Show itself, which is the end of the line. Its `player` attribute is the entity. |

`sensor.genbu_house_version` also has `music_players`: where each Show's music plays, by its device id and
by its assist satellite. The installer's music scripts and sentences read it, so music asked for in the
bedroom plays in the bedroom, and a Show moved to another room plays there from the next look on.

**Where the house version comes from.** Each Show reports the version it runs once its update has
committed, in the diagnostic text sensor `committed_version`. That is set only after the slot trial says
`good` (5 minutes up), so it lags `installed_version` on the `firmware` update entity. A new house starts at
the newest `committed_version`; after that only an install moves it, so a newer Show never pulls the house
along. A release that breaks a Show rolls back on that Show, which then says so in `update_status`.

**What it never does.** It never downloads anything and never tells a Show *what* to install beyond a tag.
Each Show fetches that tag's signed manifest from GitHub and checks it itself.

## On the Show

A Show reads the house over its existing ESPHome link (hastate), the same way it gets the weather: the
state of `sensor.genbu_house_version` and its attributes. **No HA token is needed.**

- If the integration is there, the Show follows the house version. Its `update_channel` select stays, but
  it is not read while the Show follows. Genbu 1.4.0 leaves `main_shows` empty, so a Show from before
  1.4.0 follows too.
- If HA has sent nothing about the sensor 30 seconds after connecting (HA sends nothing for an entity that
  does not exist), it is a free Show on its channel, as today. Nothing changes for a house without the
  integration. A Show that has not reached HA at all is free after 10 minutes, so a Show without HA still
  installs overnight.
- Until then, or while the sensor's state is not a version (`unavailable`), the Show stays where it is and
  says nothing about updates.
- *Firmware, next part of GENBU-119:* a Show that is on the house version and sees `update_waiting` asks
  once out loud, "Want me to install it now?"; a yes installs the house update, as the card's Install
  does. Until that firmware is out, the card and the voice ("installiere die Updates", or asking the
  assistant) do the asking.

### When a following Show moves

| Situation | What happens |
|---|---|
| Fresh install, its version differs from the house (older **or newer**) | It moves to the house version without asking. The installer has already said so. |
| It was off and boots on a different version than the house | It asks once by voice and on screen: "This Show is on version 1.0.2, your other Shows are on 1.0.3. Want me to switch to it now?" "Yes" installs. "No" asks again on the next boot or in 24 hours, never during quiet hours. |
| It is running when the house version changes | It follows straight away once nobody is using it: no music, call, or timer or alarm due soon. It does not ask, because your Install counts for the house (Jade's choice). |
| The house version is not installable here | It stays and says so in `update_status`, for example when the release has no manifest for its product or board. |

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

## The main update Show, up to 1.3

Up to 1.3 the house version was the newest `committed_version` among the main update Shows, picked in
`select.genbu_main_update_show`, and a main Show gone for 24 hours opened a Repairs issue, with an
automatic pick after 7 days. 1.4.0 removes all of it: with Home Assistant reading the channel itself, no
Show has to be the one that looks. The Show screens still say "Following <main> · version 1.0.3" until
the firmware part of GENBU-119 is out.

## Installer flow

After the HA sign-in (job 40):

1. **Check for the integration** (config entries of domain `genbu`).
   - **Missing:** say how to add it: copy the folder, restart HA. The user can check again or skip; the
     Show then stays a free Show on its channel.
   - **There but not set up:** set it up through the config flow over REST, which it already uses for
     ESPHome. The form asks nothing, so this needs no question, also with `--no-questions`.
2. **The house has no version yet** (the first Show): say that the house starts at the newest Show's
   version once it has settled, and that updates come on the `update.genbu_show_firmware` card.
3. **The house has a version:** say that the Show follows it. The Show aligns on its own (the first row of
   the table above). The installer waits until the versions match or a failure is reported, and shows the
   result. It waits up to 15 minutes; a fresh Show lets its own version settle for 5 of them first. After
   that the Show carries on by itself and its update card shows how far it is.

## What changes where

- **`ha/custom_components/genbu`:**
  - device discovery (manufacturer OpenJade);
  - the house version sensor, the update card, the release channel select and the night switch;
  - tests: the logic in CI (`tests/test_genbu_house.py`), the integration in HA's test harness (`ha/tests`).
- **echod `feature/firmware`** (`house.go`):
  - follow the house via hastate;
  - the `committed_version` sensor;
  - the following loop (align, boot ask, idle follow);
  - `update.FetchTag`;
  - going back allowed only on the align path;
  - the status line;
  - next: ask about `update_waiting`.
- **echod `feature/firmware/tell.go`:** following Shows stay quiet. The boot ask reuses `remind.Ask`.
- **`tools/jarvis_crown/genbu.py`:** integration check and setup, waiting for alignment.
  `tools/jarvis-show.py` runs it after the Show is added (`install` and `home-assistant`). Tests in
  `tests/test_jarvis_crown_genbu.py`.

## Decided

Jade's answers on 2026-10-11:

- **Following Shows** follow straight away when idle.
- **Floor:** CI-set. Each signed manifest can carry `min_align`, empty for now. A Show refuses a house
  version below its own manifest's floor.
