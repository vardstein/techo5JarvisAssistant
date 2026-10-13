# Board catalogue: evidence ledger

GENBU-163, 2026-10-13. Every value in `catalogue/` is listed here as one of three things:

- **proven**: a test or check was run in this worktree and its output is below, or a hardware capture in
  the repo shows it (file and line given).
- **claimed**: code, a code comment or doc prose says so, but no capture or test backs it. Code saying
  so is not proof.
- **unknown**: nobody has written it down anywhere.

Two kinds of fact get mixed up easily, so they're kept apart:

1. *The catalogue says the same as today's code.* That's what the parity test proves. It says nothing
   about whether the code is right about the hardware.
2. *The value is true for the real board.* Only a capture from a real unit proves that.

The hash checks prove that a pin is the sha256 of a file on this PC. They don't prove the file is safe
or works on a Show.

## 1. Catalogue equals today's code (all proven)

Proven by `tests/test_board_catalogue.py`, run below (section 5).

| # | Fact | Test |
|---|---|---|
| T1 | Same board set in the catalogue, `boards.PROFILES`, `install.SUPPORTED_BOARDS`, `profiles/genbu-show-v1.json` and `assets.ASSETS` | `test_same_board_set_everywhere` |
| T2 | Every `BoardProfile` field of all three boards matches the catalogue, and a new field fails the test | `test_board_profile_fields` |
| T3 | `profiles/crown-v1.json` and `checkers-v1.json` match, every key accounted for; cronos has no profile file | `test_board_profiles_json` |
| T4 | Download assets (file, url, source, manual, sha256, size) match `assets.ASSETS`; "" and 0 there mean absent here | `test_download_assets` |
| T5 | Boot pins match `install.BOOT_SHA256_BY_BOARD` | `test_boot_pins` |
| T6 | TWRP pins match `recovery.TWRP_SHA256_BY_BOARD` (cronos: none on both sides) | `test_twrp_pins` |
| T7 | Crown's zip pins exactly `unlock.AMONET_UNLOCK_SHA256`; checkers and cronos don't pin all of it, which is why unlock is refused for them | `test_unlock_pins` |
| T8 | `UNLOCK` and `ERASE` are the same on every board, so they can move into step code | `test_confirmations_are_not_board_facts` |
| T9 | Fastboot product and TWRP device lookups give the same board as `boards.py`; inexact names (`crown`, `CROWN `, `Crown`) and unknown boards are refused | `test_product_lookup_matches`, `test_twrp_device_is_the_board_name_recovery_checks`, `CatalogueRefusesTest` |
| T10 | The checker refuses: unknown key, missing key, `unlock: none`, a placeholder identity key, an identity value two boards share, wrong asset kind, sha256 without size, a malformed sha256, member pins on an unpinned zip, id not matching the file name | `CatalogueValidatorTest` |

Not covered by parity, because they aren't read from the catalogue yet: echod's board list and detection,
dashcast's geometry, `install-show.py` `BOARDS` and `LOGO_KAERU`, `boot_logo.py` `KAERU`,
`amonet_upgrade.py` `TARGETS`.

## 2. Board facts against real hardware

### crown (Echo Show 8 1st gen)

| # | Fact | State | Where |
|---|---|---|---|
| C1 | fastboot reports product `CROWN` | claimed | `tools/jarvis_crown/boards.py:29`, `docs/jarvis-show-platform.md:7` |
| C2 | TWRP reports `ro.product.device=crown` | claimed | `docs/jarvis-crown-installer.md:177`; `docs/JOBS-CHECKPOINT.md:179` mentions fixing a real crown TWRP handoff, no log |
| C3 | model "Echo Show 8 1st gen / C7H6N3" | claimed | `boards.py:30` |
| C4 | screen 1280x800 | claimed | `boards.py:32`, `docs/jarvis-show-platform.md:22`; the physical display check (J38) is still open, `docs/anti-brick-audit.md:105` |
| C5 | device-tree compatible | unknown | not captured anywhere |
| C6 | panel `lcm=1-jd936x_wxga_dsi_vdo_crown_st_kd_hsd` (not in the catalogue) | claimed | comment `echod/internal/layout/device_cronos.go:86`, "read off a unit 2026-09-22" |
| C7 | Lineage zip pin is the sha256 and size of the local copy | proven | `check-files`, section 5 |
| C8 | boot v1.0.1 pin is the sha256 and size of the local copy | proven | `check-files` against the main checkout's `build/release-techo5-v1.0.1/`, section 5 |
| C9 | boot v1.0.1 boots on a crown | claimed | `install.py` comment; a crown ran v1.0.3-dev.133 per a note outside the repo, which doesn't name the boot image |
| C10 | Amonet zip pin is the local copy | proven | `check-files` |
| C11 | the 7 member pins (`fastbrick.sh`, `profile.sh`, `device.prop`, `bin/fastbrick.img`, `bin/fastboot`, `bin/fastboot32`, `bin/twrp.img`) are the bytes inside that zip | proven | `check-files` |
| C12 | the zip contains `amonet/bin/crown-kaeru.bin` | proven | `check-files` |
| C13 | `amonet-kaeru` unlock with this zip works on a stock crown | claimed | `unlock.py:21` "known-good archive"; no log in the repo |
| C14 | the pinned TWRP boots on a crown | claimed | as C2 |

### checkers (Echo Show 5 1st gen)

| # | Fact | State | Where |
|---|---|---|---|
| K1 | fastboot reports product `CHECKERS` | claimed | `boards.py:41`, `docs/jarvis-show-platform.md:8` |
| K2 | TWRP reports `ro.product.device=checkers` | claimed | `recovery.py:122` expects it (and lowercases what TWRP says first) |
| K3 | model "Echo Show 5 1st gen / H23K37" | claimed | `boards.py:42` |
| K4 | screen 960x480 | claimed | `boards.py:44`, `docs/jarvis-show-platform.md:23`, J38 open. The screenshots in `docs/screenshots/` are 960x480 but don't say which board took them |
| K5 | device-tree compatible | unknown | |
| K6 | panel `lcm=1-st7701s_wsvga_dsi_vdo_checkers_st_kd_hsd` (not in the catalogue) | claimed | comment `device_cronos.go:85` |
| K7 | Lineage zip pin is the local copy | proven | `check-files` |
| K8 | boot v1.0.1 pin is the local copy | proven | `check-files` |
| K9 | boot v1.0.1 boots on a checkers | claimed | `install.py` comment |
| K10 | Amonet zip pin is the local copy | proven | `check-files` |
| K11 | `bin/twrp.img` member pin is the bytes in that zip | proven | `check-files` |
| K12 | the zip contains `amonet/bin/checkers-kaeru.bin` | proven | `check-files` |
| K13 | in the checkers zip, `fastbrick.sh`, `bin/fastboot` and `bin/fastboot32` are byte-identical to crown's pins; `profile.sh`, `device.prop` and `bin/fastbrick.img` are not; `device.prop` says `DEVICE=checkers` | proven | one-off hash of the local zips, section 5. So crown's unlock pins can't simply be reused for checkers |
| K14 | unlock from stock on a checkers | unknown | not pinned, no evidence; `profiles/checkers-v1.json` says it's refused |
| K15 | the `amonet-upgrade` partition map is right for a real checkers | claimed | `amonet_upgrade.py:56` "mapped on a real unit", no log |

### cronos (Echo Show 5 2nd gen)

| # | Fact | State | Where |
|---|---|---|---|
| R1 | fastboot reports product `CRONOS` | claimed | prose in `docs/hardware.md:473` ("Fastboot reports `product: CRONOS`"), no captured output |
| R2 | TWRP reports `ro.product.device=cronos` | claimed | not captured; R3 is the same property under LineageOS, not TWRP |
| R3 | under LineageOS 18.1, `ro.product.device` is `cronos` | proven | `docs/dumps/cronos-lineage-18.1-bench.txt:2` |
| R4 | screen 960x480 at 59.64 fps | proven | `docs/dumps/cronos-lineage-18.1-bench.txt:828`, `:830` |
| R5 | device-tree compatible | unknown | the dump has none |
| R6 | panel `lcm=1-st7701s_wsvga_dsi_vdo_cronos_st_truly` (not in the catalogue) | proven | `docs/dumps/cronos-lineage-18.1-bench.txt:90` |
| R7 | kernel command line has `androidboot.product=cronos` | proven | same line |
| R8 | model "Echo Show 5 2nd gen" | claimed | `boards.py:57` |
| R9 | Lineage zip pin | claimed | no local copy to hash |
| R10 | boot v0.7.16 pin, and that it boots on a cronos | claimed | no local copy; `install.py` comment |
| R11 | Amonet zip pin | unknown | not reviewed; the catalogue leaves the pin out |
| R12 | the zip contains `amonet/bin/cronos-kaeru.bin` | claimed | `boards.py`; oddly `install-show.py:205` already pins a stock cronos kaeru hash |
| R13 | TWRP pin | unknown | none anywhere |

### All assets

| # | Fact | State |
|---|---|---|
| A1 | the Lineage and boot pins equal what upstream publishes | claimed (`assets.py` comments) |
| A2 | licence of each asset | unknown (all 9) |

## 3. Counts

Board and asset facts (section 2): **15 proven, 22 claimed, 7 unknown.**

- proven: C7, C8, C10, C11, C12, K7, K8, K10, K11, K12, K13, R3, R4, R6, R7
- claimed: C1, C2, C3, C4, C6, C9, C13, C14, K1, K2, K3, K4, K6, K9, K15, R1, R2, R8, R9, R10, R12, A1
- unknown: C5, K5, K14, R5, R11, R13, A2

Catalogue-equals-code facts (section 1): 10 proven.

The short version: every identity key the installer uses today (C1, C2, K1, K2, R1, R2) is only claimed.
No board has a captured device-tree compatible. Crown's and checkers' screen sizes are claimed; only
cronos has a capture.

## 4. Hardware tests that would promote the claims

All for Jade to run, guided, one board at a time. Each output goes into `docs/dumps/` with serials, MACs
and `androidboot.serialno` stripped first, and the ledger row cites the file and line. Only H7 writes to
the unit.

| Test | What to run | Promotes | Writes? |
|---|---|---|---|
| H1 | in fastboot: `fastboot getvar product` | C1, K1, R1 | no |
| H2 | in TWRP: `adb shell getprop ro.product.device` | C2, C14, K2, R2 | no (but getting into TWRP on crown/checkers goes through the existing recovery step) |
| H3 | on a running Show: `dumpsys display \| grep -m1 DisplayDeviceInfo` | C4, K4 | no |
| H4 | on a running Show: `tr '\0' '\n' < /proc/device-tree/compatible` | C5, K5, R5 (and unblocks step c) | no |
| H5 | on a running Show: `tr ' ' '\n' < /proc/cmdline \| grep '^lcm='` | C6, K6 | no |
| H6 | on a running Show: `uname -r` plus the sha256 of the first *size* bytes of the boot partition, compared with the pin | C9, K9, R10 | no |
| H7 | the first `amonet-kaeru` unlock of a stock unit, step by step, log saved | C13, K14 | **yes**, Jade's call per board |
| H8 | a full `tools/hwdump.sh` probe on crown and checkers, after hwdump learns to strip identifiers | fixtures for both | no |

For R9, R11, R13 (cronos zip, Amonet and TWRP pins) there's no hardware test: someone has to download,
review and pin the files first, then `check-files` proves the pin.

## 5. Test output

Run in this worktree on 2026-10-13. Python 3.13.15, `python3 -m unittest` (pytest isn't installed here).
No Go code changed, so `go test` wasn't run.

New tests:

```
$ python3 -m unittest tests.test_board_catalogue -v 2>&1 | sed 's/ (tests.test_board_catalogue.[A-Za-z]*.test_[a-z_]*)//'
test_board_profile_fields ... ok
test_board_profiles_json ... ok
test_boot_pins ... ok
test_confirmations_are_not_board_facts ... ok
test_download_assets ... ok
test_product_lookup_matches ... ok
test_same_board_set_everywhere ... ok
test_twrp_device_is_the_board_name_recovery_checks ... ok
test_twrp_pins ... ok
test_unlock_pins ... ok
test_unlock_policy ... ok
test_explain_names_the_owner ... ok
test_no_guessing_identity_keys ... ok
test_unknown_or_inexact_board ... ok
test_unknown_or_inexact_product ... ok
test_bad_sha ... ok
test_id_must_match_file ... ok
test_identity_placeholder_key ... ok
test_member_pins_need_zip_pin ... ok
test_missing_key ... ok
test_sha_without_size ... ok
test_shared_identity ... ok
test_unknown_key ... ok
test_unlock_outside_closed_set ... ok
test_wrong_asset_kind ... ok
----------------------------------------------------------------------
Ran 25 tests in 0.020s
OK
```

To check that the parity test can fail, I changed crown's width to 1281 and then checkers' TWRP pin by
one digit, one at a time, and put them back:

```
width 1281:  FAIL test_board_profile_fields (board='crown'), FAIL test_board_profiles_json (crown-v1.json)
TWRP digit:  FAIL test_board_profiles_json (checkers-v1.json), FAIL test_twrp_pins
```

Full suite:

```
$ python3 -m unittest discover -s tests -p 'test_*.py'
ERROR: test_actual_archived_techo5_rootfs_is_rejected_as_not_jarvis (test_jarvis_crown_install...)
ERROR: test_pinned_boot_is_exact_known_good_image (test_jarvis_crown_install...)
ERROR: test_plan_forces_safe_prestaged_path_and_keeps_expdb_logo_untouched (test_jarvis_crown_install...)
ERROR: test_plan_passes_show_settings_as_switches_and_files (test_jarvis_crown_install...)
FAIL: test_backup_must_match_current_recovery_serial (test_jarvis_crown_install...)
Ran 717 tests in 5.703s
FAILED (failures=1, errors=4, skipped=24)
```

Those five fail the same way before this change (692 tests, same 1 failure and 4 errors). They need
`build/release-techo5-v1.0.1/` and `build/release-techo5-v0.9.21/`, which are gitignored and only exist
in the main checkout, not in a fresh worktree.

Catalogue check and local hashes (`~/.cache/jarvis-show/{crown,checkers}` and the main checkout's
`build/release-techo5-v1.0.1/`):

```
$ python3 tools/jarvis_crown/catalogue.py check
ok: 3 boards, 9 assets

$ python3 tools/jarvis_crown/catalogue.py check-files <cache>/crown <cache>/checkers <main>/build/release-techo5-v1.0.1
amonet-checkers-v2.0.1: match
  amonet/bin/twrp.img: match
  amonet/bin/checkers-kaeru.bin: present (kaeru, not pinned)
amonet-cronos-v2.0.1: no local copy
amonet-crown-v2.0.1: match
  amonet/bin/fastboot: match
  amonet/bin/fastboot32: match
  amonet/bin/fastbrick.img: match
  amonet/bin/twrp.img: match
  amonet/device.prop: match
  amonet/fastbrick.sh: match
  amonet/profile.sh: match
  amonet/bin/crown-kaeru.bin: present (kaeru, not pinned)
lineage-18.1-20260904-UNOFFICIAL-checkers: match
lineage-18.1-20260904-UNOFFICIAL-cronos: no local copy
lineage-18.1-20260904-UNOFFICIAL-crown: match
techo5-boot-checkers-v1.0.1: match
techo5-boot-crown-v1.0.1: match
techo5-boot-v0.7.16: no local copy
(exit 0)
```

K13, one-off comparison of the two local Amonet zips (first 12 hex digits of each member's sha256):

```
amonet/fastbrick.sh        crown aa93be2a752f  checkers aa93be2a752f
amonet/profile.sh          crown 844e584acc0b  checkers 68d82d7a254b
amonet/device.prop         crown 4313cbc0c8a7  checkers 99d51c82aea8
amonet/bin/fastbrick.img   crown 6b899c291993  checkers 9c486bc2e586
amonet/bin/fastboot        crown cb3d13b85014  checkers cb3d13b85014
amonet/bin/fastboot32      crown 8912e9926cfc  checkers 8912e9926cfc
amonet/bin/twrp.img        crown b6b1446436de  checkers dcc75bb65189
checkers device.prop: DEVICE=checkers   crown device.prop: DEVICE=crown
```
