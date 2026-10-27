# Jarvis Show upgrade and recovery

This document applies to both supported first-generation targets:

- **Jarvis Crown v1** — Echo Show 8 1st gen (`CROWN` / `crown`)
- **Jarvis Checkers v1** — Echo Show 5 1st gen (`CHECKERS` / `checkers`)

It deliberately separates ordinary rootfs OTA recovery from boot/kernel and bootloader recovery.  Do
not treat those as interchangeable operations.

## 1. Keep the fork and upstream separate

The repository uses:

```text
origin   https://github.com/vardstein/techo5JarvisAssistant.git
upstream https://github.com/HuskerMinion/techo5.git
```

Before taking an upstream TECHO5 update:

```sh
git fetch origin
git fetch upstream
```

Review the upstream range before merging it into Jarvis Show.  Published Jarvis Show history should
not be rewritten merely to make the upstream relationship look cleaner; merge upstream deliberately
and resolve the Jarvis deltas in the normal branch history.

Pay special attention to changes touching:

```text
tools/install-show.py
tools/jarvis-show.py
tools/jarvis_crown/
tools/linux/init
tools/linux/slotctl
tools/linux/mkrootfs.sh
echod/internal/feature/firmware/
dashcast/
catalogue/
```

Any upstream change that alters partition names, boot/recovery handling, `slotctl`, updater trust,
manifest semantics, or Crown/Checkers hardware assumptions requires a fresh anti-brick review under
J41 before release.

Run the complete Jarvis Show validation suite after every upstream integration:

```sh
tools/validate-jarvis-show.sh
```

A release is not acceptable until the mandatory Go suites also pass under the repository's required
Go 1.26 toolchain in CI.

## 2. Ordinary OTA recovery is A/B rootfs recovery

Normal Jarvis Show OTA updates install only into the inactive rootfs slot.  A new slot starts as
`trial 3`; the initramfs consumes one try each boot and the rootfs becomes `good` only after the
daemon has run continuously for the health-settle period.

Inspect the current state from a Jarvis Show shell or rescue console:

```sh
STORE=/store slotctl status
```

The normal recovery commands are:

```sh
STORE=/store slotctl rollback
STORE=/store slotctl status
reboot
```

`rollback` marks the running slot bad and selects the other good slot.  It is the preferred manual
recovery from a bad rootfs release.

Other diagnostic/recovery controls are:

```sh
STORE=/store slotctl switch a
STORE=/store slotctl switch b
STORE=/store slotctl rescue
STORE=/store slotctl rescue off
```

`switch` is an expert recovery tool, not an update mechanism.  Switching to a previously bad slot
puts it back on trial.  `rescue` requests the initramfs on the next boot.

Persistent user/product state lives under `/data`, outside both rootfs slots, so a rootfs rollback
must not erase Wi-Fi, Home Assistant pairing, SSH authorization or ordinary Jarvis state.

## 3. A partial install that reaches RESCUE is not a brick

TECHO5 intentionally boots its initramfs rescue environment after the Show boot image has been
flashed but before a bootable slot exists.  The screen says **RESCUE** and USB serial remains the
primary recovery path.

On Linux, the console is normally `/dev/ttyACM0` at 115200 baud.  Once at the root prompt:

```sh
STORE=/store slotctl status
```

Do not blindly restart the destructive one-command installer merely because ADB no longer sees the
unit.  After the TECHO5/Jarvis boot image takes over, rescue is expected to be reached through USB
serial rather than Android ADB.

Jarvis Show's installer has stage-specific reconnect budgets for Amonet, TWRP, post-format recovery,
fastboot, rescue and first boot.  Let the installer finish those polling windows rather than manually
power-cycling a device during an expected USB re-enumeration.

## 4. TWRP remains the Android/Lineage recovery route

The Amonet Show flow leaves TWRP in `recovery` (and, where required by the board flow, the matching
`swdl` recovery target).  Jarvis Show does not use `fastboot boot` on these devices.

To abandon Jarvis Show and restore LineageOS:

1. Enter the board's existing TWRP recovery.
2. Use the **exact LineageOS ZIP for that board** (`crown`, `checkers` or `cronos`).
3. Flash that ZIP, which recreates Android `system`.
4. Restore the saved Lineage boot image from the install backup for that same unit.

Jarvis Show's installer preserves the Lineage boot image under the per-device backup directory,
conceptually:

```text
backups/<device-serial>/boot-lineage.img
```

Never use one board's Lineage or boot image on another (Crown, Checkers and Cronos each have their own).

To go all the way back to Fire OS on a Cronos, see [hardware.md](hardware.md#back-to-fire-os). That
route is from the XDA thread and hasn't been tried here.

## 5. Keep the recovery backup offline

Keep each device's complete installer backup somewhere other than the Show itself.  At minimum keep:

```text
backups/<device-serial>/partitions/
backups/<device-serial>/boot-lineage.img
backup manifest / checksums
board + serial metadata
```

Also keep whatever non-secret information is needed to identify the matching release and Lineage
image.  Home Assistant encryption keys and other credentials should remain in the user's normal
secret-management/HA backup process rather than being added to a public troubleshooting bundle.

The small-partition/eMMC backup is a safety artifact, **not** a routine restore recipe.  Writing
`preloader`, `lk`, `tee*`, eMMC boot areas or other bootloader-chain partitions incorrectly can make
recovery substantially harder.  Restore those only as part of a board-specific, reviewed unbrick
procedure.  J41 must verify any such procedure against current upstream Crown/Checkers guidance.

On a **Cronos** there is no such procedure: **LK, preloader, TZ and TEE1 are never written back, in
any mode.** It has no BROM to come back from if one of them is wrong. Its only way back from a brick is
the preloader download port with mtkclient ([hardware.md](hardware.md#unbrick)), and even that doesn't
write those four.

## 6. Boot/kernel updates are not ordinary OTA

The rootfs A/B mechanism does not provide an A/B boot partition.  Therefore normal Jarvis Show OTA
must not silently replace the kernel/initramfs boot image.

A boot/kernel change requires a separate maintenance operation with:

- exact board identification again;
- a board-specific pinned boot image;
- current recovery backup present;
- explicit user confirmation;
- current anti-brick review (J41).

Until Jarvis Show gains a genuinely rollback-safe boot-image design, rootfs OTA and boot updates stay
separate.

## 7. Stable/dev release recovery

Stable and dev manifests use the same Jarvis Show signature verification and the same A/B installer.
Changing channels clears the cached offer and a manifest fetched for one channel cannot be installed
through the other.

A failed rootfs release is recovered by `slotctl`/automatic A/B fallback, not by deleting persistent
state or reflashing the device.

Do not publish an older version number as an attempted rollback.  Devices intentionally refuse
network downgrades.  Use the slot rollback mechanism for an immediate bad-release recovery and then
publish a newer fixed release.

## 8. Signing-key rotation

The current Jarvis Show updater embeds one Ed25519 release public key.  Its corresponding private seed
must never be committed to the repository, rootfs or public release assets.

With a single trusted key, safe key rotation is a bridge process:

1. Build a bridge Jarvis Show release that contains the **new public key**.
2. Sign that bridge release with the **old private key**.
3. Roll it out and verify the supported devices have installed it.
4. Only then begin signing subsequent releases with the new private key.

A device that misses the bridge cannot authenticate a release signed solely by the new key and needs
a separately authenticated/manual recovery path.

A seamless overlapping old+new trust window is intentionally deferred to J34; do not revoke the old
key early and do not claim the current single-key updater supports transparent rotation.

If the private signing key is suspected compromised, stop publishing releases immediately and treat
key recovery as an incident, not an ordinary version bump.

## 9. Interrupted installer rule

At any interruption, first determine the current stage instead of restarting from the beginning:

- **fastboot / hacked fastboot** — identify the exact serial/product again;
- **TWRP** — inspect whether Lineage staging and the verified recovery backup already completed;
- **RESCUE** — inspect `STORE=/store slotctl status` over USB serial;
- **booted Jarvis slot** — inspect slot state before installing anything else.

The one-command installer deliberately records/verifies the board, serial, backup, Lineage staging,
boot image and rootfs before crossing its destructive gates.  Recovery should preserve those facts
rather than bypassing them.

## 10. Release recovery checklist

Before declaring an upgrade/recovery path production-ready:

- installer and OTA Python regression suites pass;
- all Go tests pass under Go 1.26 in CI;
- signed-manifest tamper tests pass;
- A/B trial, commit, rollback and rescue tests pass;
- Crown and Checkers assets cannot be cross-used;
- current upstream TECHO5 install/recovery documentation has been re-reviewed under J41;
- no ordinary OTA path writes bootloader-chain partitions;
- no ordinary rootfs OTA writes the boot image.
