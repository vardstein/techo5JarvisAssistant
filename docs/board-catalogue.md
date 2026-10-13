# Board catalogue

GENBU-163. Design for a small board catalogue in the shape oddc uses, and the first slice of it.
What is proven, what is only claimed and what nobody knows yet is in
[board-catalogue-evidence.md](board-catalogue-evidence.md). Read that before trusting any value here.

## Why

Today the same board facts live in several places, and nothing keeps them equal:

| Fact | Owners today |
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
- Exact match or refuse. No case folding, no "closest board", no generic fallback board.
- Policy fields take values from closed sets. An unknown value is an error, not a warning.
- Behaviour comes from policy or quirk data, never from a hardware fact ("has a camera, so turn it on").
- The resolver is Python and runs on the PC. The Show only reads the resolved file.
- `tools/jarvis_crown/catalogue.py` owns the entity shape. If a JSON Schema file is ever added, a test
  must keep it equal to the Python checks.

## Layout

```
catalogue/
  boards/<board>.json     one per board, file name = id
  assets/<asset>.json     one per file you download and pin, file name = id
```

There's no product entity in the catalogue yet. `profiles/genbu-show-v1.json` stays the product for now
and lists the boards it ships on; boards never point back at the product. Moving it in is part of step b.

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

Every key is required. `identity` takes only keys from a closed set, one per stage the installer sees the
unit in, and a key that nobody captured from a real unit is left out. It's never filled with "unknown".

| Identity key | Stage | Read today by | In the catalogue |
|---|---|---|---|
| `fastboot_product` | fastboot, before unlock | `device_gate.identify_show` | yes, all three |
| `twrp_device` | TWRP (`ro.product.device`) | `recovery.verify_twrp_board` | yes, all three |
| `dt_compatible` | running Show (echod) | nobody yet | **no**: never captured, see the evidence ledger. Stage c stays blocked until it is |
| `lcm_panel` | running Show (kernel `lcm=`) | echod, by substring | **no**: the panel is a component and two boards may share one. It becomes a board key only once a test shows each captured value maps to exactly one board |

The checker refuses an identity value that two boards share.

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
  "kaeru": "amonet/bin/crown-kaeru.bin",
  "members": { "amonet/bin/twrp.img": "b6b14464...", "amonet/fastbrick.sh": "aa93be2a...", "...": "..." }
}
```

- `kind` is one of `lineage`, `boot`, `amonet`.
- `sha256` and `size` together are the pin. An asset without them is known but never used: that's
  how `amonet-cronos-v2.0.1` looks today.
- `members` (amonet zips only) pins files inside the zip by their path in the zip. A member only gets its
  own entity if something uses it outside that zip. Member pins need the zip itself pinned.
- `kaeru` names the kaeru file in the zip. It's a path, not a pin; the kaeru hashes still live in
  `boot_logo.py` and `install-show.py` (see "Next owners to fold in").
- Not there yet: `version` and `licence`. oddc's asset entities have both. Nobody has written down the
  licence of the Amonet zips or the LineageOS builds, and making one up would be worse than leaving it
  out. Jade's call whether a destructive step should refuse an asset without a licence.

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

The confirmation words (`UNLOCK`, `ERASE`) belong to the step code. They're the same on every board
today, and the parity test checks that before they leave the board profile.

A destructive step may run on a unit only when all three hold:

1. the unit matched exactly one board on the identity key for that stage;
2. every asset and member the step uses is pinned and the local bytes hash to the pin;
3. there is passing evidence for that step on that board, bound to the current catalogue data
   (see Evidence). This part is new; today's code has no evidence check.

So whether a board may be unlocked is computed, not written down. Policy says *how* (all three boards say
`amonet-kaeru`), the pins and evidence say *whether*. Checkers fails (2) today, because only its TWRP is
pinned in its zip; cronos fails (2) because its zip isn't pinned at all. That matches what `unlock.py`
and `recovery.py` refuse today.

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

Today almost nothing reads the catalogue; that's step b. Every change that reads a new field adds its row
here in the same change.

| Module | Reads |
|---|---|
| `tests/test_board_catalogue.py` | everything (parity) |
| everything else | nothing yet; still `boards.py`, `assets.py`, `install.py`, `recovery.py`, `unlock.py` |

## board.json on the Show

At flash time the installer writes the resolved board (minus download URLs) to the Show as board.json
(where exactly is decided in step c). Go reads it through one struct with `DisallowUnknownFields`; a test resolves
every board in Python and loads each result through that struct, so the two can't drift.

At start echod reads the device-tree `compatible` and compares it with `identity.dt_compatible` in
board.json. Mismatch or no file: echod refuses to start its board-specific parts. One echod binary for all
Show boards.

This can't start yet: no DT compatible has been captured for any board.

### Units already in the field

They have no board.json. Shipping an echod that refuses without one would take them down, so:

1. The OTA step checks for board.json before it installs an echod that needs it. No file, no update.
2. A one-time migration writes board.json only when the unit matches exactly one board through a key that
   is already proven exact on real units.
3. Any unit that would need a guess to identify stays on its current echod and goes on a list for a
   person to identify.

Failing closed here means "this unit doesn't update", never "this unit stops working".

### Must go: echod's guess

`echod/internal/layout/device_cronos.go:94-111` (`showBoard`) matches the `lcm=` panel name, and when
there's no panel name it guesses: `amazon-gating` present means checkers, absent means cronos. A crown
without a panel name would come out as checkers. That's exactly the kind of fallback the catalogue
removes. It must not be copied into the catalogue, and it goes when echod reads board.json (step c).

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

Nothing in this slice reads evidence yet. The format and the check come with step b.

## Next owners to fold in

Not part of slice a; they're listed so step b doesn't miss them.

- `boot_logo.py` `KAERU` and `install-show.py` `LOGO_KAERU`: kaeru offsets, lengths and hashes. The two
  disagree on crown's "with logo" hash, and `install-show.py` pins a stock cronos kaeru hash while the
  cronos Amonet zip itself is unpinned.
- `amonet_upgrade.py` `TARGETS`: the partition map, checkers only ("Only Checkers has been mapped on a
  real unit"). Partition layout is a board fact.
- `dashcast/serve.go` geometry, `manifest.go` board list, `install-show.py` `BOARDS`.
- `boards.profile_for_product` folds case and strips spaces; the catalogue doesn't. Step b has to pick
  one, and exact is the rule.

## Migration, in order

a. **Catalogue, checker, resolver, explain, parity test.** Done in this branch, nothing rewired.
b. Installer reads the catalogue. `boards.py`, the duplicated parts of `profiles/*.json` and the pin
   constants in `install.py`, `recovery.py`, `unlock.py` and `assets.py` go in the same PR. Product
   entity moves in. Evidence format and check land here.
c. board.json written at flash time. echod reads it next to its constants with a test that they match,
   then the constants go, the guess with them. Needs captured DT compatibles first.
d. Product mains (`cmd/echod-show`, `-dot`, `-spot`) replace the build tags. Shell scripts get resolved
   values as arguments.
e. CI grep for board names and build tags outside the catalogue and the mains, with a baseline count that
   may only go down.

## oddc answers

All nine design questions from this ticket were answered by oddc on 2026-10-13 and are folded in above. Nothing here is
waiting on oddc right now.
