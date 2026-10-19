# Jarvis Crown v1 — Job Ledger

Rule: complete and validate one job before starting the next. Do not build a release/rootfs tarball until the user explicitly asks.

## Phase A — Baseline and safety

- [x] **J01 — Reconstruct archived TECHO5 baseline**
  - Restore the actual Git repository from `Archive.zip` into this project.
  - Verify exact HEAD commit and worktree state.
  - Inventory bundled Crown boot/rootfs and Amonet assets.
  - Do not modify source behavior yet.

- [x] **J02 — Freeze Crown-v1 product scope and supported hardware contract**
  - CROWN / Echo Show 8 1st gen only.
  - Refuse cronos/checkers/other products in installer.
  - Later: checkers joined in J23C, and cronos in GENBU-185 with its own pins.
  - Add machine-readable product profile and docs.

- [x] **J03 — Third-party asset and redistribution audit**
  - Inventory Amonet/TWRP/boot/Lineage dependencies and licenses.
  - Keep private/local-only assets out of public-release paths where redistribution is unclear.

## Phase B — Voice appliance behavior

- [x] **J04 — Jarvis Crown wake-word profile**
  - `Hey Jarvis` primary maintained microWakeWord model.
  - `Okay Nabu` fallback.
  - Optional Alexa recovery model only if already shipped upstream.
  - One logical wake selector; second slot disabled by policy.
  - Implemented in fork commit `8b62921`.
  - Fresh-device fallback order: `hey_jarvis` → `okay_nabu` → `alexa` → any installed model.
  - 2026-10-10: `Okay Nabu` is the default now, since Hey Jarvis wakes badly for German speakers.
    Order `okay_nabu` → `hey_jarvis` → `alexa` → any installed model. "Hey Genbu" takes over once
    it is trained ([plan](genbu-wake-word-plan.md)).
  - Exactly one wake model active at a time; explicit `No wake word` still survives restart.
  - Static/parser validation passed; Go test execution deferred because this environment has Go 1.23.2 and the repo requires Go 1.26.0.

- [x] **J05 — Alexa-like voice state machine**
  - Idle → wake → listening → processing → responding → follow-up → idle.
  - Silent 6 s follow-up, max 2 automatic turns. (Since 2026-10-10 the default is 0: it listens again only when the reply asks something.)
  - HA-requested continuation still takes precedence.
  - Wake detector remains local; no HA wake re-arm automation.
  - Implemented in fork commit `82cefa1`.
  - Static/parser validation passed; Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J06 — Local stop/barge-in/audio defaults**
  - Preserve AEC and wake-over-playback.
  - Default stop threshold 0.55; playback floor remains 0.50.
  - Local stop ladder audited: ring/alarm → turn/TTS → announcement → media pause → reminder.
  - Active-call far-end wake protection retained.
  - Implemented in fork commit `7a3898a`.
  - Static/parser validation passed; Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J07 — Automatic brain mode**
  - HA Assist first when subscribed/healthy at turn start.
  - Existing Direct Brain fallback when HA is unavailable between turns.
  - No mid-turn backend switch: the selected backend is pinned on the open conversation.
  - Visible conversation state carries the selected backend so display behavior follows the actual turn.
  - Setup UI exposes Automatic / Home Assistant / Direct and refuses incomplete Automatic/Direct fallback configuration.
  - Existing direct timers/alarms/reminders/intercom/radio/calendar/weather/screen/search tools remain unchanged.
  - Implemented in fork commit `2940514`.
  - Static/parser validation passed; Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

## Phase C — Display appliance behavior

- [x] **J08 — Jarvis Display product defaults**
  - Fresh devices default to streamed `jarvis-display`, idle ON, native kiosk/headerless request ON.
  - Persisted user settings override product defaults and survive restart/OTA.
  - Browser-side sidebar/header enforcement remains owned by J09; stale-page lifecycle remains J10.
  - Static/format validation passed; Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J09 — Dashcast Crown appliance mode**
  - `JARVIS_CROWN_MODE=1` forces 1280×800, `/jarvis-display`, and kiosk ON server-side.
  - Device-supplied viewport/path/kiosk values cannot override the Crown appliance contract.
  - Dashcast owns Home Assistant header + sidebar suppression; HA Kiosk Mode / edge CSS hacks are unsupported and unnecessary.
  - Generic Dashcast behavior is unchanged when appliance mode is disabled.
  - Regression tests added for viewport/path/kiosk ownership and browser-chrome ownership.
  - Go/gofmt + injected JavaScript syntax validation passed; Go tests remain blocked by local Go 1.23.2 vs dashcast-required Go 1.26.8.

- [x] **J10 — Dashcast warm-tab generation contract**
  - Warm key includes authenticated device, viewport, path, kiosk/header state, and `JARVIS_CROWN_UI_GENERATION`.
  - Crown mode defaults UI generation to `1`; changing generation can never match an older parked tab.
  - Added one-shot ESPHome `dashboard_reload` action: the device reconnects with authenticated `cold=true`, Dashcast discards the matching parked page, and opens a fresh Chrome tab.
  - Cold reload state is not persisted and is consumed only after the hello is successfully sent.
  - Added regression tests for generation-aware keys, cold flag preservation, warm-pool discard, and one-shot state.
  - Implemented in fork commit `dfeb98b`.
  - Static/gofmt/profile validation passed; Go tests remain blocked by local Go 1.23.2 vs Dashcast-required Go 1.26.8 / echod-required Go 1.26.0.

## Phase D — Alexa-like feature integration

- [x] **J11 — Timers, alarms, reminders experience audit/polish**
  - Audited existing local timer persistence, alarm/snooze persistence, missed-timer restart handling and Crown full-screen ringing/touch-stop behavior.
  - Added strict local German ring Stop commands alongside English without consuming unrelated smart-home/media requests.
  - Added strict local German Snooze commands and common German minute lengths; a snooze still requires an explicit snooze word.
  - Reminder/alarm labels now prefer live HA speech, fall back to configured Direct Wyoming TTS when HA is unavailable, then degrade to local chime + screen only.
  - Implemented in fork commit `6f6fb8b`.
  - Pure local stop/snooze parser tests passed on the available Go toolchain; full package tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J12 — Announcements / intercom / Drop In productization**
  - Existing HA Assist Satellite announcement + `StartConversation` path retained; DND now acknowledges/suppresses proactive HA audio without opening the microphone.
  - Native peer announcements remain local/mDNS-based and now require signed HMAC requests only; Jarvis Crown rejects the legacy plaintext `X-Techo5-House` credential path.
  - DND rejects incoming intercom and mutes incoming house announcements while keeping their screen card; alarms/timers/local wake interactions remain available.
  - Drop In remains explicit opt-in and known-peer-name+address only, requires the existing audible chime to complete before auto-answer, falls back to ringing when the chime is interrupted, and now uses a persistent distinct orange privacy light in addition to the `Drop In` screen.
  - Implemented in fork commit `8d9f030`.
  - gofmt/diff/invariant validation passed; full Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J13 — SIP calling and contacts polish**
  - SIP signaling is TLS-only and media SRTP-only; the legacy TECHO5 `plain` account bit is decoded for compatibility but cannot downgrade transport.
  - Provider certificate validation remains enabled with the SIP host as `ServerName`; TLS 1.2 is the minimum.
  - SIP account files remain owner-only (`0600`).
  - `phone_account`, `phone_contacts` and HA-triggered `phone_call` refuse to carry password/address-book/dialed-number data until the ESPHome API has a real encryption key.
  - Incoming caller-ID/contact labels are sanitized and length-bounded before entering call state; matching local contacts override provider display names.
  - Routine phone logs no longer include peer/number fields; HA call events retain peer identity for automations.
  - Existing Crown incoming/outgoing/connected call UI and wake-word suppression during calls remain intact.
  - Implemented in fork commit `0c34e39`.
  - gofmt/diff/invariant validation passed; full Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J14 — Music / now-playing / Sendspin integration contract**
  - Music Assistant remains the server-side owner of library/search/queue/grouping; Crown remains a Sendspin room endpoint.
  - Fresh installs show full native Now Playing for 30 seconds, then return to Jarvis Display with the native music strip over the streamed dashboard.
  - Existing configs with omitted `music_strip` keep historical Full page behavior, so OTA does not reinterpret an explicit zero-value choice.
  - The strip is rendered over streamed Dashcast frames and owns only visible taps inside its rectangle; every other browser touch/edge gesture continues to Dashcast.
  - Camera/settings/Wi-Fi/drawer/etc. retain higher input priority and cannot be tapped through an invisible strip.
  - Existing Sendspin metadata/artwork/controller/held-pause semantics retained; Music Assistant transport stays server-owned.
  - Implemented in fork commit `c49ac3e`.
  - Config regression tests pass in a temporary lowered-go-directive copy; display target tests are committed/static-clean but full compilation remains blocked by the local Go 1.23.2 / repo Go 1.26 dependency environment.

- [x] **J15 — Camera / doorbell / proactive conversation UX**
  - Added encrypted-API-only HA camera/list/show actions and a first-class `home_doorbell` action.
  - Doorbell popup is visual even under DND/quiet hours; local cue and camera audio are suppressed there.
  - Doorbell/camera actions never open the microphone themselves; HA Assist Satellite `StartConversation` remains the explicit proactive-reply path and J12 DND rules can veto it.
  - Camera entity IDs are constrained and camera names are printable/length-bounded before reaching the screen.
  - Proactive HA announcement/question text is no longer written to routine logs.
  - Explicit regression test proves camera/doorbell temporarily covers a requested Jarvis dashboard and the same dashboard returns afterward.
  - Full Go test execution remains blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J16 — Night mode / DND / bedside behavior**
  - Night and DND remain separate controls: screen policy vs proactive-communication/privacy policy.
  - A ringing alarm/timer now wakes an otherwise dark night screen and is capped to gentler night brightness; sunrise alarms keep their own ramp.
  - Reminder-only events do not light a sleeping room.
  - DND now also turns away incoming SIP calls, in addition to the J12 intercom/Drop In/announcement privacy rules and J15 visual-only doorbell behavior.
  - Alarms, timers, explicit local interactions and outgoing calls remain available under DND.
  - Added regression tests for dark-screen ring wake and SIP DND gating.
  - Full Go test execution remains blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

## Phase E — One installer

- [x] **J17 — Crown installer host preflight**
  - Linux + Python 3.10+, adb/fastboot/git/bash/timeout, USB-serial permissions, ModemManager, writable work/backup storage, and >=8 GiB free space.
  - Implemented as a host-only gate: it never invokes adb/fastboot, enumerates USB devices, or writes to an Echo.
  - Local Amonet input must be structurally complete and declare `DEVICE=crown`; an optional Lineage ZIP must declare `pre-device=crown`.
  - Seven pure-Python regression tests cover wrong/incomplete Amonet, wrong Crown ZIP metadata, missing fastboot, and low disk space.
  - Container rehearsal deliberately failed closed with adb/fastboot absent and confirmed no device command was executed.

- [x] **J18 — Crown identification and fail-closed device gate**
  - Requires exactly one connected fastboot device and queries it by serial.
  - Requires `product=CROWN` and a recognizable boolean `unlock_status`; explicitly rejects every other product/state.
  - `lk_build_desc` is diagnostic only and cannot broaden supported hardware.
  - Device-gate implementation is read-only: regression tests forbid write-capable fastboot verbs and cover zero/multiple devices, timeout, malformed status, wrong product, locked Crown and already-unlocked Crown.
  - 16 combined J17/J18 pure-Python tests pass; no real device I/O or build occurred.

- [x] **J19 — Amonet Crown unlock wrapper**
  - Uses the known-good local Crown Amonet tooling only after J17/J18 pass; critical script/profile/payload/fastboot bytes are SHA-256 pinned.
  - Already-unlocked Crown skips Amonet entirely; locked Crown requires exact `UNLOCK CROWN` confirmation.
  - Amonet success is never trusted alone: the same serial must return as `CROWN` with `unlock_status=true` through the read-only identity gate.
  - Hash mismatch, exploit timeout/error, serial change or unconfirmed unlock fails closed and requires re-identification before retry.
  - 24 combined J17-J19 pure-Python tests pass; no real device I/O occurred.

- [x] **J20 — TWRP + recovery backup orchestration**
  - Reuses already-running Crown TWRP; otherwise flashes only the SHA-256-pinned TWRP image to `recovery` + `swdl` and reboots recovery after re-proving the same unlocked Crown identity.
  - Never uses unsupported `fastboot boot` and never writes `lk`, preloader, expdb, tee*, boot, system or userdata in this stage.
  - Requires Crown/root TWRP and atomically saves p1-p11 plus p14/p15 and eMMC boot0/boot1 before later destructive storage work.
  - Every image is checked for exact block size and must match both device-side and host-side SHA-256; incomplete backups cannot be reused.
  - Existing backups are accepted only after full hash + coverage re-verification.
  - 7 J20 regression tests pass; no real device I/O occurred.
  - Documented caveat: this is a post-Amonet safety backup, not a pristine pre-unlock factory image.

- [x] **J21 — Lineage vendor-driver staging**
  - Requires Crown Lineage metadata before destructive work and uses LineageOS only as the proven vendor/system driver source; Android never needs to boot.
  - Fixes the real Crown TWRP handoff failure by using `twrp reboot recovery` after formatting data and waiting for recovery + mounted/writable `/data`.
  - Verifies the uploaded ZIP by SHA-256 before TWRP installation.
  - Mounts installed system read-only and requires both MT7668 Wi-Fi and Bluetooth modules to match kernel ABI `4.9.337-g8d928c5176cc` before later slot-store conversion.
  - 5 J21 regression tests pass; no real device I/O occurred.

- [x] **J22 — Jarvis Crown rootfs install/provision wrapper**
  - Reuses upstream `install-show.py`/`slotctl` destructive storage path instead of cloning partition logic.
  - Re-verifies J20 backup + target serial, pins the known-good Crown boot SHA-256, and requires an exact SHA-256 plus `jarvis-crown-v1`/`crown` marker for the future Jarvis rootfs.
  - J21 pre-staged mode consumes the already-verified Lineage vendor tree without formatting/reinstalling it and bypasses network release lookup.
  - Forces Amazon-logo preservation so J22 never writes `expdb`; destructive targets stay limited to `boot` and documented `system`/mmcblk0p12 slot-store conversion.
  - Provisions name, HA PSK, optional SSH/Wi-Fi, and persistent `jarvis-crown-v1` profile marker without freezing product defaults into state.json.
  - Exact final confirmation is `ERASE LINEAGE INSTALL JARVIS CROWN`; 14 J21/J22 tests pass; no rootfs/release tarball was built.

- [x] **J22B — Shared Jarvis Show platform / Checkers integration**
  - One shared daemon/rootfs now targets both supported 2019 Shows: Jarvis Crown v1 (`crown` / Echo Show 8 1st gen) and Jarvis Checkers v1 (`checkers` / Echo Show 5 1st gen).
  - Destructive installer inputs remain board-specific and cross-board identity, Lineage, recovery, backup and boot-image checks fail closed.
  - Dashcast appliance geometry is board-owned: Crown 1280x800, Checkers 960x480; both force `/jarvis-display` and the same headerless/sidebarless contract.
  - Added shared `jarvis-show-v1` rootfs marker with explicit supported-board list plus a `jarvis-show` installer front-end.
  - Crown destructive asset hashes remain pinned from the supplied archive. Checkers real flashing remains blocked until trusted Checkers Amonet/TWRP/boot bytes are locally supplied and SHA-256 pinned; synthetic tests do not authorize a real flash.
  - 55 Python installer tests pass across the Crown compatibility suite plus new Checkers/shared-rootfs cases. Dashcast Go tests are written but cannot execute here because the archive requires Go >=1.26 and the local toolchain is 1.23.2.
  - No rootfs/release tarball was built.

- [x] **J23 — One-command installer acceptance tests**
  - `tools/jarvis-show.py install --board crown|checkers` now orchestrates preflight → read-only board identity → optional Amonet unlock → TWRP → verified recovery backup → local boot/rootfs/backup validation → exact destructive confirmation → Lineage vendor staging → pre-staged slot-store install.
  - All boot/rootfs/backup inputs are validated before the first userdata format; a bad local install input never reaches Lineage staging.
  - Added a hidden, board-bound `install-show.py --jarvis-show-stage-lineage-only` mode that requires J20's complete backup, installs/verifies only the Lineage vendor tree, and returns before release lookup, boot flash or slot-store conversion.
  - The same exact product-specific erase phrase gates the first userdata write and the final pre-staged install plan.
  - Tests cover wrong-board identity, locked/unlocked Crown, locked Checkers with synthetic pins, failed preflight/backup/rootfs plan/confirmation/Lineage stage, and board-specific stage-only early return.
  - 68 Python Jarvis installer tests pass; all destructive device interactions in J23 acceptance are mocked. No real device I/O occurred.
  - Checkers real flashing remains fail-closed until trusted Amonet/TWRP/boot digests are pinned.
  - No rootfs/release tarball was built.


- [x] **J23C — First-generation auto-detect installer gate**
  - `jarvis-show install` now reads the fastboot product itself; `--board` is optional and only a cross-check.
  - `CROWN` selects Jarvis Crown v1 and `CHECKERS` selects Jarvis Checkers v1.
  - `CRONOS` is explicitly refused as Echo Show 5 2nd gen (later: identified and installed since GENBU-185; its unlock needs `--amonet-hashes` on the first run); every other unknown product warns that it may be a newer generation and stops with no write attempted.
  - The first read-only fastboot serial is bound to the destructive flow; swapping devices before the second identity gate aborts before recovery/writes.
  - Installer/device-flow regression tests cover second-gen refusal, unknown-product refusal and serial-swap rejection.

- [x] **J23D — Bass / treble voice control contract**
  - Keep the existing HA `Bass`, `Treble` and `Speaker EQ` entities.
  - Add Direct Brain tools for absolute and relative bass/treble commands with the existing safe ±6 dB range.
  - Ensure voice commands such as `mehr Bass`, `weniger Höhen`, `Bass auf +3 dB` and `Equalizer zurücksetzen` have a deterministic local action when Direct Brain owns the turn.
  - Keep one tone-control implementation shared by touchscreen, HA entities and Direct Brain so state/display/DSP cannot diverge.
  - Implemented as shared atomic tone state + Direct Brain `set_speaker_eq`, `set_equalizer`, `adjust_equalizer`, and `reset_equalizer` tools; requests are clamped to the existing safe ±6 dB range.

## Phase F — OTA and releases

- [x] **J24 — Fork-owned OTA identity**
  - New Ed25519 public key in daemon/installer.
  - Private seed kept outside repo.
  - Configurable release repository/base URL.
  - Official release base is `https://github.com/bakanura/techo5JarvisAssistant/releases`; stable uses `/latest/download/manifest.json`, dev uses `/download/dev/manifest.json`.
  - New Jarvis Show Ed25519 trust root is embedded in daemon + installer; the matching private seed is stored outside the Git repo with mode 0600 and was verified against the embedded public key.
  - Git remotes are split as `origin=bakanura/techo5JarvisAssistant` and `upstream=HuskerMinion/techo5`. No release/rootfs tarball was built or published.

- [x] **J25 — Preserve A/B rootfs update/rollback contract**
  - Trial slot, health commit, automatic rollback.
  - Settings/provisioning persist across OTA.
  - Regression coverage proves inactive-slot install, `trial 3` countdown, five-minute health commit, manual rollback, no overwrite of the running root, and persistent `/data/misc/techo5` state across OTA.

- [x] **J25A — Installer reboot/reconnect resilience**
  - Survive expected USB disappearance/re-enumeration across Amonet, TWRP, userdata-format recovery reboot, adb→fastboot, rescue boot and first Jarvis boot.
  - Uses polling deadlines rather than fixed sleeps: 5 min post-Amonet, 5 min TWRP, 5 min post-format recovery, 3 min fastboot, 7 min rescue console, 10 min first boot.
  - Individual fastboot presence probes are bounded so a wedged host USB command cannot hang the installer forever.
  - 19 targeted reconnect/recovery/Lineage tests pass.

- [x] **J26 — Stable/dev channel behavior**
  - Stable uses `releases/latest/download/manifest.json`; dev uses the rolling `releases/download/dev/manifest.json`.
  - Cached manifests are bound to the channel that produced them; switching channels clears the old offer immediately, a failed fetch may only reuse a same-channel cache, and an in-flight fetch for a channel no longer selected is discarded.
  - OTA refuses downgrades and also refuses network OTA from an unrankable/ad-hoc running version instead of guessing ordering.
  - Signed-manifest verification and A/B install/rollback semantics are identical on stable and dev.

- [x] **J27 — Release tooling and CI (NO BUILD YET)**
  - Show-only CI builds/tests the shared ARMv7 daemon; Dashcast publishes under `ghcr.io/bakanura/techo5-jarvis-dashcast`.
  - Canonical Linux release path is `tools/release-jarvis-show.sh`; the inherited generic TECHO5 PowerShell publisher is disabled to prevent accidental Dot/Spot/upstream publication.
  - Tag/manual release workflow can build the shared rootfs, attest daemon+rootfs provenance, write the signing seed only from the GitHub secret, generate/sign the manifest, publish a GitHub release, and advance the rolling dev manifest only forward.
  - `mkrootfs.sh` now embeds `etc/jarvis-show-release.json` with `product=jarvis-show-v1`, both first-gen boards, and the release version so the hardened installer accepts CI-built images.
  - Workflow YAML + shell syntax validated only; no rootfs/release tarball was built and nothing was published.

## Phase G — Validation and docs

- [x] **J28 — Go/Python/shell test suite**
  - Unit tests for profile/wake/backend/dashcast/installer/release behavior.
  - Static/syntax validation available in this environment.

- [x] **J29 — Upgrade/recovery documentation**
  - Upstream sync workflow, rollback, USB recovery, key rotation.

- [x] **J30 — Final install + OTA acceptance checklist**
  - Fresh supported Show (Crown or Checkers) → its Jarvis product via one installer.
  - Subsequent repo release → signed A/B OTA → health commit/rollback validation.

## Phase H — Security hardening

- [x] **J31 — Threat model and exposed-surface inventory**
  - Enumerate listeners, protocols, trust boundaries, credentials, privileged processes and update paths.
  - Classify LAN-only, device-local and internet-facing surfaces.
  - Record explicit security invariants for an IoT-network deployment.
  - Implemented in fork commit `f107b96`.
  - J31 found three release-blocking exposures for J32: unauthenticated Sendspin listener, unauthenticated optional camera/screen HTTP reads, and a persistent global insecure-TLS diagnostic mode.

- [x] **J32 — Network/service hardening**
  - Bind management/debug services to the narrowest interfaces possible.
  - Disable or authenticate unused web/SSH/debug endpoints by default.
  - Review Dashcast, ESPHome, SIP, intercom, setup UI, camera and Bluetooth exposure.
  - Fail closed on missing credentials or malformed peer identity.
  - Sendspin now defaults off/unpaired, requires one exact Music Assistant server IP provisioned over the encrypted HA API, and rejects every other source before WebSocket/session parsing.
  - Camera/screen diagnostic HTTP reads now require the physical-presence setup session; normal HA camera access remains on the encrypted ESPHome camera entity.
  - Persistent certificate-bypass and remote-ADB controls were removed; legacy saved values are migrated one-way to false.
  - Web management responses gained no-store/nosniff/frame-deny/referrer headers and bounded request headers/timeouts.
  - SSH remains off by default, key-only, and cannot listen without both an encrypted HA link and at least one valid key.
  - Static/gofmt/diff validation passed; full Go tests remain blocked by local Go 1.23.2 vs repo-required Go 1.26.0.

- [x] **J33 — Secret, identity and privilege hardening**
  - Audit file permissions and persistence for API keys, Wi-Fi credentials, house secrets and update keys.
  - Ensure logs/support bundles never expose secrets.
  - Reduce daemon/helper privileges where feasible without breaking Crown hardware access.
  - `state.json`, API PSK, HA access, SIP account/contacts, Wi-Fi state and persistent logs now converge to owner-only files/directories on upgrades as well as fresh installs; runtime/provisioning uses `umask 077` and core dumps are disabled.
  - Diagnostics explicitly redact current house/brain/camera/calendar secrets plus Dashcast key, ESPHome PSK and HA token; Bluetooth pairing no longer logs the peer address or numeric passkey.
  - Raw SIP library text, signed media/TTS URLs, private calendar links and calendar event titles were removed from persistent logs/error strings.
  - An ignored device recovery backup containing HA credential material was moved outside the Git checkout into owner-only private storage and remains excluded from source/release archives.
  - `echod` remains root in v1 because it directly owns framebuffer/audio/camera/Bluetooth/Wi-Fi/firewall/SSH/update hardware paths; this residual risk is explicit and mitigated by J32 listener hardening plus J35 segmentation.
  - Validation: 88/88 Python installer tests PASS; shell/Python/static checks PASS; isolated config/redactor Go tests PASS under a temporary lowered Go directive. Full Go suite remains blocked by local Go 1.23.2 vs repo-required Go 1.26.0.
  - Checkpoint commit: `af792cd` (`jarvis show: harden secrets and privileges`).

- [x] **J34 — OTA / supply-chain hardening**
  - Signed manifests now carry `product=jarvis-show-v1` plus explicit first-generation boards; both host installer and device updater reject wrong/missing/duplicate/unsupported product-board identity before install.
  - Downloaded rootfs archives are SHA/size checked and then must contain exactly one matching `etc/jarvis-show-release.json` product/boards/version marker before `slotctl` may touch the inactive slot.
  - `mkmanifest` refuses malformed Jarvis identity and verifies its generated Ed25519 signature against the exact public trust root embedded in devices, catching a wrong CI signing seed before publication.
  - Manual release tooling refuses signing-key symlinks or group/world-readable seed files; Python and Go release readers are regression-checked to use the same public key.
  - Alpine package pins no longer silently float. Every fetched APK is RSA-signature verified using keys from the SHA256-pinned minirootfs before extraction, then control/data hashes are checked; rootfs construction has no untrusted-package fallback.
  - Wake models remain immutable-commit + SHA256 pinned, GitHub Actions are commit-SHA pinned, Go modules remain `go.sum` locked, and GitHub provenance attestations cover daemon + rootfs.
  - J25 A/B rollback and J26 channel/downgrade isolation remain unchanged and covered by the release contract.
  - Validation: 100/100 static Jarvis tests PASS plus signed-APK supply-chain tests. Full update-package Go tests remain blocked by local Go 1.23 vs required Go 1.26 (`strings.SplitSeq` is unavailable locally).
  - No rootfs/release tarball was built or published.

- [x] **J35 — Security regression suite and deployment policy**
  - Dedicated `tools/security-regression.sh` now gates CI and release publication before rootfs inputs are fetched or artifacts are built.
  - Security contract covers wrong/unsupported device identity, Amonet/recovery integrity, A/B rollback, signed/product/board-bound OTA, wrong/replayed house traffic, intercom authentication, Sendspin source-IP admission, private web authorization, SSH/security state and Dashcast handshake authentication.
  - Published `docs/iot-firewall-policy.md`: default-deny IoT segmentation with explicit HA 6053, Music Assistant 8928, Dashcast 9555, HA 8123 and router DNS/NTP/DHCP flows; SSH/web/SIP/Direct Brain remain optional exact-host rules.
  - Same-IoT peer announcements/intercom are explicitly documented as authenticated L2 traffic; AP/client isolation is incompatible with that feature unless peer routing is redesigned.
  - Validation: 11/11 security-contract tests, 61/61 focused installer/OTA security tests and 111/111 full Python/static tests PASS. Runtime Go security suite is CI-gated with Go 1.26.x; local sandbox has Go 1.23.2.
  - No rootfs/release tarball built or published.
## Phase I — Known broken behavior / regression repairs

- [x] **J36 — Web Setup page repair and regression suite**
  - Root cause fixed: first-generation Shows have no action button, but the browser authorization copy told users to press one. Crown/Checkers now direct the user to the existing on-screen `Allow` / `Not now` prompt; Dot keeps its real action-button flow.
  - Setup management round-trip tests on screen builds now authorize through `setup.Answer(true)`, the same path as the touchscreen, rather than a synthetic Action-button event.
  - Read-only management routes explicitly reject state-changing methods; every rendered settings form is statically checked to dispatch through the single CSRF/session-token-checked save endpoint.
  - Brain/listening, Dashcast, update state, alarms/timers, tabs, sessions and diagnostics have browserless HTTP round-trip/regression coverage. Secrets are accepted where required but never rendered back into HTML.
  - Setup/reboot instructions are hardware-aware, and the setup design document now describes the actual Show physical-presence model rather than the old all-devices-have-a-button assumption.
  - Validation: 6/6 focused Setup contract tests and 117/117 full Python/static tests PASS; gofmt + `git diff --check` PASS. Full Go package execution remains blocked locally by repo Go 1.26 vs sandbox Go 1.23.2.

- [x] **J37 — Wi-Fi / first-boot provisioning recovery**
  - Removed the stale installer dependency on LineageOS already having a saved Wi-Fi network; a first boot with no network is explicitly supported.
  - With no saved network, boot keeps `wpa_supplicant`/DHCP available, starts `echod` immediately, and Crown/Checkers automatically open the native Wi-Fi picker after 45 seconds without an IPv4 address.
  - `Settings -> Connections -> Wi-Fi` remains the manual local recovery path even when Home Assistant or mDNS is unavailable.
  - Unified installer now exposes USB serial recovery as `jarvis-show wifi --wifi NETWORK`, delegating to the existing TECHO5 serial recovery path with optional `--serial` and passphrase-file support.
  - Wrong-password recovery preserves/reverts prior working configuration where one exists and allows re-entering the same SSID with a corrected password.
  - Post-install guidance names both on-screen and USB recovery paths; no zeroconf dependency remains.
  - Validation: 6/6 focused Wi-Fi recovery tests and 123/123 full Python/static tests PASS; shell syntax and `git diff --check` PASS.

- [x] **J40 — Known-bug closure pass**
  - Added `docs/known-bug-closure.md`, classifying every recorded original `livingRoomEcho8` failure as FIXED, OPEN GATE, or EXTERNAL with an explicit owner and closure path.
  - Historical Setup/Wi-Fi/TWRP/cross-board/rootfs/wake/warm-tab/security defects are closed in source; HA custom-card geometry, Klar personality, and network NTP remain explicitly external responsibilities rather than duplicated firmware behavior.
  - J38 physical display acceptance and J39 deployed HA/Klar Taco routing remain intentionally OPEN release gates; the closure pass does not convert either into a paper PASS.
  - J41 anti-brick and J42–J45 resilient/full-screen music are explicitly tracked as pending product work, not hidden known bugs.
  - Validation: 4/4 closure-contract tests and 137/137 full Python/static tests PASS (1 unrelated local-Chromium skip); `git diff --check` PASS.


- [x] **J41 — Final Crown + Checkers anti-brick / upstream safety audit (RELEASE BLOCKER)**
  - Re-read current TECHO5 install/getting-started guidance plus current amonet documentation and compared Jarvis Show against the documented Crown/Checkers safety assumptions.
  - Added a hard TWRP gate for the retired Amonet 1.x `microloader` boot layout; ambiguous/unreadable boot layout now fails closed before backup, Lineage staging or Jarvis boot writes.
  - Locked Jarvis prestaged installs to `--amazon-logo`, so the optional upstream `expdb`/kaeru boot-logo patch cannot be reactivated through the Jarvis-owned final install path.
  - Locked-device Amonet invocation now also requires readable `lk_build_desc`; unsupported/newer products, cross-board devices, changed serials and unpinned Checkers destructive assets remain fail-closed.
  - Verified Jarvis-owned TWRP writes are limited to `recovery` + `swdl`; final product writes are limited to board-pinned `boot` plus `system` -> slot store. Ordinary OTA remains `slotctl install` into the inactive rootfs slot and cannot reach fastboot/raw partitions.
  - Written report: `src/docs/anti-brick-audit.md`, final result **PASS WITH WARNINGS**. Main residual warning is the inherently destructive Amonet unlock itself; Jarvis isolates it to the pinned, explicitly confirmed unlock stage rather than reimplementing it.
  - Validation: 51/51 focused anti-brick/recovery/unlock/multiboard tests PASS; 152/152 full Python/static tests PASS with one unrelated local-Chromium skip; security source + 65/65 sensitive installer/OTA tests PASS. Full Go runtime security suite remains environment-blocked because Go 1.26 cannot be fetched in this sandbox.
  - No rootfs/release tarball built and no real device written/flashed during the audit.

## Phase J — Resilient music routing and full-screen playback

- [x] **J42 — Area-primary speaker fallback routing**
  - Added one persisted preferred Music Assistant player per Jarvis Show room; empty means the Show itself is primary. HA/Klar (`music_play`) and Direct Brain (`play_music`) use the same resolver.
  - A preferred player is usable only when its HA state is online and identifies as a Music Assistant player; unavailable/unknown/misconfigured primaries fall back to this Show's own MA player.
  - If the preferred player disappears while it was actively playing, Jarvis asks Music Assistant to `transfer_queue` to this Show with autoplay, preserving the active queue when the server can still transfer it.
  - Failover is sticky: a recovered preferred speaker never pulls the currently playing fallback session back mid-song.
  - On the next explicit Play request the room is resolved again; if the preferred player is healthy, the old local fallback is stopped before the new request starts on the preferred output. If it is still offline, playback remains local.
  - Music Assistant stays the queue/synchronization owner; Jarvis does not create a competing group engine.
  - Validation: 5/5 focused routing-contract tests and 157/157 full Python/static tests PASS with one unrelated local-Chromium skip. Full Go package execution remains blocked locally by the Go 1.26 toolchain requirement.

- [x] **J43 — Dynamic whole-home / named-group playback resolution**
  - Added persisted, bounded room/group routing definitions with aliases such as `wohnung`, `ganze wohnung` and `überall`; every remote room must explicitly name its Jarvis Show MA fallback, while this device may auto-discover its own fallback player.
  - Every explicit named-group Play re-resolves each room independently: preferred Music Assistant player when online/valid, otherwise that room's Jarvis Show fallback. Rooms with neither output are skipped; an entirely unavailable group fails rather than pretending playback started.
  - Jarvis asks Home Assistant/Music Assistant to build the temporary synchronized group via `media_player.join`, then starts the queue with `music_assistant.play_media`. No competing Sendspin/group engine was added to `echod`.
  - HA/Klar (`music_play_group`) and Direct Brain (`play_music` with optional `group`) use the same resolver. Before a later explicit Play, Jarvis dismantles only the temporary group it previously created; arbitrary user-owned MA groups are left alone.
  - The resolved group name and actual member entities are retained as observational state for J44's full-screen Now Playing surface.
  - Validation: 11/11 focused J42/J43 routing tests PASS; full static Jarvis Show gate PASS with 163 Python tests (1 unrelated Chromium skip), 65 sensitive installer/OTA tests and profile/workflow/shell/diff checks. Full Go execution remains environment-blocked by the Go 1.26 requirement.

- [x] **J44 — Full-screen music dashboard and persistent Now Playing**
  - Music Assistant now owns a dedicated native full-screen surface for the entire active/paused session; MA playback no longer times itself down into the legacy strip and cannot be swipe-dismissed while the session exists. Radio retains the legacy configurable strip behavior.
  - The surface follows the resolved J42/J43 route even when audio is on an external preferred speaker: HA/MA metadata fills title/artist/album/progress/output state, Sendspin remains the low-latency metadata/art source when this Show is an output, and external player `entity_picture` is fetched through the existing bounded cover-art decoder.
  - It shows artwork, title, artist, album, actual output/named group, live resolved rooms, elapsed/total progress when exposed, and the next queue item via the supported `music_assistant.get_queue` response action. Queue lookup is background/best-effort and cannot interrupt playback.
  - Previous/play-pause/next/Done and Favorite now target the real routed MA entity, including external room-primary speakers. The existing MA favorite-current-song operation remains the safe library mutation; no guessed "add to album" write was invented because HA/MA does not expose a stable album-mutation action for this use.
  - Alarm/timer ringing, calls, setup/security prompts, settings and camera pages retain their existing higher display priority. When playback really ends, the route state stops requesting Now Playing and `/jarvis-display` naturally resumes.
  - Validation: 7/7 focused J44 tests PASS; full static Jarvis Show gate PASS with 170 Python tests (1 unrelated Chromium skip), 65 sensitive installer/OTA tests and security/profile/workflow/shell/diff checks. Full Go execution remains environment-blocked by Go 1.26.

- [x] **J45 — Music failover / group regression suite**
  - Added runtime Go regressions for primary-offline-at-start, primary-loss-mid-playback with `transfer_queue`, sticky fallback after primary recovery, next-command restoration to the recovered primary, a room with no preferred speaker using its Jarvis Show as primary, and named whole-home substitution of offline room primaries.
  - Added Now Playing assertions that the displayed entity/output/room/group comes from the resolved live route and never reports an unavailable preferred speaker.
  - Fixed a J44 compile-structural gap by adding the missing cached `picture` field used by external MA `entity_picture` handling.
  - Final acceptance now names every J45 failover/group scenario explicitly.
  - Validation: J42-J45 focused Python/static contracts PASS; full static Jarvis Show gate PASS with 174/174 Python/static tests (1 unrelated Chromium skip), 11/11 security tests, 65/65 sensitive installer/OTA tests, profile/workflow/shell/diff checks. The new Go runtime regressions are source/gofmt clean but cannot execute locally because the sandbox has Go 1.23.2 and cannot fetch the required Go 1.26 toolchain/modules; CI/release must run them under Go 1.26.x.

- [x] **J46 — Microphone mute responsiveness + trusted HA remote-unmute permission**
  - Split microphone privacy into a reversible software cut for Home Assistant/touchscreen and the existing physical privacy latch; either one makes the effective microphone state muted.
  - HA mute is always accepted and cuts captured audio before history, wake detection or streaming on the next frame; local physical mute also uses a transition cut while the ~1.4 s latch catches up.
  - Remote HA unmute is fail-closed and requires both the device-local **Allow trusted Home Assistant to clear its software microphone mute** setting and a provisioned encrypted ESPHome/native API key. The permission defaults OFF and is only exposed on the physically authorized Setup page, not as an HA/Direct Brain entity/tool.
  - Crown/Checkers physical privacy remains stronger: HA and touchscreen never call the hardware unmute path, and a latched hardware mute still requires the physical device button to release it.
  - The touchscreen has an always-local reversible software mute/unmute path, so a default-denied HA unmute cannot strand the device in software mute.
  - HA now exposes `Microphone mute status` with `muted`, `unmuted`, `physical mute active`, and `remote unmute not permitted` states.
  - Runtime security regression now includes the mute package; added Go policy/status tests plus six J46 source-contract tests and final-release acceptance checks.
  - Validation: 11/11 security-contract tests PASS; 65/65 security-sensitive installer/OTA tests PASS; 180/180 Python/static tests PASS with one unrelated Chromium skip; profile/workflow/shell/diff checks PASS. Full Go runtime execution remains blocked locally by Go 1.23.2 vs required Go 1.26.x; a temporary lowered-directive attempt could not fetch the missing `go-esphome-device` module in this sandbox.
  - Checkpoint commit: `0e63128` (`jarvis show: harden microphone remote unmute`).
  - **Superseded by GENBU-166 (2026-10-13):** Jade decided on one mute state. The software mute and the remote-unmute permission are gone; HA and the touchscreen engage the privacy latch, only the button releases it, and an old saved software mute becomes the latch on start-up. Tests: `echod/internal/feature/mute/remote_test.go`, `tests/test_j46_mute_contract.py`.

- [x] **J47 — JODS visual-system port / White Jade default**
  - Replace the upstream-looking fresh-device theme with the JODS design language used by the user's other appliance/admin surfaces.
  - Fresh Jarvis Show installs default to **White Jade**.
  - Native Crown/Checkers UI exposes only the canonical JODS palette family: White Jade, Leaf Jade, Sakura Jade and Ember Jade. Legacy TECHO5 presets were removed; an old saved preset name migrates fail-soft to White Jade. User-defined Custom colors remain supported.
  - Browser Setup uses the same JODS visual language: light layered canvas, soft translucent cards, large radii, restrained shadows, pill actions, soft accent states and JODS typography/spacing conventions.
  - Keep Jarvis branding; reuse the JODS design system, not JODS product naming/logos. The daemon-rendered boot splash and empty-name fallbacks now identify as **Jarvis Show** instead of exposing the upstream TECHO5 wordmark.
  - The configured palette is applied before the first-run welcome/splash path can render, preventing the compiled-in legacy TECHO5 colors from leaking onto a fresh device before the normal idle frame.
  - Added source-contract/regression coverage for the canonical JODS palettes, White Jade default, Setup design language, first-run theme ordering and post-install J38/J39 placement.
  - Validation: 11/11 security-contract tests PASS; 65/65 security-sensitive installer/OTA tests PASS; 190/190 Python/static tests PASS with one unrelated Chromium skip; profile/workflow/shell/diff checks PASS.

## Phase K — Final post-install real-device E2E acceptance

These are deliberately **after installation of the updated firmware**. They are not pre-install paper
gates and must not be marked complete from static tests, screenshots of old firmware, or mocked HA.

- [ ] **J38 — Installed-firmware display E2E acceptance**
  - CODE GATE COMPLETE: Dashcast remains the single browser-chrome owner, fixed to Crown 1280x800 / Checkers 960x480, `/jarvis-display`, kiosk, generation-bound warm tabs and one-shot cold reload.
  - After installing the candidate firmware, cold-load the dashboard on a real Crown and Checkers.
  - Confirm no black bar/header/sidebar, correct native viewport, edge touch behavior, no stale warm-tab geometry, and unchanged external HA WIND/weather/room/clock custom-card geometry.
  - Record fresh evidence with `tools/live-acceptance.py`; evidence from the currently installed older firmware does not count.

- [ ] **J39 — Installed-firmware HA/Klar voice/web E2E acceptance**
  - FIRMWARE GATE COMPLETE: Direct Brain separates information questions from device actions and explicit web/search requests use the SearXNG/read-page path when configured.
  - After installing the candidate firmware, keep HA/Automatic Brain ownership active and test `Was ist ein Taco?` plus an explicit Taco recipe/web-search request against the deployed HA/Klar pipeline.
  - The general question must remain informational; the explicit web request must use the intended search path; neither may misroute into arbitrary device actions.
  - Record the deployed HA-owned transcript/log with `tools/live-acceptance.py`; mocked/fake-LLM evidence does not count.
