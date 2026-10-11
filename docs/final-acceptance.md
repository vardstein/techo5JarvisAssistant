# Jarvis Show final acceptance gate

This is the release/install gate for Jarvis Crown v1 and Jarvis Checkers v1.  It is intentionally
not a claim that the product is accepted today.

## Gate A — source and supply chain

- [ ] `tools/validate-jarvis-show.sh` passes completely under Go 1.26, including all Go suites.
- [ ] Python installer/recovery/release-contract suites pass.
- [ ] Git worktree is clean and the release commit is identified.
- [ ] J31–J35 security hardening is complete.
- [ ] J36/J37/J40 code-side deployment regressions are closed; J38/J39 are intentionally deferred to the post-install real-device gate below.
- [ ] J41 current-upstream anti-brick review is complete for Crown and Checkers.
- [ ] J42–J45 Music Assistant failover/group/full-screen music acceptance is complete.
  - [ ] preferred speaker offline at Play start routes to that room's Jarvis Show;
  - [ ] preferred speaker lost mid-playback transfers the active MA queue to the Jarvis Show;
  - [ ] recovered preferred speaker does not pull a failed-over session back mid-song;
  - [ ] next explicit Play re-resolves and returns to a healthy preferred speaker;
  - [ ] a room with no preferred speaker uses its Jarvis Show as primary;
  - [ ] named/whole-home groups substitute unavailable room primaries with their Jarvis fallbacks;
  - [ ] Now Playing reports the real resolved endpoint/room/group, never an unavailable preferred speaker.
- [ ] J46 microphone privacy/control acceptance is complete.
  - [ ] HA software mute cuts captured audio immediately, before history/wake/stream consumers;
  - [ ] HA may always mute, but clearing an active software mute requires the device-local opt-in;
  - [ ] remote unmute is refused unless the native API is provisioned with a real encryption key;
  - [ ] the local touchscreen can clear the reversible software mute without granting HA permission;
  - [ ] a physical Crown/Checkers privacy latch always wins and cannot be released by HA or touchscreen;
  - [ ] HA reports `muted`, `unmuted`, `physical mute active`, and `remote unmute not permitted` truthfully.
- [ ] The release manifest is signed by the Jarvis Show release key and tamper verification fails as expected.
- [ ] Rootfs contains `genbu-show-v1`, version and both supported-board markers.
- [ ] Published release contains only intended Show artifacts; no upstream/legacy publisher path was used.

## Gate B — installer dry-run acceptance

For both `CROWN` and `CHECKERS` mocked targets:

- [ ] auto-detection selects the correct first-generation profile;
- [ ] `CRONOS` and unknown/newer products fail before any write command;
- [ ] device serial stays pinned through every destructive handoff;
- [ ] already-unlocked device skips Amonet;
- [ ] locked device requires the board-specific Amonet proof and confirmation;
- [ ] TWRP recovery is board-matched and verified;
- [ ] complete recovery backup validates before destructive staging;
- [ ] wrong-board Lineage/boot/rootfs assets fail closed;
- [ ] Wi-Fi and Bluetooth module ABI checks pass before slot-store conversion;
- [ ] final destructive confirmation is exact and board-specific;
- [ ] expected USB reboot/re-enumeration cycles survive within their polling budgets;
- [ ] no installer path writes protected bootloader-chain partitions.

## Gate C — first real device per board

Perform this only after Gates A and B are complete and the user explicitly authorizes a build/install.

For each board separately:

- [ ] preserve the complete per-device recovery backup off-device;
- [ ] install the exact signed release being accepted;
- [ ] first boot reaches the expected Jarvis product profile;
- [ ] the starter guide shows on first boot in Obsidian, taps through its four pages (Skip ends it),
      and does not come back after a reboot or an update;
- [ ] correct screen geometry: Crown 1280×800, Checkers 960×480;
- [ ] Wi-Fi, Bluetooth, microphones, speaker, touch and display pass;
- [ ] wake word, Assist, Direct fallback and local Stop pass ([voice-test-phrases.md](voice-test-phrases.md));
- [ ] **J38 installed-firmware display E2E:** after a cold dashboard reload, the real device has no header/sidebar/black-bar regression, keeps its native viewport and edge-touch behavior, does not resurrect stale warm-tab geometry, and leaves the established HA WIND/weather/room/clock card geometry unchanged;
- [ ] Jarvis dashboard loads normally after the J38 cold-session checks;
- [ ] **J39 installed-firmware HA/Klar E2E:** while HA owns the turn, `Was ist ein Taco?` remains informational and an explicit Taco recipe/web-search request follows the intended web/search path rather than becoming a device action;
- [ ] alarms/timers/reminders survive HA outage as designed;
- [ ] DND/privacy/intercom/Drop In/SIP behavior passes;
- [ ] physical mute, touchscreen software mute, HA mute, and trusted/untrusted HA unmute behavior pass;
- [ ] Setup page and first-boot Wi-Fi provisioning pass;
- [ ] music fallback/group/full-screen Now Playing passes;
- [ ] device remains stable for the A/B health-commit window;
- [ ] active slot becomes `good` rather than remaining `trial`;
- [ ] `tools/live-acceptance.py verify` passes using J38/J39 evidence captured from this installed candidate firmware, not an older deployment.

## Gate D — OTA acceptance

From the accepted previous release on each board:

- [ ] stable channel fetches only the stable signed manifest;
- [ ] dev channel fetches only the rolling dev signed manifest;
- [ ] same/older/unrankable release is not installed as an OTA downgrade;
- [ ] update installs only into the inactive rootfs slot;
- [ ] persistent `/data` state survives the update;
- [ ] healthy update becomes `good` after the settle window;
- [ ] deliberately broken trial fails back to the previous good slot;
- [ ] manual `slotctl rollback` works;
- [ ] rescue remains reachable when no slot can boot;
- [ ] ordinary OTA does not rewrite the boot image or bootloader-chain partitions.

## Gate E — release decision

A public stable release may be called accepted only when every applicable box above is checked and
J41 has been rerun against current upstream TECHO5 documentation.  A green mocked test suite by
itself is not equivalent to hardware acceptance.
