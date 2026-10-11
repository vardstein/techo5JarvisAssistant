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
