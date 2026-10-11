# One update point per house (job 51)

Status: design, not built yet. Questions for Jade are at the end.

Today every Genbu Show checks GitHub on its own every 10 minutes, and each one speaks up about an update every
4 hours. With several Shows in one house you hear the same question from every room, and the Shows drift
apart: one says yes, another is off for a week, a third was installed from a newer build.

This design gives a house one Show that decides the version. The others follow it.

## Roles

**Main update Show (the leader).** It works like a Show does today. It checks its channel, tells and asks,
and installs at night when auto-update is on. It also publishes the **house version**, which is the version it
is running now.

**Following Show (a follower).** It never checks the channel for offers and never speaks about updates on
its own. It reads the house version from Home Assistant and moves to it.

Most houses have one leader. More are allowed, and the installer warns before it makes a second one
(see "More than one leader").

Dots will take the same follower role later. Nothing here is specific to the Show except where it says so.

## The house version entity

The leader publishes an ESPHome text sensor, `house_version`, which Home Assistant names
`sensor.<node>_house_version`. Its value is a release tag such as `v1.0.3-stable.52`.

- It is set **after the leader's own update has committed**: the new slot ran its 5-minute trial and stayed.
  So the leader is also the canary. A release that breaks the leader never reaches the other Shows, because
  the leader rolls back and its house version stays where it was.
- It is only ever a tag. It carries no download links, so nothing can be smuggled in through Home Assistant.
  A follower fetches that tag's signed manifest from GitHub itself (see "How a follower installs").

## How a follower knows its leader

Each follower gets a text entity, `update_leader`, that holds the leader's entity id. It is stored in
`/data/misc/techo5/state.json`, so it survives updates.

- The installer fills it in (see below). Home Assistant or a person can change it later with
  `text.set_value`, without reinstalling.
- The follower reads the leader's state over its existing ESPHome link (hastate). Weather and the radio
  list arrive the same way. **No Home Assistant token is needed.**
- Changing the leader makes the follower reconnect, because hastate declares what it follows at connect time.
- The rename tool (job 41) also updates `update_leader` on every follower when the leader is renamed, since
  the entity id changes with the name.

## When a follower moves

| Situation | What happens |
|---|---|
| Fresh install, its version differs from the house (older **or newer**) | It moves to the house version without asking. The installer has already told the user. |
| It was off and boots on a different version than the house | It asks once by voice and on screen: "The house runs 1.0.3. Update now?" "Yes" installs. "No" asks again on the next boot or in 24 hours, never during quiet hours. |
| It is running when the leader moves on | It follows without asking, once nobody is using it: no music, call, timer or alarm about to ring. Overnight auto-update rules do not apply: the user already said yes on the leader. *(See question 2.)* |
| The leader is gone (entity unavailable) | It stays where it is. `update_status` says "Main update Show not reachable since …". Nothing is spoken. *(See question 3.)* |
| The house version is not installable on this device | It stays and says so in `update_status`, for example when the release has no manifest for its product or board. |

A follower's Home Assistant update card shows `installed = its version`, `latest = house version`. The card's
Install button means "align with the house", so it also works for a downgrade.

## How a follower installs

- The channel's manifest URL is replaced by the tag's own:
  `releases/download/<tag>/manifest.json` and `.sig`. Every release already has these.
- The same checks run as today: release key signature, `product = genbu-show-v1`, the board in `boards`,
  sha256 and size.
- The same slot install, trial and rollback run as today.
- **New: a downgrade is allowed, but only on this path** (aligning with the house). The channel path keeps
  "newer only".
- Settings survive going back a version: `config/unknown.go` keeps keys an older build does not know. A
  setting a newer build *changed the meaning of* could still confuse an older one. That has not happened so
  far, and the release notes would have to say so.
- A rollback floor guards against a Home Assistant user pushing every Show back to an old release with a
  known hole. A follower refuses a house version below the floor carried in its own signed manifest
  (`min_align`, set by CI, empty for now). *(See question 4.)*

## The update channel select

Followers get a new option, **House**, in `update_channel`, next to Stable, Staging and Dev. It is the
follower role. Picking Stable, Staging or Dev on a follower makes it a free Show again: it checks GitHub
itself, as today.

The leader is a Show with the switch **Main update Show** (`update_leader_role`) turned on. Only a Show on a
real channel can have it on.

So in Home Assistant you can see and change the roles without the installer.

## Installer flow

After the Home Assistant sign-in (job 40) and before the Show is added:

1. **Scan.** Use the template API, which is already used for entity lists:
   `integration_entities('esphome')` filtered to `*_house_version`, with each state.
2. **None found.** Ask: "Make <name> the main update Show for this house? It will be the only one that
   checks for and asks about updates. (Recommended for your first Show.)" The default is yes.
3. **One found.** Say "<leader> is this house's main update Show (house version <tag>). <name> will follow
   it." Offer "follow" (default) or "make this one a main update Show too".
4. **Several found.** List them with their versions and ask which one to follow.
5. **Choosing a second leader** gets the warning: "<leader> is already the main update Show. With two,
   whichever announces a new version first decides for the followers, and the two can disagree. Continue?"
   The default is no.
6. **After the Show is added**, the installer sets `update_channel = House` and `update_leader = <entity>`
   through Home Assistant. The Show then aligns on its own (the first row of the table above). The installer
   waits for the version to match or for a reported failure, and shows the result.

`--leader <entity>` and `--main-update-show` skip the questions for scripted installs. Without Home
Assistant access the installer skips all of this, and the Show stays a free Show on its channel.

## More than one leader

Jade's rule: the one that triggers first wins, so followers are never pulled back and forth.

A follower follows **one** leader entity, the one picked at install. With two leaders, each follower takes
its own leader's version, so two groups can end up on different versions. *(See question 1.)* To stop the
leaders drifting apart, a leader also follows the other leaders it finds. When another leader announces a
**newer** house version first, it moves to that version as well, and never to an older one. So the first
leader to announce a version decides, and the second one catches up instead of fighting it.

## What changes where

- **echod `feature/firmware`:**
  - the role switch;
  - the House channel;
  - the `house_version` sensor, set on trial commit;
  - the `update_leader` text;
  - the follower loop (align, boot ask, idle follow);
  - `update.FetchTag`;
  - the downgrade allowed only on the align path.
- **echod `feature/firmware/tell.go`:** followers stay quiet. The boot ask reuses `remind.Ask`.
- **`tools/jarvis_crown`:** the scan, the questions and warnings, setting the two entities, waiting for
  alignment, and the rename tool update.
- **CI:** an optional `min_align` in the manifest (empty for now).
- **Tests:**
  - leader publishes only after commit;
  - follower aligns up and down;
  - boot ask once per boot or 24 hours;
  - no ask in quiet hours;
  - no install while busy;
  - a tag below the floor is refused;
  - the channel path still refuses downgrades;
  - installer scan with 0, 1 or 2 leaders.

## Open questions for Jade

1. **Two leaders.** Is "followers follow the leader picked at install, and leaders catch up to the newest
   one" what you meant by "the one triggering first dominates"? The other reading is that every follower
   listens to all leaders and takes whichever announced last. That one can flip a follower back and forth.
2. **Online followers.** When the leader updates, should running followers follow straight away when idle
   (my proposal), or wait for the night window like auto-update?
3. **Leader gone for long.** Should a follower whose leader has been unreachable for, say, 7 days fall back
   to checking its own channel? My proposal is no: stay put and show it in Home Assistant, and only the
   status text says so.
4. **Downgrade floor.** Is a CI-set floor fine, or should followers never downgrade below the version they
   were installed with?
