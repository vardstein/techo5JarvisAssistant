# Jarvis Show OTA channels

Jarvis Show has three compiled-in release streams under the same signed release trust root:

- `stable`: `https://github.com/vardstein/techo5JarvisAssistant/releases/latest/download/manifest.json`
- `staging`: `https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-staging/manifest.json`
- `dev`: `https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest.json`

Stable follows GitHub's latest non-prerelease release. Staging and dev follow the rolling
`channel-staging` and `channel-dev` releases, which hold only the signed manifest of the release they
point at. Changing the update channel (Home Assistant select or the Show's own settings) stores only
`stable`, `staging` or `dev`; it cannot configure an arbitrary URL.

## Where releases come from

Each channel has a branch of the same name. A push to it runs `.github/workflows/release.yml`, which
tests, builds the rootfs, signs and publishes a release on that channel:

| Branch    | Version            | Moves channels         |
|-----------|--------------------|------------------------|
| `dev`     | `vX.Y.Z-dev.N`     | dev                    |
| `staging` | `vX.Y.Z-staging.N` | staging, dev           |
| `stable`  | `vX.Y.Z-stable.N`  | stable, staging, dev   |

`X.Y.Z` is the `VERSION` file, the next stable version. `N` is the workflow run number, which the three
branches share, so for one VERSION the later build ranks higher whichever channel it names. The Show,
the release script and Home Assistant all rank them that way: Home Assistant's AwesomeVersion knows
`dev` but not `staging` or `stable`, and ranks those by their number alone. Ranking by the word would
put `stable` below `staging`, as plain text order does. A release moves the less stable channels too,
so dev never offers something older than what stable already has. Channels only ever move forward.

The stable release is GitHub's "latest" release even though its name has a suffix, because the
release script marks it latest and the others prerelease. `v1.0.0` predates channel names; a bare
`vX.Y.Z` still counts as stable and ranks past every build of the same `X.Y.Z`.

Usual flow: finished features land on `dev`; when a set of them is good, fast-forward `staging` to it;
when staging has run fine on the house's Shows, fast-forward `stable` to staging, with release notes in
`release-notes/X.Y.Z.md`. [WORKFLOW.md](WORKFLOW.md) has the whole path, from feature branch to stable. After a stable release, bump `VERSION` on `dev` before the
next push. The workflow stops if VERSION is already released on stable (`vX.Y.Z-stable.N` or a bare
`vX.Y.Z`), so there is never a second stable release of one version.

Pushes that only touch `docs/`, `audit/` or Markdown files don't cut a release. Neither does creating a
channel branch at a commit GitHub already has, because that push changes no files. In both cases run the
workflow by hand on the branch with the version left empty, and it releases the branch like a push:

    gh workflow run release.yml --ref dev

A `v*.*.*` tag push, or a manual run with a version, picks the channel from the version instead (no
suffix or `-stable.N`: stable, `-staging.N` or `-rc.N`: staging, anything else: dev).

## On the device

A manifest cache is channel-bound. Changing channel clears the previous offer immediately. If a fetch
fails during installation, only a cache produced by the currently selected channel may be reused. If
the selection changes while a network request is in flight, that result is discarded even when its
signature is valid.

Version ordering is fail-closed. A release older than the running version is never installed, so moving
a unit from staging to dev shows nothing until dev passes it. A running build whose version cannot be
ranked is also barred from network OTA rather than guessing whether a signed release is newer. Proper
release/dev builds must use a rankable `vX.Y.Z` style stamp.

All channels use the same Ed25519 verification, size/hash checks, inactive-slot install, trial boot,
health commit and automatic rollback contract.
