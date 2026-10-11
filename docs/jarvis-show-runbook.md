# Jarvis Show installation runbook

This runbook installs Jarvis Show on any Echo Show this repository supports:

| Board | Device | Erase phrase |
| --- | --- | --- |
| `crown` | Echo Show 8, first generation | `ERASE CROWN` |
| `checkers` | Echo Show 5, first generation | `ERASE CHECKERS` |

It uses the supported installer only. Don't use manual `fastboot` writes, `dd`, or raw partition
commands. Details of each step are in [jarvis-crown-installer.md](jarvis-crown-installer.md).

## Before connecting the device

The installer runs on Linux with Python 3.10+, `adb`, `fastboot`, `git`, `bash`, and GNU `timeout`.
Recovery needs serial access, so the login has to belong to `dialout` or `uucp`. `getent group dialout`
lists the members. When you're not one, add yourself, then run the installer under the group so you
don't have to log in again:

```sh
sudo usermod -aG dialout "$USER"
sg dialout -c 'python3 tools/jarvis-show.py preflight --board crown'
```

`sg` on its own only works once you're a member. Otherwise it asks for a group password that doesn't
exist. On NixOS, a rebuild can drop a membership added this way, so check again before each install.

Keep the Show on mains power, use a known-good USB data cable, and connect only this one Show. The
installer refuses zero or several devices.

## What the installer fetches

Given no paths, `install` gets everything for the board it detects and checks each file before
using it:

- **LineageOS 18.1 and the board's boot image** are downloaded from GitHub into
  `~/.cache/jarvis-show/<board>/` and kept only when size and SHA-256 match the pins in
  `tools/jarvis_crown/assets.py`.
- **Amonet** is only attached to the XDA threads, which block scripts. The installer opens the link in
  your browser and waits for the zip in `~/Downloads` (log in to XDA if it asks). It holds the zip to
  its pin and unpacks it into the same cache. A cached copy is reused, so this only happens once
  per board.
- **The Jarvis root filesystem** comes from the release channel picked with `--channel`. The installer
  believes the release only with the release key's signature over its manifest, the manifest has to
  name this board, and the download has to match the manifest's hash. The Show then keeps following
  that channel for updates.

`--channel` is `stable` (the default), `staging` or `dev`. Until there is a stable release, pass
`--channel dev`. The installer says so when the chosen channel has nothing.

`--lineage-zip`, `--boot-image`, `--amonet-dir`, and `--rootfs` with `--rootfs-sha256` still take
your own files instead. `python3 tools/fetch-show-assets.py --board <board>` fills the cache ahead of
time, and `--check` checks what is there without downloading.

Run everything from the repository root.

## Preflight and identity

Preflight checks this computer and the input files. With `--board` it doesn't touch the Show
(without it, it reads the board from the Show on fastboot):

```sh
python3 tools/jarvis-show.py preflight --board checkers     # or crown
```

Put the Show into fastboot mode and run the read-only identity check:

```sh
python3 tools/jarvis-show.py identify
```

Carry on only when it reports exactly one device and the board you expect. Stop if the product,
serial or board is a surprise. If an earlier install stopped while the same Show is in verified
TWRP, `install --board <board>` carries on from there without going back to fastboot.

## Install

```sh
python3 tools/jarvis-show.py install --name kitchenShow --channel dev \
  --ssh-key ~/.ssh/id_ed25519.pub
```

`--ssh-key` is optional: it switches SSH on with that key from the first boot.

The installer repeats its host and identity checks, then fetches the files above. Before writing
anything it makes a verified off-device recovery backup in `../backups/<serial>/`. A locked Show also
needs `UNLOCK <BOARD>` typed in. The last step before erasing needs the board's erase phrase from the
table at the top.

Don't pull power or USB while it runs. If it stops, don't just run it again: run `identify` first and
use the recovery and rollback path.

## Reinstalling a Show

A Show running a build from before the release key change can't update itself and needs a
reinstall. Use the same steps and the same `--name`. The
backup from the first install stays where it is, and the new run makes a fresh one.

## After first boot

Check one layer at a time: boot and active slot, network, Home Assistant, display and touch, audio,
then microphone and mute. Then go through [final-acceptance.md](final-acceptance.md): display, voice
routing, music, privacy, reboot, OTA and rollback.
