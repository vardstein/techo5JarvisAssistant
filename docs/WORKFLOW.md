# How work moves: branches, Plane, commits, releases

Set up on 2026-10-11 from Jade's outline. It is built on the way JODS and gjallarOS work, and since
2026-10-20 its items, branch names and merges follow ODDC's rules (GENBU-198). The one difference: the
Shows update themselves from three channels, so a change goes dev → staging → stable, and each step is
a release that real Shows install. See [ota-channels.md](ota-channels.md) for how the channels serve
updates.

## The short version

1. A **feature** is a whole thing a user notices: music that follows you, DashCast on the Show, one
   update point per house. It gets a branch cut from `dev` and a parent item in Plane. Only one feature
   is being built at a time.
2. Its jobs are **tasks** and **fixes**: branches cut from the feature, merged back into it by pull
   request. The merge deletes the branch.
3. When the whole feature works, it is merged into `dev` the same way. Every push to `dev` that touches
   more than docs is a dev release, and the test Shows on the dev channel install it.
4. When `dev` holds a set of finished features worth shipping, `staging` is fast-forwarded to it. The
   house's Shows on the staging channel run it for real.
5. When staging has run fine and Jade confirms it, `stable` is fast-forwarded to staging, with release
   notes.

Never merge a half-built feature into `dev` just to get a part of it out, and never send each feature
to stable on its own. Stable gets a release when a feature, or a set of them, is complete and tested.

## Branches

| Branch | Cut from | Merged into | What it is |
|---|---|---|---|
| `stable` | – | – | What every house runs. Only ever fast-forwarded to `staging`. The default branch on GitHub. |
| `staging` | – | – | The release candidate. Only ever fast-forwarded to `dev`. |
| `dev` | – | – | Finished features waiting to be shipped. Takes features through pull requests merged with a merge commit. |
| `feature/genbu-<n>-<slug>` | `dev` | `dev` | One feature. `<n>` is its Plane item, e.g. `feature/genbu-14-music-follows-you`. |
| `task/genbu-<n>-<slug>` | its feature | its feature | One job of a feature. |
| `fix/genbu-<fixed>-fixNN-<slug>` | its feature, or `stable` for a hotfix | its feature, or see hotfixes | One bug. `<fixed>` is the item it fixes and `NN` counts that item's fixes, e.g. `fix/genbu-31-fix01-main-not-picked`. |

- Every branch is worked on in its own git worktree next to the repo (`../src-<slug>`), so two jobs never
  share a tree.
- Every merge of a feature, task or fix branch is a pull request into its target, merged with
  `gh pr merge <pr> --merge --delete-branch`. That is a `--no-ff` merge commit, so the history shows which
  commits made up which job; never squash or rebase a pull request.
- The merge deletes the branch: GitHub deletes it on origin (the repository has "Automatically delete
  head branches" on), and `--delete-branch` deletes it locally. Remove its worktree first
  (`git worktree remove ../src-<slug>`), because git won't delete a branch a worktree still has checked
  out. No branch is left behind after its merge.
- `staging` and `stable` only move with `git push origin dev:staging` and `git push origin staging:stable`.
  Both are fast-forwards; if one is refused, something was committed where it should not have been, so
  stop and look. The one exception is a hotfix, below.
- Every branch is pushed while it is worked on, so nothing lives only on one laptop.
- A feature that is already in `dev` has no feature branch left to cut from. A fix for it, or a task
  added to it later, is cut from `dev` and its pull request goes into `dev`, the way a hotfix does.
- Branches from before 2026-10-20 don't all have these names, and some went into `dev` as fast-forwards.
  That history stays as it is.
- `genbuShowCrown` predates this. It was the feature branch for everything built before 2026-10-11, went
  into `dev` like any feature and was retired on 2026-10-13 (GENBU-197): nothing is pushed to it any more,
  and links point at `stable`. It stays on origin until a stable release carries those links, because
  installed blueprints and Genbu's documentation link still point at it.

Who does what: Claude may create, push, merge and delete `feature/`, `task/` and `fix/` branches, and
since 2026-10-11 may merge a finished feature into `dev` and push `dev`. Moving `staging` comes after the
human tests on the dev Shows, done with Jade step by step. A stable release is Jade's call.

### Hotfixes

When something on stable is broken and cannot wait for the next release:

1. Cut `fix/genbu-<fixed>-fixNN-<slug>` from `stable` and fix it there.
2. If `dev` holds nothing else that is not ready for stable, its pull request goes into `dev`. Let the
   dev Shows install it, then promote as usual (dev → staging → stable).
3. If it does, its pull request goes into `stable` instead. The fix branch then also raises the patch
   version in `VERSION` and brings its release notes, since stable won't build a version that is already
   out. Then merge `stable` into `staging` and `staging` into `dev`, both locally with
   `git merge --no-ff` and pushed. Each channel then holds the one after it again, so the next
   fast-forward from `dev` still works.

## Testing at each step

| Step | What has to pass first |
|---|---|
| Commit on a task or fix branch | The checks for what it touches, run by hand: `python3 -m unittest discover -s tests -p 'test_*.py'`, `(cd echod && go test ./...)`, and for `ha/` the Home Assistant harness ([update-leader.md](update-leader.md#tests)). A few installer tests need real images in the gitignored `build/`; they skip until `python3 tools/fetch-test-fixtures.py` has fetched them (in CI, where that runs first, they fail instead). |
| Push of any branch | `python3 tools/secret_scan.py --here`: no secrets, and nothing only this machine knows (its addresses, home directory, our SSH keys). The repo is public. CI runs the same scan without `--here`. |
| Task or fix → feature | The above, plus CI (`build.yml` runs on every push to `feature/**`, `task/**` and `fix/**`, and on every pull request). |
| Feature → `dev` | Everything in the feature's Plane item done; its human test steps written in the item; CI green. |
| `dev` → `staging` | The dev release installed and committed on the dev test Show (slot trial passed); the human test steps done there; nothing open in Plane for those features. |
| `staging` → `stable` | Staging ran on the house's Shows for at least two days, including one night with auto-install on; nothing new in Repairs or the logs; Jade has confirmed; `release-notes/X.Y.Z.md` is written. |
| After stable | Bump `VERSION` on `dev` before its next push. The release workflow refuses to build a version that is already out on stable. |

## Release notes

A stable release needs `release-notes/X.Y.Z.md` (X.Y.Z from `VERSION`). The release workflow stops on
`stable` without it and uses it as the GitHub release text. Dev and staging releases keep their one-line
notes.

Write it on `dev` while the release is on staging, from the Plane items in that release. Keep it for the
people who own a Show, not for developers:

```markdown
## What's new
- Music plays in the room you are in, and follows you when you move.

## Fixed
- The bedroom Show no longer plays on the living room's speaker.

## Before you update
- Shows on 1.0.1 or older must be reinstalled once (new product id).
```

## Plane

Project **Genbu Show** (identifier `GENBU`).

- **Feature**: a top-level item with the Feature label, holding one thing a user notices. Only one
  feature is open at a time: In Progress, with its branch not yet merged into `dev`.
- **Task**: a child of the feature with the Task label, covering one piece of work.
- **Fix**: a child of the item it fixes, with the Fix label. A fix to something already merged is its
  own item.
- Titles are plain statements of the behaviour ("Music plays on the Show in the room it was asked in"),
  with `Bug:` in front for a bug.
- **Order is the board's sort order**, lowest first. Weight is never written into titles or text; items
  from before 2026-10-20 that still open with `Weight N.` keep that text, but the sort order is what counts.
- The description holds what and why, the design doc, open questions written out, and for a feature its
  human test steps.
- **States follow the code**: Backlog → Todo → In Progress → On dev → On staging → Done (= on stable).
  Cancelled for dropped work. A task or fix is Done when it is merged into its feature; a feature moves
  with `dev`, `staging` and `stable`.
- **Modules** are the big areas (Updates & releases, Installer, Music & radio, DashCast & dashboards,
  Voice, Display & UI, Boot & hardware, Security, Home Assistant). **Labels**: Feature, Task, Fix,
  Security, Testing.
- **Cycles** are releases, named after the version (`1.0.3`). A feature joins the cycle when it is
  merged into `dev`. The cycle's items are the release notes' raw material.
- Progress goes in as comments: what was built, the pull request and its merge commit, which checks
  ran, which Show it was tested on.

## Commits

```
<area>: <plain sentence, lowercase>

What was wrong or missing, why, what changed, and where it was tested
(unit tests, HA harness, on livingRoomEcho8 ...). Short; leave it out if the subject says it all.

Refs: GENBU-31
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

- Areas are the part of the repo: `echod`, `installer`, `ha`, `rootfs`, `boot-logo`, `dashcast`, `ci`,
  `docs`, `release`, or a feature inside echod (`music`, `display`, `firmware`).
- The format starts with the commits on `genbuShowCrown` from 2026-10-11. Older history stays as it
  is; the work before that is in Plane, backfilled as features with their tasks.
- One logical change per commit. Commits are authored as `vard <vard@openjade.de>`.
- Merge commits keep GitHub's default subject, and their body is the pull request's title with the
  merged item's trailer:

  ```
  gh pr merge 12 --merge --delete-branch --body "$(printf '%s\n\nRefs: GENBU-31' 'Installer picks the main Show')"
  ```

- History that is already on `dev`, `staging` or `stable` is never rewritten: release tags and the
  Shows' manifests point at it.

## Versions

- `VERSION` holds the next stable version, `X.Y.Z`.
- Each push to a channel branch releases `vX.Y.Z-<channel>.N`, N being the workflow run number, which
  the three branches share. See [ota-channels.md](ota-channels.md).
- Raise the minor version (1.1.0) for a release with new features and the patch version (1.0.4) for a
  release of fixes only.
