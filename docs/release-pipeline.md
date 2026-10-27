# Jarvis Show release pipeline

Official release repository: `vardstein/techo5JarvisAssistant`.

The project has one canonical publishing path: `tools/release-jarvis-show.sh` locally or
`.github/workflows/release.yml` in GitHub Actions. The inherited generic TECHO5 PowerShell publisher is
disabled so this fork cannot accidentally publish Dot/Spot/upstream artifacts.

A release tag uses a rankable `vX.Y.Z` or prerelease version. CI tests the installer/daemon, builds the
shared ARMv7 Show rootfs, builds the Show daemon, and creates GitHub provenance attestations for both.
The rootfs builder embeds `etc/jarvis-show-release.json` naming `genbu-show-v1`, `crown`, `checkers`
and the exact release version.

The release signing seed is not stored in Git. GitHub Actions expects it in the repository secret
`JARVIS_SHOW_SIGN_KEY`; locally `--sign-key` points at the private 0600 seed. `mkmanifest` signs the
manifest, and devices/installers trust only the corresponding embedded Ed25519 public key.

Stable is GitHub's latest non-prerelease release. The `dev` GitHub release carries only a rolling
manifest+signature that points at immutable versioned release assets, and the publisher moves that
manifest only forward according to the same version ordering used by the device.

Boot images are optional release assets until each board-specific image has passed the final anti-brick
and hash-pinning gates. A rootfs release can still serve existing A/B-installed devices without them.

## Manifest names

Each release carries three signed manifests:

- `manifest.json` for crown and checkers, as the first Shows were built to read it.
- `manifest-cronos.json` for cronos, which came later (see `supply-chain-hardening.md`).
- `manifest-v2.json`, which names every board the rootfs lists and every boot image, under one name.

The bridge release (GENBU-191) signs all three itself. A Show on it reads `manifest-v2.json` only once
its `board.json` agrees with what echod finds on the board. Until then it stays on the old names and
logs why at start. That log line is how to find the Shows that can't be identified yet.

Every release after the bridge needs `board.json`, so it signs only `manifest-v2.json`, and echod reads
only that name. Its `manifest.json` and `manifest-cronos.json` are the bridge's, copied unchanged, so a
Show that can't be identified stays on the bridge instead of taking a release it can't check.
`release-jarvis-show.sh` takes the bridge from `tools/ota_bridge.txt`:

- The newest tag there that the channel may carry and this release ranks above. Stable takes only a
  stable bridge, staging a staging or stable one, dev any.
- The bridge's files have to match its `SHA256SUMS`, and `mkmanifest -check` has to find each one signed
  by the release key, of this product and the bridge's version, naming the boards that name stands for.
- With no tag there, the script stops and nothing is published. A tag goes in by hand once that release
  is out and its Shows have been seen to go by `board.json`.

So the order is: release the bridge, pin its tag, deploy a Genbu Assistant that reads `manifest-v2.json`
first, then release from the v2 line. Home Assistant reads `manifest-v2.json` first and falls back to
`manifest.json` only when a release has none, since after the bridge `manifest.json` names the bridge's
version. The host installer (`techo5lib.py`) still reads `manifest.json`, so a fresh install gets the
bridge and the Show updates from there.

A v2-reading Show passes over boards it doesn't know in a manifest instead of refusing it, so a manifest
that names a newer board still works on the older ones. `mkmanifest` still refuses a board name the
release doesn't know, so a typo can't get into a manifest.
