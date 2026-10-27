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

A Show reads `manifest-v2.json` only once its `board.json` agrees with what echod finds on the board
itself. Until then it stays on the old names and logs why at start. That log line is how to find the
Shows that can't be identified yet.

The v2 manifest exists so that later releases can need `board.json` without stranding Shows that don't
have one. Those releases will publish only `manifest-v2.json` of their own. The old names stay on the
last release that writes `board.json` itself, so a Show that can't be identified stays there instead of
taking a release it can't check.

A v2-reading Show passes over boards it doesn't know in a manifest instead of refusing it, so a manifest
that names a newer board still works on the older ones. `mkmanifest` still refuses a board name the
release doesn't know, so a typo can't get into a manifest.
