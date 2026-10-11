# Jarvis Crown v1 installer

The Jarvis Crown installer is deliberately split into fail-closed gates.  J17 implements only the
host/input preflight.  It **never invokes adb or fastboot**, so it cannot query, reboot or modify an
Echo Show.

```sh
python3 tools/jarvis-crown.py preflight \
  --amonet-dir /path/to/amonet-crown-v2.0.1 \
  --lineage-zip /path/to/lineage-18.1-...-UNOFFICIAL-crown.zip
```

The preflight requires:

- Linux and Python 3.10+;
- `adb`, `fastboot`, `git`, `bash`, and GNU `timeout` in `PATH`;
- USB serial access through `dialout`/`uucp` for an unprivileged install;
- ModemManager not running;
- writable work and backup directories;
- at least 8 GiB free on every distinct filesystem used for work/backups;
- a complete local Amonet package whose `amonet/device.prop` says `DEVICE=crown`;
- when supplied, a LineageOS ZIP whose metadata says `pre-device=crown`.

The LineageOS zip and the board boot image are fetched rather than hunted for by hand:

```sh
python3 tools/fetch-show-assets.py --board checkers     # or crown
```

It downloads both from GitHub (amazon-oss/releases for LineageOS, the upstream TECHO5 release for the
boot image) into `~/.cache/jarvis-show/<board>/` (override with `--cache` or `JARVIS_SHOW_ASSETS`),
keeps a file only when its size and SHA-256 match the pin in `tools/jarvis_crown/assets.py`, and prints
the paths. It refuses a folder inside the repository: these files are hundreds of MB and never belong in
the checkout. `--check` verifies what is cached without downloading.

`jarvis-show.py install` does the same by itself for the board it detects, Amonet included, whenever
`--lineage-zip`, `--boot-image` or `--amonet-dir` is left out. Without `--rootfs` it also takes the
Jarvis root filesystem from a published release: the one `--channel` names (`stable`, `staging` or
`dev`, default `stable`). The release manifest is believed only with the release key's signature over
it and only when it names this board, and the download is held to the manifest's hash. The Show is
then set to follow the same channel for updates. A channel with no release for the board stops the
run before any question, with the other channels to try.

Amonet zips are only attached to the XDA threads, and XDA's CDN answers anything that isn't a real
browser with a 403 challenge page, so a script can't download them. The tool opens the attachment link
in your default browser instead (log in to XDA if it asks) and waits up to ten minutes for the finished
zip to show up in `~/Downloads` (or `--from DIR`). Then it checks the zip against its pin and copies it
into the cache and unpacks its `amonet/` folder to `~/.cache/jarvis-show/<board>/amonet-<board>-v2.0.1/`,
which `jarvis-show.py` uses when `--amonet-dir` isn't given and there's no bundle in `../third_party/`.
With `--no-browser`, or when no browser can be opened, it prints the link and you run it
again after downloading. A package with no pin yet is not used. The tool
compares every file in it with the public source (R0rt1z2/amonet) and sorts them into:

- files identical to the current upstream branch;
- files identical to an older upstream commit (the report names branch and commit);
- files that differ from every upstream copy it checked;
- binaries the installer already pins elsewhere (unlock payloads, the board's TWRP);
- binaries only the zip carries (preloader, LK, TZ, kaeru, and so on).

Then it stops. The hash goes into `tools/jarvis_crown/assets.py` only after someone has read that
list. The crown and checkers 2.0.1 zips went through this on 2026-10-09 and are pinned.

### Amonet 1.x units

A Show 5 first unlocked with Amonet 1.x has `microloader by xyz` at the start of its boot partition, and
the TWRP gate refuses it. The upgrade the XDA thread gives for that is flashing the 2.x zip in TWRP, and
the installer has a separate command for it:

```sh
adb reboot recovery
python3 tools/jarvis-show.py amonet-upgrade --board checkers
```

It takes the pinned Amonet zip and the pinned LineageOS zip from the cache (`--amonet-zip` and
`--lineage-zip` override that) and refuses either one if it doesn't match its pin. With exactly one Show
in TWRP, it goes through these steps:

1. Checks `ro.product.device` and root, then reads the boot partition. If there's no microloader there,
   it stops with PASS and changes nothing.
2. Resolves every partition the zip's update-binary will write, the same way the update-binary does,
   and stops unless each one is the expected block on this board. On checkers that's `lk_real` → p3,
   `tee1_real` → p4, `tee2_real` → p6, `expdb` → p7, `MISC` → p8, `recovery` → p10, `swdl` → p11, plus
   `mmcblk0boot0` for the preloader. It also checks that p9 is named `boot`, and that every image fits
   its partition (the update-binary's `dd` doesn't check that).
3. Backs up the same partition set as J20 to `backups/<adb serial>/before-amonet2/partitions` and
   verifies it.
4. Asks for `UPGRADE AMONET CHECKERS`. Anything else stops here with nothing written.
5. Pushes the zip, checks its SHA-256 on the device and runs `twrp install`. The update-binary writes
   the preloader, LK, both TEE images, kaeru, TWRP to recovery and swdl, wipes misc and reboots to
   recovery. If TWRP comes back with the same boot id, the update-binary stopped early and the command
   says so.
6. Reads back every written partition and compares it with the bytes in the zip.
7. Writes the `boot.img` from the LineageOS zip straight to p9. The Lineage updater-script writes to
   `by-name/boot`, which doesn't exist while the 1.x renames are in place. The command then checks
   that p9 holds those bytes and that the microloader is gone.

If anything fails after the backup, the error names the backup folder. After a PASS, the normal
`install` flow runs as usual.

The Amonet package stays a local/user-supplied dependency rather than a Jarvis Crown release asset.
Later installer stages must not weaken or bypass this gate.

J18 adds an independent live-device CROWN identity gate. Passing J17 therefore never means a connected
device is accepted for flashing.

## J18 — live Crown identity gate

`identify` runs the J17 host/input preflight first and only then performs read-only fastboot queries:

```sh
python3 tools/jarvis-crown.py identify \
  --amonet-dir /path/to/amonet-crown-v2.0.1 \
  --lineage-zip /path/to/lineage-18.1-...-UNOFFICIAL-crown.zip
```

The live gate requires:

- exactly one device in `fastboot devices`;
- `fastboot -s <serial> getvar product` to return `CROWN`;
- `fastboot -s <serial> getvar unlock_status` to return exactly `true` or `false`;
- all fastboot queries to complete within the installer timeout.

`lk_build_desc` is collected only as diagnostic/payload-selection metadata. Missing LK description is
not enough to reject an otherwise proven Crown because the supplied Amonet Crown bundle has a default
Crown payload.

The J18 implementation cannot issue `flash`, `erase`, `boot`, `reboot`, `oem`, or `flashing`
commands. Wrong product, zero/multiple devices, malformed identity data, command failures, or timeouts
all stop the installer before J19 can invoke Amonet.

## J19 — pinned Amonet Crown unlock wrapper

`unlock` first executes J17 and J18. If the proven Crown already reports
`unlock_status=true`, Amonet is skipped entirely. A locked Show needs `UNLOCK` typed in
before any exploit runs.

Before execution, Jarvis Crown SHA-256 verifies the exact known-good archived bytes for:

- `fastbrick.sh`;
- `profile.sh`;
- `device.prop`;
- `bin/fastbrick.img`;
- `bin/fastboot` and `bin/fastboot32`.

A hash mismatch is fatal. The wrapper runs only the pinned `fastbrick.sh`; it does not duplicate or
rewrite the exploit. When Amonet exits, Jarvis Crown treats the state as unproven until the read-only
J18 gate sees the same fastboot serial, `product=CROWN`, and `unlock_status=true`. A timeout, Amonet
error, serial change, or persistent locked state stops the workflow and requires re-identification
before any retry.

J19 does not flash TWRP, write TECHO5 partitions, or start the rootfs installer. Those operations are
separate later gates.

## J20 — TWRP handoff and recovery backup gate

Jarvis Crown reuses an already-running Crown TWRP whenever possible. This matters because a successful
Amonet fastbrick run normally lands in TWRP already, and reflashing recovery for no reason only adds
risk.

If an already-unlocked Crown is still in fastboot, J20 follows the archived Amonet Crown recovery
sequence exactly, using the SHA-256 pinned `twrp.img`:

1. `fastboot -s <serial> flash recovery twrp.img`
2. `fastboot -s <serial> flash swdl twrp.img`
3. `fastboot -s <serial> reboot recovery`

`fastboot boot` is intentionally not used: TECHO5 documents that Amonet's Crown LK does not implement
it. Before these writes, the live fastboot identity must still be the same unlocked `CROWN` serial.
J20 never writes `lk`, `preloader`, `expdb`, `tee*`, `boot`, `system`, or userdata.

Once TWRP is present, Jarvis Crown requires `ro.product.device=crown`, root ADB, and the expected Crown
boot block before backup. It then saves the same small-partition set TECHO5 protects before install
(p1-p11 except none skipped there, plus p14/p15) and both eMMC boot areas (`boot0`, `boot1`). Every file
must match:

- the block size reported by the device;
- a device-side SHA-256 calculated from the block device;
- a host-side SHA-256 calculated from the received file.

The backup is written to `partitions.partial/`, receives `SHA256SUMS` plus a manifest, is fully
re-verified, and is only then atomically renamed to `partitions/`. A failed/incomplete backup is removed
and can never be mistaken for a reusable completed backup. A pre-existing completed backup is reused
only after all hashes and partition coverage verify again.

This is a **post-Amonet recovery backup**, not a pristine factory/pre-unlock dump. Amonet may already
have replaced recovery/swdl with TWRP by this point. Its purpose is to preserve the known unlocked Crown
state before Jarvis Crown/TECHO5 later formats userdata or converts `system` into the A/B rootfs store.

## J21 — Crown Lineage vendor-driver staging

The LineageOS ZIP remains a vendor-driver source only; Jarvis Crown never requires a first Android boot.
Before any destructive action, the installer verifies the ZIP metadata names `crown` and the live TWRP
unit is the same board.

The known Crown recovery handoff bug is fixed in the fork: after `twrp format data`, the installer uses
TWRP's own `twrp reboot recovery` command rather than generic `adb reboot recovery`. It then waits for
all three conditions before pushing the ZIP:

- ADB reports recovery state;
- `/data` is mounted;
- `/data` passes a real create/remove write probe.

The ZIP is SHA-256 checked after transfer before TWRP installs it. After installation, `system` is
mounted read-only and both MT7668 vendor modules must report the exact kernel ABI Jarvis Crown uses:

- `mt76x8_wlan.ko` -> `4.9.337-g8d928c5176cc`
- `mt76x8_bt.ko` -> `4.9.337-g8d928c5176cc`

If either module is absent or has a different vermagic, the workflow stops in TWRP before the later
slot-store conversion. LineageOS itself is never booted by this path.

## J22 — Jarvis Crown rootfs install/provision wrapper

J22 deliberately reuses TECHO5's proven rescue/slot-store implementation instead of cloning the
partition conversion code.  Jarvis Crown adds a fail-closed wrapper around the only destructive part
of the install.

The wrapper will not construct an install plan until all of these are true:

- J20's atomic `partitions/` backup passes its SHA-256/coverage verification again;
- the backup manifest belongs to the same ADB recovery serial now being installed;
- the boot image is byte-for-byte the known-good Crown v0.8.0 image
  (`cf5a492f7ee7ec16305905c58bf7f0b2e3f3e75521668ca905b9ae1ffb6d0baa`);
- the rootfs matches the exact SHA-256 supplied by the future signed Jarvis Crown release metadata;
- the rootfs contains a regular `etc/jarvis-crown-release.json` marker naming
  `product=jarvis-crown-v1`, `board=crown`, and a non-empty version;
- the operator types exactly `ERASE`.

The archived upstream v0.9.21 rootfs intentionally fails the Jarvis product-marker gate.  J22 therefore
cannot accidentally install an old generic TECHO5 userspace while presenting it as Jarvis Crown.  The
future release/build job will create the marked rootfs; J22 does not build one.

J21 has already installed and checked LineageOS's Crown vendor tree.  The internal
`--jarvis-crown-prestaged` path therefore **does not format userdata or install Lineage again**.  It
mounts/checks the vendor modules once more and then enters the same upstream flow that:

1. copies the wrapper-verified rootfs to userdata and verifies the transfer;
2. saves the current Lineage boot image as the USB recovery path;
3. flashes only the pinned Crown TECHO5/Jarvis boot image to the `boot` partition;
4. boots the rescue environment;
5. refuses to continue if a slot store already exists;
6. saves the unit-specific Lineage vendor tree before `system` is erased;
7. converts only `mmcblk0p12` (`system`) into the A/B slot store;
8. installs rootfs slot A and copies the vendor tree into that slot;
9. provisions the device name, encrypted Home Assistant API key, optional SSH public key and optional
   Wi-Fi credentials, plus whatever of Home Assistant access, DashCast and Music Assistant was given
   (see "Ready at first boot" below);
10. writes `/data/misc/techo5/profile` as `jarvis-crown-v1` and boots slot A.

Jarvis Crown forces `--amazon-logo` on this internal handoff.  That is intentionally named after the
upstream option: it means **do not patch the boot logo**, which keeps `expdb`/kaeru unchanged.  The J22
wrapper never requests a Lineage ZIP, never writes `lk`, preloader, `expdb`, `tee1`, `tee2`, persist,
metadata, recovery, swdl or either eMMC boot area.  J20's earlier TWRP handoff is the only recovery/swdl
write in the one-installer flow.

The pre-staged path also skips the upstream GitHub release lookup completely.  Boot and rootfs are
local wrapper-verified inputs, so the destructive stage cannot substitute an asset fetched from the
network.

Fresh Jarvis behavior (Okay Nabu as the wake word, follow-up turns, stop threshold, streamed `/jarvis-display`, and the
other product defaults) is compiled into the Jarvis Crown rootfs.  J22 intentionally does not create a
large `state.json` that would freeze today's defaults forever; it writes only persistent identity/
credentials and the profile marker.  Future OTA updates therefore retain user settings while still
allowing new defaults to apply correctly to genuinely fresh devices.

## Ready at first boot: Home Assistant, DashCast, Music Assistant

`jarvis-show.py install` asks, before it touches the Show, for everything a unit needs to be useful
the moment it boots:

```
── Wi-Fi ─────────────────────────────────────────────────────────────────────
  The Show joins it on first boot.
    Networks this computer can see; a number picks one:
      1  Home
      2  Home Guest
  ? network name (SSID)
    Enter: pick it on the Show's screen instead
    › 1
  ? passphrase for Home
    It goes onto the Show only; this computer keeps no copy.
    › hidden as you type, Enter to skip
    › once more, to be sure
    To skip this next time, keep it in the desktop keyring:
      secret-tool store --label='Jarvis Show: Wi-Fi' application jarvis-show secret wifi network Home

── Home Assistant ────────────────────────────────────────────────────────────
  ? address, as the Show reaches it
    Like http://homeassistant.local:8123. Leave it empty to skip Home Assistant
    for now.
    › http://10.0.0.2:8123
  ? an admin's username
    Enter: no admin, the Show is then added by hand
    The installer signs in as them to add the Show to Home Assistant, give it
    a user of its own, and set up its room, dashboard and voice. It signs out
    again when the run ends, failed or Ctrl-C too, and keeps neither the
    password nor the sign-in.
    › jade
  ? jade's password
    Only for this sign-in; it is dropped right after.
    › hidden as you type, Enter to skip
  ? two-factor code
    Enter gives up signing in
    From the authenticator app you set up for Home Assistant.
    › 123456
    The Show becomes a Home Assistant user of its own when it is added, and
    gets its token then.

── DashCast ──────────────────────────────────────────────────────────────────
  The dashboard, streamed to the Show.
  ? server, host[:port]
    Enter to skip
    › 10.0.0.3:9555
  ? the DashCast key
    It goes onto the Show only; this computer keeps no copy.
    › hidden as you type, Enter to skip

── Music Assistant ───────────────────────────────────────────────────────────
  The Show plays through it as a Sendspin player.
  ? server IP or name
    Enter to skip
    › 10.0.0.2
  ? a Music Assistant admin's username
    Enter: no sign-in (a token is asked next)
    The installer signs in as them to make the Show a plain Music Assistant
    user of its own, which the lyrics need. It signs out when the run ends and
    keeps neither the password nor the sign-in.
    › jade
  ? jade's password
    Only for this sign-in; it is dropped right after.
    › hidden as you type, Enter to skip
```

### Signing in for the run

For Home Assistant and Music Assistant the installer signs in as an admin, with a username and
password, the way a browser does, and works with that sign-in for the rest of the run. Home
Assistant's two-factor code is asked for when the account has one. A wrong password is said so and
asked again, three times at most; after that, or with Enter at the username, the run goes on without
an admin and the Show is added by hand later. An account that is not an admin is signed out again
right away and refused.

When the run ends it signs out of both again: done, stopped by an error, Ctrl-C, or the terminal
closed. The password is used for the sign-in and dropped, and the sign-in itself is never written
anywhere, not even encrypted. All that is kept while the run lasts is the sign-in's id in
`~/.config/jarvis-show/sign-ins.json` (owner-only), which is no secret: it only says which sign-in
to end. A run killed outright (`kill -9`, a power cut) leaves it behind, and the next run that signs
in as the same user at the same place ends that old sign-in first and says so. The usernames are
remembered with the addresses, so next time the username is already filled in.

With an admin signed in, the Show becomes a Home Assistant user of its own by default (see
`--own-ha-user` below) and gets that user's token, so you make no token for it by hand. Without an
admin you're asked for the Show's own long-lived token instead (made as the user the Show should be:
profile, Security, Long-lived access tokens). When the Music Assistant sign-in is skipped or doesn't
work out (Enter at its username or password, or three wrong passwords), an admin's Music Assistant
token is asked for instead; Enter there too means music plays, but lyrics won't work on the Show.

Music Assistant running as a Home Assistant app makes an account by itself for whoever first opens
it from the Home Assistant sidebar. That account signs in through Home Assistant and has no password
of its own, so a password sign-in is refused until one is set in Music Assistant under Settings,
your profile (8 characters or more). The installer says so after the first refused password. A
long-lived token from the same profile page works too, without setting a password.

The Wi-Fi network is picked by number from the ones this computer sees (NetworkManager's list, when
there is one) or typed by name. The passphrase is asked twice, since nobody sees it, and checked
for WPA's 8 to 63 characters; spaces at its ends are kept. Only the WPA key made from it goes to
the Show, never the passphrase itself, and the Show is on that network from its first boot. Enter
at the network name leaves Wi-Fi to the Show's screen instead.

Each part has a heading, each question a `?` line with what it is for underneath, and what you type
goes after `›`. Hidden answers show nothing as you type. A wrong answer gets a `!` line saying why,
and the question again. Colour and the symbols are left out when the terminal can't show them
(`NO_COLOR`, `TERM=dumb`, or output into a file or pipe); the layout stays the same.

Where a question already has a value (the addresses from last time), it is already on the line:
Enter keeps it, or edit it, or clear the line to leave the setting out. The addresses are remembered in
`~/.config/jarvis-show/defaults.json`, so the second Show is mostly Enter. Secrets are never
remembered by this tool and never put on a command line. Each secret is taken from the first of
these that has it:

1. its switch (`--ha-token-file`, `--ha-admin-token-file`, `--dashcast-key-file`,
   `--wifi-passphrase-file`, `--music-assistant-token-file`);
2. the desktop keyring, through `secret-tool` (or `$JARVIS_SHOW_SECRET_TOOL`; on OpenJade desktops
   it is `~/.local/share/jplane/libsecret/bin/secret-tool`, which the installer finds by itself);
3. a hidden question.

To keep them in the keyring:

```
secret-tool store --label="Jarvis Show: HA token" application jarvis-show secret ha-token
secret-tool store --label="Jarvis Show: HA admin token" application jarvis-show secret ha-admin-token
secret-tool store --label="Jarvis Show: DashCast key" application jarvis-show secret dashcast-key
secret-tool store --label="Jarvis Show: Wi-Fi" application jarvis-show secret wifi network "Home"
secret-tool store --label="Jarvis Show: Music Assistant token" application jarvis-show secret music-assistant-token
```

An admin token from a file or the keyring stands in for the sign-in, for runs nobody answers
questions in (`--no-questions`). Better not keep them at all: answer the sign-in questions instead.
What the Show needs it gets a token of its own for. The Show's own Home Assistant token (`ha-token`,
`--ha-token-file`) is only for a Show that should use a user you made by hand; given, it wins over
the user of its own.

The Music Assistant admin, signed in or by token, is optional and asked for whenever there is a Music
Assistant server. It is used for this run only: to make the Show a plain Music Assistant user of its
own for the lyrics on the now-playing page ([Lyrics](lyrics.md)), and with
`--music-assistant-local-metadata` (below) to switch the online lookups off. The Show keeps only the
token of its own user.

The addresses have switches too (`--ha-url`, `--dashcast`, `--music-assistant`), and
`--no-questions` (or `TECHO5_NO_PROMPT=1`, or no terminal) asks nothing at all.

What happens with the answers:

- **On the Show, at install.** The secrets reach `install-show.py` as owner-only files in a temporary
  folder that is gone when the install ends. It checks each value the way the daemon does, then
  writes `/data/misc/techo5/hass.json` (address and token) and a small first `state.json` with only
  the DashCast server and key and the Music Assistant address (Sendspin on). The daemon fills in
  every other default itself. A `state.json` that is already there is kept as it is.
- **In Home Assistant, right after.** With an admin signed in (or an admin token) and Wi-Fi, the
  tool waits up to five minutes for the Show to come up, then over Home Assistant's REST API:
  1. adds it as an ESPHome device with its encryption key, by its address or else by its `.local`
     name (REST cannot see the discoveries Home Assistant has open, so it starts its own flow; an
     open discovery for the same unit closes by itself once the device is added);
  2. turns on "Allow the device to perform Home Assistant actions";
  3. picks the assistant (which of Home Assistant's Assist pipelines the Show talks to) and the wake
     word. The tool lists what is really there, the pipelines this Home Assistant has and the wake
     words this Show has, and you pick one or press Enter to keep the current one. The pipeline Home
     Assistant has as preferred is marked "(default)", and "preferred" says which one it means
     today. When that is the only pipeline there is, nothing is asked: both choices do the same.
     `--assistant` and `--wake-word` answer ahead of time; `''` leaves one alone. With nobody at
     the terminal and no switch, both stay as they are;
  4. asks which room (Home Assistant area) it stands in and puts its device there, over the
     websocket API since REST has no device registry. The room is how the Show finds the Music
     Assistant speaker whose music it shows, when it is the only one in that area. `--room` answers
     ahead of time; Enter or `''` leaves it where it is;
  5. when the room has no dashboard of its own (by the rules in
     [The room's dashboard](dashboards.md#the-rooms-dashboard)), asks whether to make a small one.
     It holds what someone standing in the room would reach for: up to two thermostats, two
     temperature and two humidity sensors, and the room's lights, blinds and fans, twelve tiles at
     most. Doors, windows, motion, media players and the Show itself are left out. It is made as
     `dashboard-<room>` (for "Küche", `dashboard-kueche`), and a room with nothing on that list gets
     none. Enter is no. `--room-dashboard` makes it without asking, `--no-room-dashboard` never
     asks. A dashboard that is already there is never changed;
  6. asks whether to set up the voice extras, the Home Assistant pieces around the Shows (`--voice-extras`
     says yes ahead of time, `--no-voice-extras` no; Enter is no):
     - the [turn-room-down](voice-setup.md#music-in-the-same-room) blueprint, and for this Show an
       automation from it with its assist satellite, its wake word near-miss event and the media
       players in its room (not other Shows, and a speaker's own entity rather than Music
       Assistant's). A Show that has one already, whatever it is called, keeps it; it only gets the
       near-miss event added when that is missing;
     - when one of the Assist pipelines speaks German, answers that never reach the language model:
       the [volume](voice-setup.md#volume-the-music-or-the-show) blueprint with an automation from it,
       "Wo sind wir?", "Wie warm ist es in der Küche?" (each room's temperature sensor, or "draußen"
       from the weather), "Wie wird das Wetter morgen?", and "Stell einen Wecker auf 7 Uhr" or "Weck
       mich in 20 Minuten", which sets the alarm on the Show that was asked;
     - with Music Assistant in Home Assistant as well, music by voice without the model: "Spiel
       Musik", "Spiel was Ruhiges", "Musik von Queen", "Hör Radio FFH". It plays in the room of the
       Show that was asked, and the scripts behind it (`play_random_music`, `jarvis_play_mood`,
       `jarvis_play_radio`) are there for the model too. Moods find playlists by words in their
       names, so "laute Musik" plays a playlist with "metal" or "rock" in its name if there is one.

     The rooms are matched by their names in English or German (Living Room, Wohnzimmer, Küche,
     Bad, Büro, Flur and so on); a room the sentences don't know is still answered with its own name.
     What the house lacks (no weather, no Music Assistant, no German pipeline) is left out and said
     so. Every automation and script is looked at first. Missing ones are made and identical ones
     left alone. One that differs, because you changed it or an older installer made it, is only
     replaced when you say yes. When several differ, you're asked once (keep all, replace all, or
     ask about each), and every old configuration is saved first in
     `backups/home-assistant/`. Which scripts Assist may use (Settings → Voice assistants → Expose)
     is not touched; expose `jarvis_play_radio` and `jarvis_play_mood` there if the model should
     reach them;
  7. hands it the DashCast server, Home Assistant access and Music Assistant address again through
     its own actions, and turns its Sendspin switch on;
  8. with a Music Assistant admin, makes the Show's own Music Assistant user (`jarvis-show-5` for
     "Jarvis Show 5", not an admin, with a random password nobody keeps) and gives the Show a token
     for it, replacing the one made last time. See [Lyrics](lyrics.md);
  9. sets up the calendars ([Calendars](calendars.md)). When there's someone at the terminal, it
     first asks whether to add a calendar account to Home Assistant. An account never comes in next
     to one Home Assistant has (its calendars would all turn up twice, as `calendar.<name>_2`). When
     there's a CalDAV account already, the question is replace or skip. Replace adds the new account,
     and only once Home Assistant has taken it does it remove the old one and give the new calendars
     the ids the old ones had, so dashboards and Shows that use `calendar.personal` keep working. A
     refused password, or the same account again, leaves the old one as it is. Calendars from a
     `calendar:` block in `configuration.yaml` can't be removed over the API, so then no account is
     added at all; the tool says to remove the block, restart Home Assistant and run it again. Yes or
     replace asks for the CalDAV server's address, the user name and the password (an app password
     where the server has them).
     It fills these into Home Assistant's own CalDAV form, which tries them before saving. The
     password goes to Home Assistant only. The installer doesn't write it anywhere, and the Show
     never gets it. Then it lists the calendars Home Assistant has and asks which one the Show
     shows on its calendar page ("all" offered when there are several; Enter takes all of them,
     or the only one). It hands that to the Show through `calendar_sources`. A Jarvis calendar card on a
     dashboard that points at a calendar Home Assistant doesn't have (the dashboard ships with
     `calendar.personal` as a placeholder) is pointed at the first one picked. Its old configuration is
     saved in `backups/home-assistant/` first. A card on a calendar that is there is left alone.
     `--caldav-url`, `--caldav-user` and `--caldav-password-file` add (or replace) an account without asking,
     `--calendars` says which calendars ahead of time, and `--calendars ''` leaves them alone. A
     server with a self-signed certificate has to be added by hand, with **Verify SSL** off.

  The Show's actions are named after its ESPHome node (`esphome.<node>_dashboard_server` and so on).
  The tool takes the node from Home Assistant, so a Show renamed since it was installed is still
  reached, and says so ("Home Assistant knows the Show as 'kitchen-show'"). While it waits for the
  Show to come online, it says what it waits for and for how much longer.

  The entity ids of a renamed Show keep the name it had on the first day
  (`select.jarvis_show_5_wake_word` on a device now called genbuShow5), and entities added later get
  the room in front (`switch.living_room_genbushow5_ssh`). So the tool asks whether its entities should
  take the new name too. Yes renames the Show's entities, and Music Assistant's player for it when
  that still has the old name, to `genbushow5_...`. Home Assistant keeps their history. Then it points
  every automation, script and dashboard that used an old id, or one of the Show's old actions, at
  the new one. Each is saved in `backups/home-assistant/` before it changes. Only whole ids are
  swapped, so `select.jarvis_show_5_wake_word_2` stays put when only the first one moves. An automation
  saved this way is reloaded by Home Assistant right away. Afterwards Home Assistant checks its
  configuration, the ESPHome entry and the Show's user (`show_genbushow5`) take the new name, and the
  installer records it in `show.json`, so the next run is `--name genbuShow5`. An id that is
  taken already keeps its old name and is listed with `!`. Renaming the user needs Home Assistant's
  owner; with another admin it keeps its old name. The rest of the run goes by the new name.
  `--rename-entities` says yes ahead of time, `--no-rename-entities` no; Enter or nobody at the
  terminal is no.

  The admin sign-ins are used for this and ended when the run ends. Each step looks first at what is already
  there, so running it again changes only what is missing. The last line says it's done, or done
  with how many things to look at, each marked `!` above.
- **In Music Assistant, only when asked.** Music Assistant looks songs, albums and artists up on the
  internet (MusicBrainz, fanart.tv, TheAudioDB, Wikipedia and more) for pictures and biographies.
  That is what you want with a streaming service or an untagged library, so it stays as it is. A
  house whose own pipeline already tags the music can have it stop: `--music-assistant-local-metadata`
  takes a Music Assistant long-lived token (made in Music Assistant under Settings, Profile, Long-lived
  tokens; `--music-assistant-token-file`, the keyring, or a hidden question) and over Music
  Assistant's websocket on port 8095 switches its "online metadata" setting off and disables the
  online-only metadata providers it allows to be disabled (MusicBrainz is not one of them, but the
  setting already keeps it quiet). The token stays on this computer; the Show never gets it. A
  failure here is a warning and does not stop the install; it runs again with
  `home-assistant --name NAME --music-assistant-local-metadata`. Both are ordinary Music Assistant
  settings, switched back on in its own settings pages.

Without Wi-Fi at install (it is picked on the Show's screen instead) or without an admin, the
tool says how to finish later. The same steps run on their own for a Show that is already installed:

```
python3 tools/jarvis-show.py home-assistant --name "Kitchen Show"
```

It knows which Show you mean from the name. As soon as the installer has read a unit's serial it
writes `backups/<serial>/show.json` with the name and board, and `home-assistant` looks the name up
there to find that unit's key. It never guesses: an unknown name, or two Shows installed under the
same name, stops with a list of the Shows it knows (`--serial` or `--key-file` pick one by hand).
The Show's address comes from `--host`, `backups/<serial>/address`, mDNS on this computer, or
Home Assistant resolving `<name>.local` itself, in that order. When the Show is on a different
network than Home Assistant (an IoT VLAN, say), mDNS does not cross, so give it `--host` or a fixed
DHCP address in that file.

### Where the answers come from

The Show is an ESPHome voice satellite. It hears its wake word itself, then streams what you say to
Home Assistant, and the Assist pipeline picked in step 3 does the rest: speech to text, a
conversation agent, text to speech. Whatever a pipeline is built from in that Home Assistant
(Whisper or the cloud, the built-in agent or an Ollama or OpenAI one, Piper or anything else) is what
the Show uses; nothing about it is set on the Show, and changing the pipeline in Home Assistant
changes every Show set to it. "preferred" follows whichever pipeline Home Assistant marks as
preferred.

The pipeline we run with our own Shows, and what made the difference (the model's context window,
Whisper's initial prompt, the wake word sensitivity), is in [voice-setup.md](voice-setup.md).

A Show can also answer without Home Assistant. On its setup page (`http://<show>:8181/setup`) the mode is one of:

- **Home Assistant** (the default): every turn goes through the pipeline above.
- **Automatic**: Home Assistant while it is connected, the direct pipeline when it is not.
- **Direct**: never Home Assistant for speech. Speech to text and text to speech are Wyoming servers
  (`host:port`, such as the faster-whisper and Piper add-ons, which listen on 10300 and 10200), and
  the answer comes from a chat model behind an OpenAI-style endpoint (llama.cpp's server, Ollama's
  `/v1`, and the like), with an optional key, a SearXNG server to look things up, and extra
  instructions. The model acts through the Show's own abilities as tools.

The installer does not set the direct pipeline; it is set on the Show.

## Commands and switches

`python3 tools/jarvis-show.py <command> [switches]`, and `--help` lists the same. Every switch is
optional unless a command says it needs it; what is left out is asked for, taken from the keyring, or
has the default given here.

| Command | What it does | Needs |
| --- | --- | --- |
| `preflight` (the default) | Checks this computer and the input files. Touches no Show. With a Show on fastboot it also says which board it is. | nothing |
| `identify` | Reads the Show on fastboot, read-only: board, serial, whether it is unlocked. | a Show on fastboot |
| `unlock` | Runs Amonet on the Show on fastboot, after you type the confirmation. Does nothing on a unit that is already unlocked. Sends the Show to fastboot first, like `install`. | a Show on fastboot, adb or SSH |
| `install` | The whole install: sends the Show to fastboot (from Android or TWRP with adb, from a running Jarvis over SSH), fetches what it needs, unlocks if needed, recovery backup, LineageOS's drivers, Jarvis, and then Home Assistant (below). Asks everything before it writes. | `--name` |
| `amonet-upgrade` | Moves a unit that still has Amonet 1.x to 2.x, from TWRP ([Amonet 1.x units](#amonet-1x-units)). | `--board`, one Show in TWRP |
| `wifi` | Puts an installed Show on a Wi-Fi network over its USB cable, for when it is offline. | `--wifi` |
| `home-assistant` | Adds an installed Show to Home Assistant and sets it up: the steps above. Safe to run again. | `--name` |
| `boot-logo` | Puts the OpenJade logo in place of Amazon's at boot, over SSH ([boot logos](../tools/boot-logo/README.md)). | `--name` |

### The Show and its files

| Switch | Used by | Meaning |
| --- | --- | --- |
| `--name NAME` | install, home-assistant, boot-logo | The Show's name, as Home Assistant shows it. The installer records it in `backups/<serial>/show.json`, and the other commands find the unit by it. |
| `--board crown\|checkers` | all | Cross-checks the board. Normally it is detected; `amonet-upgrade` needs it, and `install` needs it to carry on from TWRP. |
| `--serial SERIAL` | wifi, home-assistant | Which unit. `wifi` talks to that Show on USB (not needed when only one is attached); `home-assistant` takes its key from `backups/<serial>/`. |
| `--host ADDRESS` | home-assistant, boot-logo, install, unlock | The Show's address, when `backups/<serial>/address`, mDNS and Home Assistant cannot find it (a Show on another VLAN, say). For `install` and `unlock`, where a Show running Jarvis is reached over SSH to send it to fastboot. |
| `--ssh-identity FILE` | boot-logo, install, unlock | The private key that logs in to the Show over SSH. Default `~/.ssh/genbu_<name>` when it is there: each Show has a key of its own, which no other Show lets in. Without either, ssh picks a key the usual way. |
| `--key-file FILE` | home-assistant | The Show's ESPHome encryption key. Default `backups/<serial>/home-assistant.key`. |
| `--lineage-zip ZIP` | install, amonet-upgrade, preflight | LineageOS 18.1 for this board. Default the cached one, which `install` downloads and checks when it isn't there yet. |
| `--boot-image FILE` | install | The board's boot image. Default fetched and checked the same way. |
| `--boot-sha256 HASH` | install | Its SHA-256, only for a boot image the installer has no pin for. |
| `--channel stable\|staging\|dev` | install | The release channel the root filesystem comes from, and the one the Show follows for updates. Default `stable`. |
| `--rootfs FILE` | install | A Jarvis root filesystem of your own instead of the channel's. Needs `--rootfs-sha256`. |
| `--rootfs-sha256 HASH` | install | Its SHA-256. |
| `--amonet-dir DIR` | install, unlock, preflight | The unpacked Amonet 2.x package. Default `../third_party/<board>`, else the asset cache, which `install` fills (through the browser) when it is empty. |
| `--amonet-zip ZIP` | amonet-upgrade | The Amonet 2.x zip. Default the cached one. |
| `--amonet-hashes JSON` | install, unlock | SHA-256s for an Amonet package with no pin built in. |
| `--twrp-sha256 HASH` | install | The same for TWRP. |
| `--work-dir DIR` | install, preflight | Scratch space. Default `../work` next to the repo. |
| `--backup-dir DIR` | all | Each unit's backups, key, name and address. Default `../backups`. Keep it: `boot-logo` needs the saved expdb from here. |
| `--ssh-key FILE` | install | An SSH public key root accepts from the first boot. SSH is switched on with it. |
| `--amazon-logo` | boot-logo | Puts Amazon's logo back instead. |

### What the Show is ready with

These answer the installer's questions ahead of time. All of them work for `install`; `home-assistant`
uses the Home Assistant, DashCast, Music Assistant and choice switches.

| Switch | Meaning |
| --- | --- |
| `--wifi SSID` | The network it joins on first boot. Left out: asked, and Enter leaves it to the Show's screen. |
| `--wifi-passphrase-file FILE` | A file holding its passphrase. Default the keyring, then a hidden question. |
| `--ha-url URL` | Home Assistant's address, as the Show reaches it. |
| `--ha-token-file FILE` | A file holding a long-lived token made by hand for the Show. Without it the Show gets a user of its own. |
| `--ha-admin-token-file FILE` | A file holding an admin token, used for this run instead of signing in as an admin, and kept nowhere. |
| `--own-ha-user` | Insist on the Show getting a Home Assistant user of its own (`show_<name>`, not an admin, local network only) and that user's token. This is what happens anyway with an admin and no `--ha-token-file`; given, a missing admin stops the run instead of falling back to a token made by hand. Its streamed dashboard then shows what that user may see. Running it again gives the user a new password and token and ends the old token; if the admin isn't the owner, the user is made again with a new id. |
| `--dashcast HOST[:PORT]` | The DashCast server. |
| `--dashcast-key-file FILE` | A file holding the DashCast key. |
| `--music-assistant HOST` | The Music Assistant server. |
| `--music-assistant-local-metadata` | Music Assistant stops looking metadata up online and keeps to the library's own tags. Off unless given. |
| `--music-assistant-token-file FILE` | A file holding a Music Assistant admin's long-lived token, used instead of signing in, once for the Show's own user there (lyrics) and the switch above. |
| `--assistant NAME` | The Assist pipeline it talks to, or `preferred`. `''` leaves it alone. |
| `--wake-word NAME` | One of the wake words the Show offers. `''` leaves it alone. |
| `--room NAME` | The Home Assistant area it stands in, by name or id. `''` leaves it alone. |
| `--room-dashboard`, `--no-room-dashboard` | Make a small dashboard for its room when the room has none, or never. Asked otherwise. |
| `--voice-extras`, `--no-voice-extras` | Set up the voice extras in Home Assistant (step 6 above), or don't. Asked otherwise. |
| `--rename-entities`, `--no-rename-entities` | For a Show renamed since it was installed: give its entities, and what uses them, the new name too, or don't. Asked otherwise. |
| `--calendars LIST` | The calendars the Show shows, like `calendar.personal, calendar.familie` (step 9 above). `''` leaves them alone. Asked otherwise. |
| `--caldav-url URL`, `--caldav-user NAME`, `--caldav-password-file FILE` | Add this CalDAV account to Home Assistant, in place of the one it has. The password goes to Home Assistant's form and is kept nowhere here. |
| `--no-questions` | Asks nothing. Only switches and the keyring count. `TECHO5_NO_PROMPT=1`, or running without a terminal, does the same. |

Secrets only ever come from files, the keyring or a hidden question, never from a switch's value
([where each secret comes from](#ready-at-first-boot-home-assistant-dashcast-music-assistant)).
