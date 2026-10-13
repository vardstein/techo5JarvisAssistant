#!/usr/bin/env bash
# Publish one Jarvis Show release from already-built, already-verified artifacts.
# This script NEVER builds a rootfs or boot image. CI/local build jobs produce those separately.
set -euo pipefail

REPO=${JARVIS_SHOW_RELEASE_REPO:-vardstein/techo5JarvisAssistant}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=
NOTES=
BINARY=
ROOTFS=
SIGN_KEY=${JARVIS_SHOW_SIGN_KEY:-}
CROWN_BOOT=
CHECKERS_BOOT=
CRONOS_BOOT=
PUBLISH=
CHANNEL=
TARGET=
OUT=${JARVIS_SHOW_RELEASE_OUT:-$ROOT/bin/release}

usage() {
  cat <<USAGE
usage: $0 --version vX.Y.Z[...prerelease] --notes TEXT --binary FILE --rootfs FILE \\
          --sign-key FILE [--crown-boot FILE] [--checkers-boot FILE] [--cronos-boot FILE]
          [--channel stable|staging|dev] [--target COMMIT] [--publish]

Without --publish the script only validates artifacts and writes signed manifest/SHA256SUMS into --out.
It does not build the rootfs or boot images.

When the rootfs lists cronos (in "more_boards"), the release also gets manifest-cronos.json: cronos
reads its own signed manifest so crown and checkers Shows, which refuse a manifest naming a board they
don't know, never see one that does. --cronos-boot is published as techo5-boot-VERSION.img, the name
install-show.py looks for on cronos.

--channel says which update channel the release is for, and must agree with the name: -stable.N or no
suffix is stable, -staging.N or -rc.N is staging and anything else is dev. Without it the name decides.
A release moves its own channel and every less stable one forward (stable: stable, staging, dev;
staging: staging, dev), never back.
--target is the commit a new release tag is made on; without it GitHub uses the default branch.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION=$2; shift 2;;
    --notes) NOTES=$2; shift 2;;
    --binary) BINARY=$2; shift 2;;
    --rootfs) ROOTFS=$2; shift 2;;
    --sign-key) SIGN_KEY=$2; shift 2;;
    --crown-boot) CROWN_BOOT=$2; shift 2;;
    --checkers-boot) CHECKERS_BOOT=$2; shift 2;;
    --cronos-boot) CRONOS_BOOT=$2; shift 2;;
    --out) OUT=$2; shift 2;;
    --channel) CHANNEL=$2; shift 2;;
    --target) TARGET=$2; shift 2;;
    --publish) PUBLISH=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2;;
  esac
done

[[ $VERSION =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "invalid --version: $VERSION" >&2; exit 2; }
[ -n "$NOTES" ] || { echo "--notes is required" >&2; exit 2; }
if [ -z "$CHANNEL" ]; then
  case $VERSION in
    *-stable.*) CHANNEL=stable;;
    *-staging.*|*-rc.*) CHANNEL=staging;;
    *-*) CHANNEL=dev;;
    *) CHANNEL=stable;;
  esac
fi
case $CHANNEL in
  stable) [[ $VERSION != *-* || $VERSION == *-stable.* ]] || { echo "a stable release is vX.Y.Z-stable.N or vX.Y.Z: $VERSION" >&2; exit 2; };;
  staging) [[ $VERSION == *-staging.* || $VERSION == *-rc.* ]] || { echo "a staging release is vX.Y.Z-staging.N or vX.Y.Z-rc.N: $VERSION" >&2; exit 2; };;
  dev) [[ $VERSION == *-* && $VERSION != *-stable.* && $VERSION != *-staging.* && $VERSION != *-rc.* ]] || { echo "a dev release is a prerelease other than -stable, -staging or -rc: $VERSION" >&2; exit 2; };;
  *) echo "invalid --channel: $CHANNEL" >&2; exit 2;;
esac
for pair in "binary:$BINARY" "rootfs:$ROOTFS" "signing key:$SIGN_KEY"; do
  label=${pair%%:*}; path=${pair#*:}
  [ -f "$path" ] || { echo "$label not found: $path" >&2; exit 2; }
done
python3 - "$SIGN_KEY" <<'PYKEY'
import os, stat, sys
p = sys.argv[1]
if os.path.islink(p):
    raise SystemExit("signing key must not be a symlink")
st = os.stat(p)
if not stat.S_ISREG(st.st_mode):
    raise SystemExit("signing key is not a regular file")
if st.st_mode & 0o077:
    raise SystemExit("signing key permissions are too open; require owner-only access (0600 or stricter)")
PYKEY
command -v go >/dev/null || { echo "go not found" >&2; exit 2; }
if [ -n "$PUBLISH" ]; then
  command -v gh >/dev/null || { echo "gh not found" >&2; exit 2; }
fi

mkdir -p "$OUT"
chmod 700 "$OUT"
cp "$BINARY" "$OUT/echod-arm"
cp "$ROOTFS" "$OUT/jarvis-show-rootfs-$VERSION.tar.gz"
assets=()
if [ -n "$CROWN_BOOT" ]; then
  [ -f "$CROWN_BOOT" ] || { echo "Crown boot image not found: $CROWN_BOOT" >&2; exit 2; }
  cp "$CROWN_BOOT" "$OUT/techo5-boot-crown-$VERSION.img"
  assets+=("$OUT/techo5-boot-crown-$VERSION.img")
fi
if [ -n "$CHECKERS_BOOT" ]; then
  [ -f "$CHECKERS_BOOT" ] || { echo "Checkers boot image not found: $CHECKERS_BOOT" >&2; exit 2; }
  cp "$CHECKERS_BOOT" "$OUT/techo5-boot-checkers-$VERSION.img"
  assets+=("$OUT/techo5-boot-checkers-$VERSION.img")
fi
cronos_assets=()
if [ -n "$CRONOS_BOOT" ]; then
  [ -f "$CRONOS_BOOT" ] || { echo "Cronos boot image not found: $CRONOS_BOOT" >&2; exit 2; }
  cp "$CRONOS_BOOT" "$OUT/techo5-boot-$VERSION.img"
  cronos_assets+=("$OUT/techo5-boot-$VERSION.img")
fi

# Rootfs has one shared product marker and must explicitly support both first-generation boards; cronos
# is optional and comes from "more_boards". The last line printed says whether it is there.
rootfs_sha=$(sha256sum "$ROOTFS" | awk '{print $1}')
cronos=$(PYTHONPATH="$ROOT/tools" python3 - "$ROOTFS" "$rootfs_sha" "$VERSION" <<'PY'
import pathlib, sys
from jarvis_crown.install import verify_jarvis_rootfs
path = pathlib.Path(sys.argv[1])
sha = sys.argv[2]
for board in ("crown", "checkers"):
    ident = verify_jarvis_rootfs(path, sha, board=board)
    if ident.product != "genbu-show-v1":
        raise SystemExit("rootfs is not a shared genbu-show-v1 image")
    if ident.version != sys.argv[3]:
        raise SystemExit(f"rootfs marker version {ident.version!r} does not match release filename")
print("PASS: shared Jarvis Show rootfs marker/version", file=sys.stderr)
print("cronos" if "cronos" in ident.boards else "")
PY
)
if [ -n "$CRONOS_BOOT" ] && [ "$cronos" != cronos ]; then
  echo "--cronos-boot given, but the rootfs does not list cronos" >&2; exit 2
fi

from="https://github.com/$REPO/releases/download/$VERSION"
mk=(run ./cmd/mkmanifest
  -version "$VERSION"
  -product "genbu-show-v1"
  -board crown
  -board checkers
  -title "Jarvis Show $VERSION"
  -notes "$NOTES"
  -release-url "https://github.com/$REPO/releases/tag/$VERSION"
  -from "$from"
  -arm "$OUT/echod-arm"
  -rootfs-arm "$OUT/jarvis-show-rootfs-$VERSION.tar.gz"
  -out "$OUT/manifest.json"
  -sign-key "$SIGN_KEY")
for asset in "${assets[@]}"; do mk+=(-asset "$asset"); done
(cd "$ROOT/echod" && go "${mk[@]}")
if [ "$cronos" = cronos ]; then
  mkc=(run ./cmd/mkmanifest
    -version "$VERSION"
    -product "genbu-show-v1"
    -board cronos
    -title "Jarvis Show $VERSION"
    -notes "$NOTES"
    -release-url "https://github.com/$REPO/releases/tag/$VERSION"
    -from "$from"
    -arm "$OUT/echod-arm"
    -rootfs-arm "$OUT/jarvis-show-rootfs-$VERSION.tar.gz"
    -out "$OUT/manifest-cronos.json"
    -sign-key "$SIGN_KEY")
  for asset in "${cronos_assets[@]}"; do mkc+=(-asset "$asset"); done
  (cd "$ROOT/echod" && go "${mkc[@]}")
fi

(
  cd "$OUT"
  files=(echod-arm "jarvis-show-rootfs-$VERSION.tar.gz" manifest.json manifest.json.sig)
  [ -z "$CROWN_BOOT" ] || files+=("techo5-boot-crown-$VERSION.img")
  [ -z "$CHECKERS_BOOT" ] || files+=("techo5-boot-checkers-$VERSION.img")
  [ "$cronos" != cronos ] || files+=(manifest-cronos.json manifest-cronos.json.sig)
  [ -z "$CRONOS_BOOT" ] || files+=("techo5-boot-$VERSION.img")
  sha256sum "${files[@]}" > SHA256SUMS
)

echo "PASS: signed release payload prepared in $OUT"
[ -n "$PUBLISH" ] || exit 0

release_args=(release create "$VERSION" --repo "$REPO" --title "Jarvis Show $VERSION" --notes "$NOTES")
[ -z "$TARGET" ] || release_args+=(--target "$TARGET")
# Stable is GitHub's "latest" release; everything else is a prerelease, so a stable device never sees it.
if [ "$CHANNEL" = stable ]; then release_args+=(--latest); else release_args+=(--prerelease --latest=false); fi
release_args+=("$OUT/echod-arm" "$OUT/jarvis-show-rootfs-$VERSION.tar.gz" "$OUT/manifest.json" "$OUT/manifest.json.sig" "$OUT/SHA256SUMS")
[ "$cronos" != cronos ] || release_args+=("$OUT/manifest-cronos.json" "$OUT/manifest-cronos.json.sig")
release_args+=("${assets[@]}" "${cronos_assets[@]}")
gh "${release_args[@]}"

# Staging and dev are rolling releases (channel-staging, channel-dev) holding only the signed manifest of
# the release they point at; the manifest names that release's own immutable assets. A release moves its
# own channel and the less stable ones, so dev never offers something older than staging or stable: a
# unit would refuse it and its update card would never clear (techo5 issue #42). A channel never moves
# back. The tags are not the branch names because a tag and a branch both called dev confuse git.
# release_channels.py points each channel at the newest release it may carry and reads it back, so runs
# for several branches pushed at once cannot leave one release's manifest next to another's signature.
case $CHANNEL in
  stable|staging) rolling=(staging dev);;
  dev) rolling=(dev);;
esac
channel_args=(--repo "$REPO" --version "$VERSION")
[ -z "$TARGET" ] || channel_args+=(--target "$TARGET")
python3 "$ROOT/tools/release_channels.py" "${channel_args[@]}" "${rolling[@]}"

echo "PASS: published https://github.com/$REPO/releases/tag/$VERSION ($CHANNEL)"
