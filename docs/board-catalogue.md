# Board catalogue

GENBU-163. Design for a small board catalogue in the shape oddc uses. Slices a (the catalogue) and b
(the installer reads it) are done; the rest is below under "Migration, in order".
What is proven, what is only claimed and what nobody knows yet is in
[board-catalogue-evidence.md](board-catalogue-evidence.md). Read that before trusting any value here.

## Why

Before GENBU-163 the same board facts lived in several places, and nothing kept them equal. Stage b
removed the installer's copies; the rest are under "Next owners to fold in".

| Fact | Owners before GENBU-163 |
|---|---|
| Which boards exist | `tools/jarvis_crown/boards.py` `PROFILES`, `install.py` `SUPPORTED_BOARDS`, `tools/install-show.py:74` `BOARDS`, `echod/internal/update/manifest.go:88` `supportedBoards`, `echod/internal/layout/device_cronos.go`, `profiles/genbu-show-v1.json` `boards` |
| Screen size | `boards.py`, `profiles/<board>-v1.json`, `dashcast/serve.go:86-89` (crown and checkers only; cronos falls through to the default) |
| Boot image sha256 | `install.py:22-24`, `profiles/checkers-v1.json` |
| TWRP sha256 | `recovery.py:20-28`, `profiles/checkers-v1.json` |
| Kaeru sha256 and logo slot | `tools/jarvis_crown/boot_logo.py:58-62`, `tools/install-show.py:204` (the two crown "with logo" hashes differ, and only `install-show.py` has a cronos row) |
| Product settings (music block) | `profiles/genbu-show-v1.json`, copied into `profiles/crown-v1.json` |

The catalogue gives each fact one owner. Code reads the resolved values and never asks which board it's on.

## Rules

- One owner per fact. A board names shared things (assets) by id and never copies them.
- No inheritance and no defaults. A missing value stays missing, and whoever needs it refuses.
- Exact match or refuse. No case folding, no "closest board", no generic fallback board. The installer's
  lookups too (Q8, GENBU-170): `Crown` is refused, and the message names the right spelling.
- Policy fields take values from closed sets. An unknown value is an error, not a warning.
- Behaviour comes from policy or quirk data, never from a hardware fact ("has a camera, so turn it on").
- The resolver is Python and runs on the PC. The Show only reads the resolved file.
- `tools/jarvis_crown/catalogue.py` owns the entity shape. If a JSON Schema file is ever added, a test
  must keep it equal to the Python checks.

## Layout

```
catalogue/
  products/<product>.json one per product, file name = id
  boards/<board>.json     one per board, file name = id
  assets/<asset>.json     one per file you download and pin, file name = id
```

Every board and asset may carry `notes`, free text for people: review history, why a pin is or isn't
there. Code never reads it. The review comments that used to sit next to the pins in `assets.py`,
`install.py`, `recovery.py`, `unlock.py` and `boards.py` live there now.

### Product entity

```json
{
  "id": "genbu-show-v1",
  "version": 1,
  "boards": ["crown", "checkers", "cronos"],
  "first_boards": ["crown", "checkers"],
  "assistant": { "...": "..." },
  "display": { "...": "..." },
  "security": { "...": "..." },
  "music": { "...": "..." }
}
```

The product lists the boards it ships on, and boards never point back at it. A board no product lists is
an error. `boards` order is the order the installer offers them in. `first_boards` are the boards the
first release named. Shows from then refuse a manifest or rootfs marker naming any other board, so every
later board reads a signed `manifest-<board>.json` of its own and sits in the marker's `more_boards`
(`catalogue.first_boards()`, `later_boards()`, GENBU-176). Moving a board into `first_boards` would hand
older Shows a manifest they refuse, so it never happens for a board that shipped after them. Since GENBU-191 a
release signs only `manifest-v2.json`, naming every board; the per-board names come from the bridge release. The four blocks are the product
settings that `profiles/genbu-show-v1.json` held; the checker only makes sure each is a non-empty object.
The id is also what the rootfs marker calls "product" (`install.PROFILE_ID`). `release-jarvis-show.sh`
reads it from the catalogue, and `install-show.py` uses `techo5lib.JARVIS_SHOW_PRODUCT`. Three places
can't read the catalogue and spell `genbu-show-v1` themselves: `mkrootfs.sh` (it runs on the device too),
echod's `manifest.go` (compiled in) and `techo5lib.py` (copied to the other TECHO5 repos). So it still can't
be renamed in the catalogue alone, but `ProductIdSpelledOnceTest` fails if any of the three disagrees with
the catalogue or the id turns up anywhere else (GENBU-174).

Exactly one product exists today. When there are more, the install picks one per unit (GENBU-192):
`--product` if it was passed, which has to list the unit's board; otherwise the one product that lists
the board. When two or more do, the installer asks which, and with nobody to ask (`--no-questions`, no
terminal) it stops before downloading anything. `Catalogue.pick_product()` does this, and the id it
returns is what the release's manifest and the rootfs marker are held to. Nothing on the Show changes:
the marker already says which product it is. `Catalogue.product()` with no id still refuses when there
are several, so nothing picks the first one silently. `install.PROFILE_ID` is only set while there's
one product. `release_channels.py` names the product its releases belong to (`JARVIS_SHOW_PRODUCT`),
since another product would publish its own releases. `install-show.py` stays single-product.

### Board entity

```json
{
  "id": "crown",
  "model": "Echo Show 8 1st gen / C7H6N3",
  "profile_id": "genbu-crown-v1",
  "identity": { "fastboot_product": "CROWN", "twrp_device": "crown" },
  "display": { "width": 1280, "height": 800 },
  "policy": { "install": { "unlock": "amonet-kaeru" } },
  "assets": {
    "lineage": "lineage-18.1-20260904-UNOFFICIAL-crown",
    "boot": "techo5-boot-crown-v1.0.1",
    "amonet": "amonet-crown-v2.0.1"
  }
}
```

Every key shown is required. Two are optional: `notes`, and `amonet_upgrade` (below). `identity` takes only keys from a closed set, one per stage the unit is seen in
(fastboot, TWRP, running), and a key that nobody captured from a real unit is left out. It's never filled with "unknown".

| Identity key | Stage | Read today by | In the catalogue |
|---|---|---|---|
| `fastboot_product` | fastboot, before unlock | `device_gate.identify_show` | yes, all three |
| `twrp_device` | TWRP (`ro.product.device`) | `recovery.verify_twrp_board`, `amonet_upgrade.py`, `install-show.py` | yes, all three, each the board's own name |
| `android_product` | running Show (`androidboot.product` on the kernel command line) | echod: `board.Migrate` writes board.json by it, and echod goes by it where board.json is missing or no use (GENBU-193) | yes, all three, each the board's own name; proven on a running unit of each (evidence ledger C5, K5, R7), and `tests/test_board_json.py` reads it out of that capture |
| `dt_compatible` | running Show | nobody | **no**: captured 2026-10-19, but checkers and cronos both say `mediatek,mt8163`, and the checker refuses a shared value. `android_product` took its place |
| `lcm_panel` | running Show (kernel `lcm=`) | nobody since GENBU-193 (echod matched it by substring before) | **no**: the panel is a component and two boards may share one. It becomes a board key only once a test shows each captured value maps to exactly one board |

The checker refuses an identity value that two boards share.

`amonet_upgrade` is the partition map `jarvis-show.py amonet-upgrade` needs to move a unit from Amonet
1.x to 2.x: what the Amonet 2.x update-binary writes, one entry per write.

```json
"amonet_upgrade": {
  "writes": [
    { "names": ["swdl_real", "swdl"], "partition": "mmcblk0p11", "binary": "twrp.img" },
    { "names": ["MISC", "misc"], "partition": "mmcblk0p8", "binary": null }
  ]
}
```

`names` are the by-name entries the update-binary looks up, first found wins; `partition` is where they
must point; `binary` is the file under the zip's `amonet/bin/` it writes there, or `null` for the misc
wipe. The checker wants at least one write, no partition twice, and `mmcblk0p<n>` partitions. Only
checkers has one: its partition numbers were read off a running unit
(`docs/dumps/checkers-partitions-2026-10-20.txt`, GENBU-184). A board without it is refused by
`amonet-upgrade` and its 1.x hint doesn't send anyone there. Like `identity`, it's added only from a
real unit, never guessed.

### Asset entity

```json
{
  "id": "amonet-crown-v2.0.1",
  "kind": "amonet",
  "file": "amonet-crown-v2.0.1.zip",
  "url": "https://...",
  "sha256": "5db974c3...",
  "size": 44233517,
  "source": "XDA thread 4762900 (R0rt1z2), needs an XDA login",
  "manual": true,
  "upstream": { "repo": "R0rt1z2/amonet", "branches": ["mt8163-echo-show", "mt8163-crown"] },
  "kaeru": "amonet/bin/crown-kaeru.bin",
  "wordmark": {
    "offset": 288716, "length": 481792, "stock": "6a717f0f...",
    "logos": { "openjade": "e8d1fe05...", "techo5": "926bc844..." },
    "openjade_large": { "file": "openjade-wordmark-crown.bin", "sha256": "a29902b3...", "code_at": 63488,
                        "code_stock": "6ff49d73...", "code": "6ff4ee73...", "kaeru_sha256": "32aa461d..." }
  },
  "members": { "amonet/bin/twrp.img": "b6b14464...", "amonet/fastbrick.sh": "aa93be2a...", "...": "..." }
}
```

- `kind` is one of `lineage`, `boot`, `amonet`.
- `sha256` and `size` together are the pin. An asset without them is known but never used: that's
  how `amonet-cronos-v2.0.1` looks today.
- `members` (amonet zips only) pins files inside the zip by their path in the zip. A member only gets its
  own entity if something uses it outside that zip. Member pins need the zip itself pinned.
- `kaeru` names the kaeru file in the zip. It's a path, not a pin.
- `upstream` (amonet zips, required) is where the zip's open-source parts live. The fetcher compares a
  new zip file by file against every board's branches before anyone pins it. All amonet assets name the
  same repo; the checker refuses two.
- `wordmark` (amonet zips only) is kaeru's boot logo slot: where it is (`offset`), how much of kaeru is
  checked (`length`), and kaeru's sha256 as the zip ships it (`stock`) and with each logo written in
  (`logos`, `openjade` or `techo5`). `openjade_large` is crown's bigger OpenJade picture: its slot file
  in `tools/boot-logo/`, the 32 code bytes at `code_at` before and after, and kaeru's sha256 with both
  ([boot logos](../tools/boot-logo/README.md)). `boot_logo.py` writes the OpenJade logo,
  `install-show.py` the TECHO5 one; before GENBU-175 each had its own table. A kaeru carrying one tool's
  logo is one the other tool doesn't know, so it refuses to write over it.
- `licence` (GENBU-186) says which terms each part of the file comes under: `checked` is the date it was
  read, and each entry in `parts` names a part, its SPDX expression and the `sources` it was read from.
  A source is an https address, pinned to a commit or tag where there is one, or `asset:<where inside the
  file>`. Two values stand for findings, not gaps: `LicenseRef-not-stated` means the part's source states
  no terms at all (Amazon's and MediaTek's boot chain and vendor blobs, the prebuilt TWRP and fastboot
  binaries, amonet's scripts), and `LicenseRef-per-file-notices` means the file carries its own notice
  list (LineageOS's `/system/etc/NOTICE.html.gz`). Nothing in it was guessed: a part whose terms nobody
  states is recorded as not stated, never filled in. A pinned asset without a `licence` is refused, and
  since every tool loads the catalogue first, so is every step. An unpinned asset may leave it out,
  because nothing uses it.
- Not there yet: `version`. oddc's asset entities have it.

### Resolver and explain

```
python3 tools/jarvis_crown/catalogue.py check
python3 tools/jarvis_crown/catalogue.py resolve crown
python3 tools/jarvis_crown/catalogue.py explain crown assets.boot.sha256
  "e00bed96..."  <- catalogue/assets/techo5-boot-crown-v1.0.1.json#/sha256
python3 tools/jarvis_crown/catalogue.py check-files ~/.cache/jarvis-show/crown ...
```

`resolve` fills each asset ref in from its owner and returns one object. `explain` tells you which file
holds a value. `check-files` hashes local copies against the pins and only reports.

## The install, as one step sequence

The steps are code, the same for every board. `tools/jarvis_crown/flow.py` already runs them in this
order; the catalogue only feeds them parameters.

| Step | Writes to the unit | Reads from the resolved board |
|---|---|---|
| preflight | no | `assets.*` (pins, file names, `kaeru`) |
| identify | no | `identity.fastboot_product` |
| unlock | **yes** | `policy.install.unlock`, `assets.amonet` members |
| recovery (boot TWRP) | **yes** (RAM boot) | `identity.twrp_device`, `assets.amonet.members["amonet/bin/twrp.img"]` |
| backup | no | `identity.twrp_device` |
| validate-install-inputs | no | `assets.boot`, `assets.lineage`, `display` |
| stage-lineage, install | **yes** | `assets.boot`, `assets.lineage` |

The confirmation words belong to the step code: `unlock.CONFIRM_PHRASE` (`UNLOCK`) and
`install.FINAL_CONFIRM_PHRASE` (`ERASE`). They were the same on every board, and the parity test checks
the old profiles against these constants.

A destructive step may run on a unit only when all three hold:

1. the unit matched exactly one board on the identity key for that stage;
2. every asset and member the step uses is pinned and the local bytes hash to the pin;
3. there is passing evidence for that step on that board, bound to the current catalogue data
   (see Evidence). This part is new and not built yet: adding it would change what the installer does
   today, and stage b had to change nothing. Until it exists, `unlock.py` keeps its crown-only check in
   code, so pinning more members in the catalogue does not by itself open unlock on another board.
   Since GENBU-212 every unlock run writes the log this part needs (see Evidence).

So whether a board may be unlocked is computed, not written down. Policy says *how* (all three boards say
`amonet-kaeru`), the pins and evidence say *whether*. Checkers fails (2) today, because only its TWRP is
pinned in its zip. Cronos passes (2) since GENBU-212, which pinned the six unlock files of its zip (reviewed
on 2026-10-14) on 2026-10-20, but has no evidence yet, so it fails (3). Both match what `unlock.py`
refuses today without a hash manifest.

Units that already carry Amonet 1.x (`amonet-upgrade`) aren't a quirk and don't get a policy field. The
unlock step reads the installed Amonet version, matches it exactly against a pinned list of upgradable
versions (asset data) and refuses anything else. A closed-set policy field comes in only if boards
really differ in which upgrade paths they allow.

## Policy and hardware

Policy fields are closed sets, checked by the catalogue checker. Today there's one:

| Field | Values | Read by |
|---|---|---|
| `policy.install.unlock` | `amonet-kaeru` | unlock step |

`none` isn't in the set. No board needs it today, and writing `none` for a board whose unlock just isn't
pinned yet would put a temporary state into data. It gets added when a real board unlocks some other way.

Hardware facts and policy stay apart. When the camera comes in it'll be `hardware.camera` (a fact) and
`policy.camera.enabled` (a choice), and the checker refuses `enabled` on a board without the hardware.
Steps read policy only.

## Quirks

None in this slice. When one is needed: a quirk is its own entity
(`catalogue/quirks/<vendor>/<problem>.json`, kebab-case, no dates or ticket numbers) with id, affected
range, reason, source, removal condition and data. A board turns it on by ref. Each kind of quirk has one
generic implementation that acts on the data fields and never on a quirk or board id. Quirks are for
hardware or software misbehaviour, not for the state a unit happens to be in.

## Which module reads what

Every change that reads a new field adds its row here in the same change.

| Module | Reads |
|---|---|
| `catalogue.py` `profile_for_board`, `profile_for_product`, `supported_boards` | product `boards`; board `id`, `model`, `profile_id`, `identity.fastboot_product`, `display`; `assets.amonet` `file` and `kaeru` (for the folder and kaeru names) |
| `device_gate.py` | the profiles above: `SUPPORTED_PRODUCTS`, `SUPPORTED_SUMMARY`, the unsupported-product message |
| `install.py` | product `id` (`PROFILE_ID`), product `boards`, `assets.boot.sha256` |
| `recovery.py` | `assets.amonet.members["amonet/bin/twrp.img"]`; `identity.twrp_device` (`twrp_device()`, also used by `amonet_upgrade.py`), so a TWRP that calls itself something other than the board needs no code change; a board without one is refused before adb runs; which boards have `amonet_upgrade` (`amonet1_upgrade_hint`) |
| `amonet_upgrade.py` | board `amonet_upgrade.writes`, on every call (`catalogue.amonet_upgrade_writes`): the zip binaries it wants, the names it resolves in TWRP, the sizes it checks, what it compares after the flash |
| `unlock.py` | crown's `assets.amonet.members` for the six unlock files; refuses to import if crown loses one |
| `assets.py` | every board's `assets.*`: `file`, `url`, `source`, `manual`, `sha256`, `size`; `assets.amonet.upstream` (`AMONET_SOURCE`, for the fetcher's file-by-file compare) |
| `boot_logo.py` (`KAERU`) | `assets.amonet.wordmark`: `offset`, `length`, `stock`, `logos.openjade`, `openjade_large` |
| `install-show.py` (`LOGO_KAERU`) | `assets.amonet.wordmark`: `offset`, `length`, `stock`, `logos.techo5` |
| `install-show.py` (`BOARDS`, `--jarvis-show-board`) | product `boards`; board `model` up to " / " |
| `install-show.py` (`TWRP_BOARDS`) | `identity.twrp_device`, to turn what TWRP reports back into the board |
| `release-jarvis-show.sh`, `release_channels.py` | product `id`, `first_boards` and the boards after them |
| `preflight.py`, `amonet_upgrade.py`, `flow.py`, `jarvis-show.py`, `fetch-show-assets.py` | profiles and `supported_boards()` through `catalogue.py` |
| `validate-jarvis-show.sh` | runs `catalogue.py check` |
| `catalogue.py` `on_show` | board `id`, `model`, `profile_id`, `identity`, `display`: board.json |
| `install-show.py` (`board_json_cmd`) | `on_show` of the board the unit reported, written to the Show |
| `echod/internal/board` (Go) | its own copy of every board's `on_show`, generated by `catalogue.py write-on-show` and held equal by `tests/test_board_json.py`; echod's `layout` and `home` tests hold its hardware names and panel sizes to it |
| `tests/test_board_catalogue.py` | everything (parity against `tests/fixtures/board-catalogue-baseline.json`) |
| `tools/board_names.py`, `tests/test_board_names.py` | nothing from the catalogue: they count where board names are spelled out *outside* it, against `tests/fixtures/board-names-baseline.json` |

The parity baseline is written by `tests/fixtures/make_board_catalogue_baseline.py` from today's code
(GENBU-190). A change to the catalogue that changes an answer makes the parity tests fail. If the change is
meant, run the generator, read the diff of `board-catalogue-baseline.json` and commit it with the reason.
Don't edit the JSON by hand: a test checks that it is exactly what the generator writes.

The catalogue loads once per process, on first use. A broken catalogue stops the installer before any
device is touched. `jarvis-show.py`, `jarvis-crown.py`, `fetch-show-assets.py`, `install-show.py` and `release_channels.py` load it first thing
(`catalogue.load_or_exit()`), so every command, read-only ones too, stops with one line naming the file
and what is wrong, not a traceback (GENBU-173). All or nothing on purpose: one rule, no half-loaded
board list.

## board.json on the Show

`/data/misc/techo5/board.json`, next to `profile`, on Android and on the Linux image alike. The installer
writes it on every install, from the board the unit reported (`install-show.py`, `board_json_cmd`), and a
factory reset keeps it, as it keeps the profile: it says what the unit is, not how it was set up.

It holds `catalogue.py on-show <board>`: `id`, `model`, `profile_id`, `identity` and `display`, on one
line of about 220 bytes. No assets and no install policy: the Show reads neither, and the line goes over
the serial console as one command. Download URLs and pins stay on the PC.

Go reads it through one struct with `DisallowUnknownFields` (`echod/internal/board`), which refuses a
missing key, a second value or a half-written file. echod carries every board's board.json in
`internal/board/boards/`, made by `catalogue.py write-on-show`; `tests/test_board_json.py` fails if they
differ from the catalogue, and echod's own tests fail if its hardware names or panel sizes differ from
them. So the three can't drift.

Stage c goes in two steps:

1. **Read and compare (done).** At start echod read board.json and logged `match`, `differs`, `none` or
   `unreadable` against its own guess and hardware name. Nothing went by it.
2. **Go by it (done, GENBU-193, decided 2026-10-27).** The guess and echod's hardware names and panel
   sizes are gone. `layout.Board` is board.json's board, when the file reads and names a board this
   build carries. Where it doesn't, it's the one board whose `android_product` the kernel command line
   names, the same rule `board.Migrate` writes by, so the next start reads the same from the file.
   Nothing else is gone by: not the panel name, not the mute driver. Names and the panel size come from
   the board's own file in `internal/board/boards/`. Updates go by board.json alone
   (`layout.OTABoard`): a unit that knows its board only from the command line refuses every manifest
   until it has the file. A unit that gives neither has no board (`""`). It's called "Echo Show
   (unknown board)", runs on the values the board checks fall back to (the 2nd gen Show 5's, as
   before for a unit with no panel name and no `amazon-gating`), logs `board state=unknown` and
   doesn't update. At start echod logs which board it goes by and where from (`board.Note`):
   `match`, `differs` (board.json and the command line name two boards; the file wins), `none` or
   `unreadable` (the command line, because the file is missing or no use) or `unknown`.

### Units already in the field

They have no board.json. Shipping an echod that refuses without one would take them down, so:

1. The OTA step checks for board.json before it installs an echod that needs it. No file, no update.
   The echod side is done (GENBU-193): an echod without board.json refuses every manifest. Keeping an
   older echod, which ignores manifest fields it doesn't know, off such a release is decided but **not
   built** (GENBU-191): releases that go by board.json are published under a new manifest name, and the
   old name stays on the last release that writes board.json by itself, so an older Show takes that
   first. Until that bridge exists, an echod that goes by board.json must not be published.
2. A one-time migration writes board.json only when the unit matches exactly one board through a key that
   is already proven exact on real units. **Done** (`board.Migrate`, GENBU-191): at start, an echod that
   finds no board.json reads `androidboot.product` off the kernel command line and, when exactly one board
   it carries has that `android_product`, writes that board's file, byte for byte what the installer
   writes, the same way (`.new`, then renamed). A file already there is never touched, even a broken one.
3. Any unit that would need a guess to identify stays on its current echod and goes on a list for a
   person to identify. Today that list is echod's log: `board.json migration state=unidentified` with the
   product it saw (none, an unknown one, or two different ones). Such a unit keeps running as it did.

Failing closed here means "this unit doesn't update", never "this unit stops working".

### Gone: echod's guess

`showBoard` in `echod/internal/layout/device_cronos.go` used to match the `lcm=` panel name, and when
there was no panel name it guessed: `amazon-gating` present meant checkers, absent meant cronos. A crown
without a panel name would have come out as checkers. Both went with GENBU-193 (stage c, second step).
They must not come back, in echod or in the catalogue.

## A board nobody has tested (Echo Show 8 2nd gen and later)

1. **Probe.** Run a read-only probe (`tools/hwdump.sh`) on the unit. Before its output goes into the repo
   it has to be stripped of identifiers. hwdump.sh does **not** do that today: it prints `/proc/idme`
   serial and MAC addresses (`tools/hwdump.sh:102`) and the whole kernel command line, which carries
   `androidboot.serialno` (`tools/hwdump.sh:31`).
   That has to be fixed first.
2. **Draft as a branch.** The draft is a branch and a PR, never a `drafts/` folder. It gets a board
   entity only after a real, stripped probe is saved. It refs existing assets or components only on an
   exact id match and lists everything it couldn't match in the PR. Nothing is filled in from a similar
   board: the Show 8 2nd gen gets nothing from crown unless it's the same part by exact id.
3. **Status is computed.** There's no status field anyone writes. A fresh board has no evidence, so the
   installer only offers the read-only steps. No special mode.
4. **First destructive run.** That's not an installer feature. A person runs it step by step, guided, and
   the log becomes the first evidence record. Whether and when to risk it is Jade's call.

## Evidence

Per step, per board: `catalogue/evidence/<board>/<step>.json`. Shape, following oddc's
`schemas/evidence.schema.json` plus what a destructive step needs:

```json
{
  "schemaVersion": 1,
  "id": "crown-unlock-2026-10-20",
  "deviceId": "crown",
  "step": "unlock",
  "observedAt": "2026-10-20T19:30:00Z",
  "status": "hardware-validated",
  "release": "v1.0.3",
  "closure": "<sha256 of crown's resolved catalogue data>",
  "assets": { "amonet-crown-v2.0.1": "5db974c3..." },
  "log": { "path": "catalogue/evidence/crown/unlock-2026-10-20.log", "sha256": "..." },
  "results": []
}
```

- Only `runtime-verified` and `hardware-validated` count as passing.
- `closure` binds the record to the board's resolved data. If the data changes afterwards, the record
  reads as "changed" and the step closes again until someone re-runs it.
- The check fails if the log file is missing or its hash doesn't match.
- No name of who ran it; the git author already says that.
- A hand-written record without a log is review material, not proof.

Nothing reads evidence yet. The format is settled; the check is deferred because it would change what
the installer does today (see the destructive-step rule above and open question Q9).

Where the logs come from: since GENBU-212 every unlock that gets as far as Amonet writes one, under
`~/.local/state/jarvis-show/unlock/` (the unlock log in
[jarvis-crown-installer.md](jarvis-crown-installer.md), J19). The serial is already blanked, so after a read
through it can be copied to `catalogue/evidence/<board>/` and the record written next to it. The next
logged unlock on each board makes its first record; then the check gets built and cronos opens through
it, as Jade decided on 2026-10-20.

## Next owners to fold in

Stage b moved the installer (`tools/jarvis_crown/` and the two CLIs that use it). `live-acceptance.py`
J38 now asks for every board the product lists (GENBU-176).

These can't read the catalogue, so they keep their own copy and a test fails when it disagrees
(GENBU-176, GENBU-177): `dashcast/serve.go` geometry and echod's `manifest.go` board lists
(`supportedBoards`, `ownManifestBoards`) in `test_show_board_lists.py`; `techo5lib.py`
`JARVIS_SHOW_BOARDS` and `OWN_MANIFEST_BOARDS` there too; `mkrootfs.sh`'s rootfs marker (`boards` =
`first_boards`, `more_boards` = the rest) in `test_release_contract.py`. `release-jarvis-show.sh` reads
`first_boards`, but making the manifest of a later board (its `--<board>-boot` option and file names) is
still written out per board; the same test fails if a later board has no such section.

## Migration, in order

a. **Catalogue, checker, resolver, explain, parity test.** Done.
b. **Installer reads the catalogue.** Done. `boards.py`, `profiles/` and the pin constants in
   `install.py`, `recovery.py`, `unlock.py` and `assets.py` are gone; the product entity moved in. Parity
   against a baseline frozen before the move proves the installer gives the same answers. Two things
   planned for b were held back because they'd change behaviour: the evidence check (Q9) and exact-match
   lookups (Q8). Exact-match lookups came in with GENBU-170.
c. board.json written at flash time. echod reads it next to its constants with a test that they match,
   then the constants go, the guess with them. Done in code: the key is `android_product`
   (the DT compatibles don't tell checkers from cronos), units in use write their own board.json once
   they run an echod with the migration, and echod goes by board.json (GENBU-193). Shipping it waits on
   the bridge release (GENBU-191).
d. Product mains (`cmd/echod-show`, `-dot`, `-spot`) replace the build tags. Shell scripts get resolved
   values as arguments.
e. CI grep for board names and build tags outside the catalogue and the mains, with a baseline count that
   may only go down. The board-name half is done: `tools/board_names.py` counts crown, checkers and cronos
   per tracked file, path and text, and `tests/test_board_names.py` fails when a count goes up or a new
   file starts naming a board. A count that goes down fails too, until `python3 tools/board_names.py
   --update` lowers `tests/fixtures/board-names-baseline.json`, so a gain can't quietly slip back. Not
   counted: the catalogue, echod's carried boards, docs, audit records, Markdown and tests. The build-tag
   half waits on d: while the tags are how echod is built, a count that may only fall would just get in
   the way.

## Open questions

For Jade. None of them is decided in code; the code keeps what it did before.

- **Q8, case folding.** Decided 2026-10-27 (GENBU-170): exact. `profile_for_board` and
  `profile_for_product` refuse `Crown` and ` CHECKERS `, and the message names the right spelling. What a
  device reports is still brought into the catalogue's spelling first (fastboot's product upper-cased,
  TWRP's device name lower-cased), since that's reading the device, not a name someone typed.
- **Q9, evidence check.** Designed above, not built. Unlock stays crown-only in code until it exists.
  Decided 2026-10-20 (GENBU-212): pin and log first, then build the check once each board has a logged
  run; cronos opens through the check, not before.
- **Asset licences.** Decided 2026-10-27 (GENBU-186): every pinned asset has a licence record, and a pin
  without one is refused. Many parts state no terms at all (see `licence` above); those are recorded as
  not stated. Nothing is redistributed: the zips and images are fetched from where upstream publishes
  them.
- **echod's guess.** Decided 2026-10-27 (GENBU-193): dropped, units without board.json don't update. What
  a unit with no board at all runs on (the 2nd gen Show 5's values) is an assumption made in code, not a
  decision; see stage c above.
- **Picking a product.** Decided 2026-10-27 (GENBU-192): from the unit's board, asked when two products
  list it, and `--product` overrides. See the product entity above.
- **The remaining owners** above, especially `mkrootfs.sh`'s marker and `PROFILE_ID`, which several
  scripts and echod spell out themselves.

## oddc answers

All nine design questions from this ticket were answered by oddc on 2026-10-13 and are folded in above. Nothing here is
waiting on oddc right now.
