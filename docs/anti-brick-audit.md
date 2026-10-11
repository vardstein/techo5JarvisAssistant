# Jarvis Show v1 anti-brick audit

Date: 2026-10-02
Scope: Echo Show 8 1st gen (`crown`) and Echo Show 5 1st gen (`checkers`).
Status: **PASS with WARNs**. No firmware/rootfs release was built or flashed during this audit.

## Current upstream basis

Reviewed against the current TECHO5 documentation and current amonet documentation:

- https://github.com/HuskerMinion/techo5/blob/main/docs/install.md
- https://github.com/HuskerMinion/techo5/blob/main/docs/getting-started.md
- https://github.com/HuskerMinion/techo5
- https://github.com/R0rt1z2/amonet

Additional compatibility cross-check for the retired amonet 1.x boot layout:

- https://github.com/jxlarrea/lineageos-echo-show-camera/blob/main/docs/INSTALL.md

Upstream's important safety assumptions are preserved: board-specific unlock, board-specific boot image, Lineage vendor tree copied before `system` becomes the slot store, TWRP retained as recovery, ordinary updates installed through the inactive rootfs slot, and failed trial slots falling back rather than overwriting the running root.

## PASS — hardware identity is fail-closed

Jarvis Show supports exactly:

- `CROWN` — Echo Show 8 1st gen / C7H6N3
- `CHECKERS` — Echo Show 5 1st gen / H23K37

`CRONOS` is explicitly refused as a newer Show 5 generation, and every unknown product is refused before writes. Automatic board detection is the source of truth; an optional `--board` argument can only narrow/reject the detected board, never override it. The initially detected fastboot serial is pinned across the destructive flow.

## PASS — checkers flashes only pinned boot and TWRP images, never guessed assets

The Checkers boot image is pinned to upstream TECHO5 v1.0.1 `techo5-boot-checkers-v1.0.1.img` (`6fd696dce2592d2237a432e23ed7c032f1643bd1fe475d3094eccd9f7d2079b2`). That hash is listed in the v1.0.1 `manifest.json`, whose signature checks against the upstream release key, and the image's kernel is `4.9.337-g8d928c5176cc`, the ABI the vendor-module gate requires. Checkers TWRP is pinned to `bin/twrp.img` of the reviewed `amonet-checkers-v2.0.1.zip` (`dcc75bb651894c343039d45af73c4d900594762f6f55187c1ebdaf12d0ae59f5`), the image a unit runs in recovery after `jarvis-show.py amonet-upgrade`. The Amonet unlock bytes for checkers stay unpinned, so unlocking a stock unit is still refused; a unit that is already unlocked is moved to 2.x with the upgrade command instead.

## PASS — Crown destructive assets are pinned

The known-good Crown Amonet v2.0.1 bundle, TWRP and boot image are SHA-256 pinned. A modified/missing asset aborts before execution. The Crown unlock wrapper also requires a readable `lk_build_desc` on locked devices before invoking Amonet.

## WARN — the Amonet unlock itself is inherently destructive

This is the one unavoidable high-risk stage for a locked device. Amonet 2.x writes bootloader-class storage including preloader/LK/TEE/expdb as part of its exploit. Jarvis Show does not reimplement those writes; it executes only the pinned board-specific Amonet package after exact product checks and explicit confirmation, then requires the same device to return with `unlock_status=true`. Do not interrupt power during this stage.

For an already-unlocked device the Amonet exploit is skipped entirely.

## PASS — retired Amonet 1.x boot layout is blocked

Amonet 1.x and 2.x boot layouts are mutually incompatible. After TWRP becomes available, Jarvis Show reads the first 1 KiB of the current boot partition before backup/staging. A `microloader` signature is treated as legacy Amonet 1.x and aborts. An unreadable/ambiguous probe also aborts. This protects already-unlocked devices that bypass Jarvis Show's pinned Amonet 2.x stage.

## PASS — TWRP handoff has a narrow write surface

When TWRP is not already running, Jarvis Show may write only:

- `recovery`
- `swdl`

It never uses `fastboot boot`, matching the amonet/TECHO5 constraints. The handoff is board-bound and requires an unlocked identity. Existing board-matched TWRP is reused without fastboot writes.

## PASS — recovery backup precedes Jarvis destructive storage conversion

Before Lineage staging or Jarvis slot-store conversion, the installer takes and verifies the small-partition/eMMC boot-area recovery backup. Host SHA-256 values are checked against device-side SHA-256 values and the completed manifest is board/serial bound.

This is a post-Amonet backup, not a pristine pre-unlock factory backup; Amonet may already have changed bootloader-class partitions.

## PASS — Lineage staging is board- and ABI-bound

The Lineage ZIP must identify the detected board before userdata format. After installation, both Wi-Fi and Bluetooth vendor modules must match the exact Jarvis kernel ABI (`4.9.337-g8d928c5176cc`) before the installer can continue. The proven TWRP handoff is used after format (`twrp reboot recovery` plus writable `/data` gate).

## PASS — Jarvis install cannot patch the bootloader logo

Upstream TECHO5 optionally patches the boot logo in `expdb`/kaeru on supported Shows. Jarvis Show deliberately does not. The prestaged install path requires `--amazon-logo`, and regression tests prove the Jarvis wrapper always supplies it. Therefore the Jarvis-owned install stage does not write `expdb`.

## PASS — final product install write surface is constrained

After all gates and the exact destructive confirmation, the final product installation is limited to:

- `boot` — the board-pinned Jarvis/TECHO5 boot image
- `system` (`/dev/mmcblk0p12`) — converted to the TECHO5/Jarvis A/B rootfs store

Jarvis Show's final install path does not write `preloader`, `lk`, `tee1`, `tee2`, `expdb`, `persist`, `metadata`, eMMC boot0/boot1, or other bootloader-class partitions.

The slot-store creation uses the inherited explicit erase guard (`--i-know-this-erases-it`) and refuses to silently recreate an existing TECHO5 store.

## PASS — ordinary OTA is rootfs-only A/B

Normal OTA calls `slotctl install` only. It does not invoke fastboot, write raw `mmcblk` partitions, or update the kernel/boot image. OTA installs into the inactive slot, marks it `trial 3`, boots it, and only commits it good after the daemon remains healthy for the required period. Exhausted trials are marked bad and the other good slot is selected. Persistent `/data` state survives rootfs OTA.

Kernel/initramfs/boot updates remain a separate maintenance operation and are not silently reachable through ordinary OTA.

## PASS — release identity and rootfs identity are independently checked

Signed manifests are bound to `product=genbu-show-v1`, supported boards, size and SHA-256. Downloaded rootfs archives are then inspected for exactly one matching internal release marker before `slotctl` can touch the inactive slot. Stable/dev channel isolation and downgrade checks remain active.

## PASS — expected reboots/re-enumeration are bounded, not sleep-driven

Installer recovery windows poll for the device after Amonet, TWRP reboot, post-format recovery, fastboot transitions, rescue serial and first Jarvis boot. Individual tool calls have their own timeout so a wedged USB command cannot turn an outer recovery window into an infinite hang.

## WARN — current upstream maturity

TECHO5 currently describes Checkers as tested end-to-end on one unit and Crown as its newest Show port with substantially less field time than the older Show 5 work. Jarvis Show cannot remove that hardware/port maturity risk. Keep recovery backups and mains power during destructive stages.

## WARN — release gates still outside this audit

This anti-brick audit does not waive the remaining physical/server acceptance gates:

- J38: real Crown/Checkers display screenshot acceptance
- J39: deployed HA/Klar Taco/general-information/web-search acceptance
- J42–J45: resilient music routing/full-screen music work

Those are product/release gates, not partition-brick hazards.

## Validation

- 42/42 focused anti-brick/recovery/unlock/flow tests: PASS
- 152/152 full Python/static tests: PASS, with one unrelated local Chromium skip
- security source tests: PASS
- security-sensitive installer/OTA tests: 65/65 PASS
- full Go runtime security tests: **environment blocked**, because the sandbox cannot fetch the repo-required Go 1.26 toolchain; no Go failure was observed
- no rootfs/release tarball built
- no real device written/flashed

## Final result

**PASS WITH WARNINGS.** No Jarvis-owned change reactivates an upstream bootloader-logo patch or turns ordinary OTA into a raw boot/kernel/partition update. The dangerous bootloader work is isolated to the explicitly confirmed, pinned Amonet unlock stage. The installer fails closed on unsupported boards, unpinned Checkers assets, legacy Amonet 1.x layout, invalid backups, wrong Lineage/vendor ABI, wrong rootfs identity, and unexpected device/serial changes.
