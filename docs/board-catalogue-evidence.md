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

## 1. The installer gives what it gave before (all proven)

Stage b moved the installer onto the catalogue and deleted `tools/jarvis_crown/boards.py` and
`profiles/`. To prove that changed nothing, `tests/fixtures/make_board_catalogue_baseline.py` was run at
7d601d0, the last commit where the old owners existed, and its output was committed as
`tests/fixtures/board-catalogue-baseline.json` (55cdf4a). Running it twice gave byte-identical files
(`cmp`). `tests/test_board_catalogue.py` feeds the catalogue-backed code the same inputs and expects the
same answers, run below (section 5).

| # | Fact | Test |
|---|---|---|
| P1 | All three profiles give the same fields and values as `boards.PROFILES` did, in the same board order; `BoardProfile` lost only the two phrases | `test_profiles`, `test_profile_fields_are_the_old_ones_minus_the_phrases` |
| P2 | `UNLOCK` and `ERASE` were the same on every board, and are now `unlock.CONFIRM_PHRASE` and `install.FINAL_CONFIRM_PHRASE` | checked inside every profile comparison |
| P3 | `profile_for_board` and `profile_for_product` give the same profile, or the same `ValueError` with the same text, for 9 inputs each, including `Crown`, ` CHECKERS `, `crown`, `Cronos`, empty and unknown names. Case folding is kept (open question Q8) | `test_lookups_including_case_folding` |
| P4 | fastboot product to board map, `KNOWN_UNSUPPORTED_PRODUCTS`, `SUPPORTED_SUMMARY`, `SUPPORTED_PRODUCTS` and `unsupported_product_message` for 9 inputs | `test_product_to_board`, `test_device_gate` |
| P5 | `install.py`: the boot pins per board and the three named ones, `SUPPORTED_BOARDS`, `PROFILE_ID`, `FINAL_CONFIRM_PHRASE` | `test_install_constants` |
| P6 | `recovery.py`: `TWRP_SHA256` and the TWRP pins per board (cronos: none) | `test_recovery_constants` |
| P7 | `unlock.py`: `CONFIRM_PHRASE` and the six crown unlock pins | `test_unlock_constants` |
| P8 | `assets.py`: `ASSETS` field by field, `assets_for` for 9 inputs (it was and stays case-sensitive), `amonet_bundle_dir` | `test_assets` |
| P9 | the `--board` choices argparse gets in `jarvis-show.py` and `fetch-show-assets.py` | `test_board_choices` |
| P10 | `catalogue/products/genbu-show-v1.json` is `profiles/genbu-show-v1.json` with the key `product` renamed to `id`, nothing else | one-off comparison against `git show HEAD:profiles/genbu-show-v1.json`, section 5 |
| P11 | Crown's zip pins every unlock file and the others don't, which is why `unlock.py` still refuses them; all three say `amonet-kaeru`; `twrp_device` is the board name | `CatalogueFactsTest` |
| P12 | The checker refuses: unknown or missing key, `unlock: none`, a placeholder identity key, an identity value two boards share, wrong asset kind, sha256 without size, a malformed sha256, member pins on an unpinned zip, id not matching the file name, empty or non-text notes, and on the product: unknown key, missing or empty block, unknown board, a board listed twice, a board no product lists, version 0, and `product()` with two products | `CatalogueValidatorTest`, `CatalogueRefusesTest` |

The baseline is what the old code said, not what the hardware is. Section 2 is about the hardware.

Since GENBU-190 (2026-10-19) the generator no longer needs the deleted modules. It reads today's code, so
the baseline can follow a deliberate change: run it, review the diff, commit it with the reason.
`test_generator_writes_the_fixture` fails if the JSON differs from what the generator writes, so it can't
be edited by hand. The first regeneration changed only the `_note` line, which shows the new generator
gives what the old code gave. The proof against 7d601d0 is the fixture as committed in 55cdf4a.

P11 is out of date: since GENBU-178 `twrp_device` is read from the catalogue wherever TWRP is checked, and
`TwrpDeviceComesFromTheCatalogueTest` replaced the test that it equals the board name.

Still not read from the catalogue, so not covered: echod's board list and detection, dashcast's geometry,
`install-show.py` `BOARDS` and `LOGO_KAERU`, `boot_logo.py` `KAERU`, `amonet_upgrade.py` `TARGETS`,
`techo5lib.py` board lists, `live-acceptance.py`, `release-jarvis-show.sh`, `mkrootfs.sh`'s marker.

## 2. Board facts against real hardware

`boards.py`, `profiles/*.json` and the old pin comments are gone after stage b. Citations of them, and
line numbers in `install.py`, `recovery.py` and `unlock.py`, point at commit 7d601d0. The values
themselves are unchanged (section 1); the review comments now sit in the `notes` field of the asset or
board they were about. A comment moved into `notes` is still only a claim.

### crown (Echo Show 8 1st gen)

| # | Fact | State | Where |
|---|---|---|---|
| C1 | fastboot reports product `CROWN` | claimed | `tools/jarvis_crown/boards.py:29`, `docs/jarvis-show-platform.md:7` |
| C2 | TWRP reports `ro.product.device=crown` | claimed | `docs/jarvis-crown-installer.md:177`; `docs/JOBS-CHECKPOINT.md:179` mentions fixing a real crown TWRP handoff, no log |
| C3 | model "Echo Show 8 1st gen / C7H6N3" | claimed | `boards.py:30`. The running unit says only `crown` and `MT8163` (C5), nothing about the retail name |
| C4 | screen 1280x800 | proven (framebuffer) | `docs/dumps/board-identity-running-2026-10-19.txt:20` (800 wide, 3 buffers of 1280, so 800x1280 portrait drawn as 1280x800). That is what the OS draws, not the panel check by eye (J38, `docs/anti-brick-audit.md:105`), which is still open |
| C5 | device-tree compatible `amzn,crown-pvt` | proven | `docs/dumps/board-identity-running-2026-10-19.txt:14`. The kernel command line also has `androidboot.product=crown` (`:18`) |
| C6 | panel `lcm=1-jd936x_wxga_dsi_vdo_crown_st_kd_hsd` (not in the catalogue) | proven | `docs/dumps/board-identity-running-2026-10-19.txt:19` |
| C7 | Lineage zip pin is the sha256 and size of the local copy | proven | `check-files`, section 5 |
| C8 | boot v1.0.1 pin is the sha256 and size of the local copy | proven | `check-files` against the main checkout's `build/release-techo5-v1.0.1/`, section 5 |
| C9 | boot v1.0.1 boots on a crown | claimed | `install.py` comment. The one crown read (H6, 2026-10-20) boots v0.8.0, not this pin (C15) |
| C10 | Amonet zip pin is the local copy | proven | `check-files` |
| C11 | the 7 member pins (`fastbrick.sh`, `profile.sh`, `device.prop`, `bin/fastbrick.img`, `bin/fastboot`, `bin/fastboot32`, `bin/twrp.img`) are the bytes inside that zip | proven | `check-files` |
| C12 | the zip contains `amonet/bin/crown-kaeru.bin` | proven | `check-files` |
| C13 | `amonet-kaeru` unlock with this zip works on a stock crown | claimed | `unlock.py:21` "known-good archive"; no log in the repo |
| C14 | the pinned TWRP boots on a crown | claimed | as C2 |
| C15 | a crown in use boots upstream boot v0.8.0 (sha256 `cf5a492f…`, not in the catalogue), and its running kernel is the one in that partition | proven | `docs/dumps/boot-images-running-2026-10-20.txt:17`, `:22-23`. It was installed before the v1.0.1 pin; a reinstall would flash v1.0.1 |

### checkers (Echo Show 5 1st gen)

| # | Fact | State | Where |
|---|---|---|---|
| K1 | fastboot reports product `CHECKERS` | claimed | `boards.py:41`, `docs/jarvis-show-platform.md:8` |
| K2 | TWRP reports `ro.product.device=checkers` | claimed | `recovery.py:122` expects it (and lowercases what TWRP says first) |
| K3 | model "Echo Show 5 1st gen / H23K37" | claimed | `boards.py:42`. The running unit says only `checkers` and `MT8163` |
| K4 | screen 960x480 | proven (framebuffer) | `docs/dumps/board-identity-running-2026-10-19.txt:29` (480 wide, 3 buffers of 960). J38 still open |
| K5 | device-tree compatible `mediatek,mt8163` | proven | `docs/dumps/board-identity-running-2026-10-19.txt:23`. **Same as cronos (R5)**, so it can't tell the two apart. `androidboot.product=checkers` does (`:27`) |
| K6 | panel `lcm=1-st7701s_wsvga_dsi_vdo_checkers_st_kd_hsd` (not in the catalogue) | proven | `docs/dumps/board-identity-running-2026-10-19.txt:28` |
| K7 | Lineage zip pin is the local copy | proven | `check-files` |
| K8 | boot v1.0.1 pin is the local copy | proven | `check-files` |
| K9 | boot v1.0.1 boots on a checkers | claimed | `install.py` comment. Not read on 2026-10-20: the checkers didn't answer ssh |
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
| R3 | under LineageOS 18.1, `ro.product.device` is `cronos` | proven | `docs/dumps/cronos-lineage-18.1-bench.txt:3` (printed by `getprop ro.product.device`, `tools/hwdump.sh:10`) |
| R4 | screen 960x480 at 59.64 fps | proven | `docs/dumps/cronos-lineage-18.1-bench.txt:828`, `:830` |
| R5 | device-tree compatible `mediatek,mt8163` | proven | `docs/dumps/board-identity-running-2026-10-19.txt:32`. Same as checkers (K5) |
| R6 | panel `lcm=1-st7701s_wsvga_dsi_vdo_cronos_st_truly` (not in the catalogue) | proven | `docs/dumps/cronos-lineage-18.1-bench.txt:90` |
| R7 | kernel command line has `androidboot.product=cronos` | proven | same line |
| R8 | model "Echo Show 5 2nd gen" | claimed | `boards.py:57`. The running unit says only `cronos` and `MT8163` |
| R9 | Lineage zip pin | claimed | no local copy to hash |
| R10 | boot v0.7.16 pin, and that it boots on a cronos | proven | `docs/dumps/boot-images-running-2026-10-20.txt:28`, `:32`: the first 13553664 bytes of a running cronos's boot partition hash to the pin, and the running kernel is the one in it. Still no local copy |
| R11 | Amonet zip pin | unknown | not reviewed; the catalogue leaves the pin out |
| R12 | the zip contains `amonet/bin/cronos-kaeru.bin` | claimed | `boards.py`; oddly `install-show.py:205` already pins a stock cronos kaeru hash |
| R13 | TWRP pin | unknown | none anywhere |

### All assets

| # | Fact | State |
|---|---|---|
| A1 | the Lineage and boot pins equal what upstream publishes | proven 2026-10-19 (GENBU-187): `docs/dumps/upstream-pins-2026-10-19.txt`. Lineage: GitHub asset digest and the release-notes sha256sum (amazon-oss signs nothing). Boot: asset digest, `SHA256SUMS`, and `manifest.json`, whose signature verifies against upstream's own release key |
| A2 | licence of each asset | unknown (all 9) |

## 3. Counts

Board and asset facts (section 2): **25 proven, 16 claimed, 4 unknown** (GENBU-180/181 added C4, C5, C6,
K4, K5, K6, R5 and GENBU-187 added A1 on 2026-10-19; GENBU-182 added C15 and proved R10 on 2026-10-20).

- proven: C4, C5, C6, C7, C8, C10, C11, C12, C15, K4, K5, K6, K7, K8, K10, K11, K12, K13, R3, R4, R5, R6, R7, R10, A1
- claimed: C1, C2, C3, C9, C13, C14, K1, K2, K3, K9, K15, R1, R2, R8, R9, R12
- unknown: K14, R11, R13, A2

Installer-unchanged facts (section 1): 12 proven.

The short version: every identity key the installer uses today (C1, C2, K1, K2, R1, R2) is only claimed.
Every board's device-tree compatible is captured now, and checkers and cronos share one
(`mediatek,mt8163`), so it can't be the key stage c matches on. `androidboot.product` on the kernel
command line differs on all three. All three screen sizes have a capture; the panel check by eye (J38)
is still open. Of the boot pins only cronos's is seen booting; the crown in use runs v0.8.0, which the
catalogue doesn't pin.

## 4. Hardware tests that would promote the claims

All for Jade to run, guided, one board at a time. Each output goes into `docs/dumps/` with serials, MACs
and `androidboot.serialno` stripped first, and the ledger row cites the file and line. Only H7 writes to
the unit.

| Test | What to run | Promotes | Writes? |
|---|---|---|---|
| H1 | in fastboot: `fastboot getvar product` | C1, K1, R1 | no |
| H2 | in TWRP: `adb shell getprop ro.product.device` | C2, C14, K2, R2 | no (but getting into TWRP on crown/checkers goes through the existing recovery step) |
| H3 | on a running Show: `cat /sys/class/graphics/fb0/virtual_size` (techo5 has no `dumpsys`) | C4, K4 | no, **done 2026-10-19** |
| H4 | on a running Show: `tr '\0' '\n' < /proc/device-tree/compatible` | C5, K5, R5 | no, **done 2026-10-19**; step c needs another key, see the short version above |
| H5 | on a running Show: `tr ' ' '\n' < /proc/cmdline \| grep '^lcm='` | C6, K6 | no, **done 2026-10-19** |
| H6 | on a running Show: `/proc/version` (`uname -r` is the same on every image) plus the sha256 of the first *size* bytes of the boot partition, compared with the pin | C9, K9, R10 | no, **done 2026-10-20** on cronos (R10 proven) and crown (runs v0.8.0, C15); checkers open. C9 needs a crown installed from the v1.0.1 pin |
| H7 | the first `amonet-kaeru` unlock of a stock unit, step by step, log saved | C13, K14 | **yes**, Jade's call per board |
| H8 | a full `tools/hwdump.sh` probe on crown and checkers, after hwdump learns to strip identifiers | fixtures for both | no |

For R9, R11, R13 (cronos zip, Amonet and TWRP pins) there's no hardware test: someone has to download,
review and pin the files first, then `check-files` proves the pin.

## 5. Test output

Run in this worktree on 2026-10-13. Python 3.13.15, `python3 -m unittest` (pytest isn't installed here).
No Go code changed, so `go test` wasn't run.

Stage b, `tests/test_board_catalogue.py` against the 7d601d0 baseline:

```
$ python3 -m unittest tests.test_board_catalogue -v 2>&1 | sed 's/ (tests.test_board_catalogue.[A-Za-z]*.test_[a-z_]*)//'
test_assets ... ok
test_board_choices ... ok
test_device_gate ... ok
test_install_constants ... ok
test_lookups_including_case_folding ... ok
test_product_to_board ... ok
test_profile_fields_are_the_old_ones_minus_the_phrases ... ok
test_profiles ... ok
test_recovery_constants ... ok
test_unlock_constants ... ok
test_check_command ... ok
test_one_product_lists_every_board ... ok
test_twrp_device_is_the_board_name_recovery_checks ... ok
test_unlock_pins_only_complete_on_crown ... ok
test_unlock_policy ... ok
test_explain_names_the_owner ... ok
test_no_guessing_identity_keys ... ok
test_unknown_or_inexact_board ... ok
test_unknown_or_inexact_product ... ok
test_bad_sha ... ok
test_board_no_product_lists ... ok
test_id_must_match_file ... ok
test_identity_placeholder_key ... ok
test_member_pins_need_zip_pin ... ok
test_missing_key ... ok
test_notes_must_be_text ... ok
test_product_bad_version ... ok
test_product_board_twice ... ok
test_product_empty_block ... ok
test_product_missing_block ... ok
test_product_unknown_board ... ok
test_product_unknown_key ... ok
test_sha_without_size ... ok
test_shared_identity ... ok
test_two_products_refuse_to_pick ... ok
test_unknown_key ... ok
test_unlock_outside_closed_set ... ok
test_wrong_asset_kind ... ok
----------------------------------------------------------------------
Ran 38 tests in 0.089s
OK
```

To check that the parity test can fail, I changed checkers' height to 481 in `catalogue/boards/checkers.json`,
and separately removed the case folding from `profile_for_board`, one at a time, and put them back:

```
height 481:    FAIL test_profiles (checkers), FAIL test_lookups_including_case_folding (checkers,
               ' CHECKERS ', product CHECKERS, product ' checkers '), FAIL test_explain_names_the_owner
no folding:    FAIL test_lookups_including_case_folding ('Crown', ' CHECKERS ', 'CRONOS')
```

P10, the product file against the deleted profile:

```
$ python3 -c "... old = git show HEAD:profiles/genbu-show-v1.json; old['id'] = old.pop('product'); print(old == new)"
same apart from product->id: True
```

Full suite after stage b:

```
$ python3 -m unittest discover -s tests -p 'test_*.py'
ERROR: test_actual_archived_techo5_rootfs_is_rejected_as_not_jarvis (test_jarvis_crown_install...)
ERROR: test_pinned_boot_is_exact_known_good_image (test_jarvis_crown_install...)
ERROR: test_plan_forces_safe_prestaged_path_and_keeps_expdb_logo_untouched (test_jarvis_crown_install...)
ERROR: test_plan_passes_show_settings_as_switches_and_files (test_jarvis_crown_install...)
FAIL: test_backup_must_match_current_recovery_serial (test_jarvis_crown_install...)
Ran 730 tests in 1.579s
FAILED (failures=1, errors=4, skipped=24)
```

The same five fail on a clean checkout of 55cdf4a (717 tests, the same 1 failure and 4 errors). They need
`build/release-techo5-v1.0.1/` and `build/release-techo5-v0.9.21/`, which are gitignored and only exist
in the main checkout, not in a fresh worktree. `python3 -m compileall -q tools tests` and
`python3 tools/secret_scan.py --here` were clean.

Slice a, kept because the pins haven't changed since (P5 to P8 prove that):

Catalogue check and local hashes (`~/.cache/jarvis-show/{crown,checkers}` and the main checkout's
`build/release-techo5-v1.0.1/`):

```
$ python3 tools/jarvis_crown/catalogue.py check   # after stage b
ok: 1 product, 3 boards, 9 assets

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
