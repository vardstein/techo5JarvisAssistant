# Jarvis Show OTA and supply-chain hardening

Jarvis Show treats an update as executable root-owned code. HTTPS is transport protection, not the release trust root. A release is accepted only when the whole chain below agrees.

## Release trust root

The Ed25519 public key embedded in `echod/internal/update/trust.go` is the same public key embedded in `tools/techo5lib.py`. The matching private seed stays outside Git and device images. The release script rejects a signing-key symlink or a key file readable by group/other users.

`mkmanifest` signs `manifest.json`, then immediately verifies the generated signature with the exact public key compiled into devices. A CI secret containing the wrong private seed therefore fails before publication.

## Signed release identity

A Jarvis Show manifest carries signed identity fields:

```json
{
  "product": "genbu-show-v1",
  "boards": ["crown", "checkers"],
  "version": "vX.Y.Z"
}
```

The updater and host installer both require the expected product and the physically detected board. `crown`, `checkers` and `cronos` are the boards Jarvis Show v1 accepts. Unknown, duplicate or missing board identities fail closed.

`cronos` came after the first release, so it reads a signed manifest of its own, `manifest-cronos.json`, with `"boards": ["cronos"]`. The `manifest.json` that older Crown and Checkers Shows read never names it: those Shows were built before `cronos` was a board and would refuse the whole manifest (`supportedBoards` and `ownManifestBoards` in `echod/internal/update/manifest.go`).

This is deliberately separate from CPU architecture. All three boards are MT8163; architecture alone cannot prevent a cross-product/cross-board release mistake.

## Download integrity and rootfs identity

The signed manifest covers URL, SHA-256 and byte length for the daemon/rootfs and any named installer asset. Downloads are size-bounded and SHA-256 verified before use.

For a rootfs update, the updater performs another check after download and before `slotctl` can unpack the image. The tarball must contain exactly one small regular file at:

```text
etc/jarvis-show-release.json
```

Its product, board list and version must agree with the signed manifest and the detected device. Missing, duplicate, malformed, wrong-product, wrong-board or wrong-version markers are rejected and the downloaded file is deleted.

The host release tool applies the same marker/version checks before it creates a signed manifest.

## Build-input authentication

Jarvis Show release builds do not silently float package versions.

- Alpine minirootfs: fixed URL and SHA-256.
- Wake-word models: immutable repository commits plus per-file SHA-256.
- Go modules: normal `go.sum` module verification.
- GitHub Actions: full commit-SHA pins, not mutable action tags.
- Alpine APK package names: exact version/release pins; if Alpine's current signed repository no longer carries that exact build, the release input fetch fails and requires an intentional pin review.
- Every downloaded APK: its Alpine RSA signature is verified before extraction against `/etc/apk/keys` from the SHA-pinned minirootfs. Its control stream must then match the repository checksum and its data stream must match the signed `.PKGINFO` datahash.
- Legacy v3.12 Wi-Fi packages: `apk` must accept their signatures during rootfs construction. There is no `--allow-untrusted` fallback.

The host build therefore does not bootstrap trust from the downloaded `apk.static` executable itself: the APK carrying that executable is authenticated before `apk.static` is extracted or run.

## Stable/dev channels

Stable and dev use the same signature and artifact checks. Channel selection changes only which signed manifest URL is fetched.

- stable: `https://github.com/vardstein/techo5JarvisAssistant/releases/latest/download/manifest.json`
- staging: `https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-staging/manifest.json`
- dev: `https://github.com/vardstein/techo5JarvisAssistant/releases/download/channel-dev/manifest.json`

Caches are channel-bound and cleared on a channel change. An in-flight response from an old channel is discarded. The updater refuses version downgrades and refuses to guess ordering from an unrankable locally built version.

## A/B rollback boundary

A valid release is written only to the inactive rootfs slot. It starts as a trial slot and must survive the existing health window before being committed good. Failed boots consume trial attempts and fall back to the previous good slot. Persistent user/device state remains on userdata and is not replaced by the rootfs update.

OTA does not rewrite the bootloader chain. Kernel/boot-image changes remain outside ordinary rootfs OTA until a rollback-safe boot-image design exists.

## Provenance

The GitHub release workflow generates build-provenance attestations for the daemon and shared rootfs before publication. Attestations are an audit/provenance aid; the device's actual authorization decision remains the embedded Ed25519 release key plus the signed manifest and artifact checks above.

## Residual trust

The release private key is a high-value secret. Compromise of that key authorizes malicious manifests, which is why it stays out of Git/device images and manual release tooling refuses loose key permissions.

The CI runner/build toolchain is still part of the producer-side trust boundary. Provenance, immutable action pins, exact package versions and authenticated APK/model inputs reduce that surface; they do not make a compromised maintainer account or signing environment harmless.
