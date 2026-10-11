#!/usr/bin/env python3
"""The Jarvis Show root filesystem from a published release, for an install given no --rootfs.

Releases come in the daemon's three channels (echod/internal/update/channel.go): stable is GitHub's
"latest", staging and dev are rolling releases that hold only the signed manifest of the build they
point at. The manifest is believed only with the release key's signature over it (techo5lib.Release),
and has to name this product and this board; the root filesystem is then held to the manifest's hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import techo5lib
from jarvis_crown.install import PROFILE_ID

CHANNELS = ("stable", "staging", "dev")
DEFAULT_CHANNEL = "stable"

_TAGS = {"stable": "latest", "staging": "channel-staging", "dev": "channel-dev"}


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseRootfs:
    path: Path
    sha256: str
    version: str
    channel: str


def channel_tag(channel: str) -> str:
    try:
        return _TAGS[channel]
    except KeyError:
        raise ReleaseError(f"no release channel {channel!r}; one of {', '.join(CHANNELS)}") from None


def fetch_rootfs(channel: str, board: str, work_dir: Path, *, release=techo5lib.Release,
                 repo: str | None = None) -> ReleaseRootfs:
    """Downloads the channel's root filesystem into work_dir, checked against the signed manifest."""
    tag = channel_tag(channel)
    repo = repo or techo5lib.JARVIS_SHOW_REPO
    try:
        rel = release(repo, tag, str(work_dir), expected_product=PROFILE_ID, expected_board=board)
        path = Path(rel.rootfs("arm"))
    except techo5lib.Fail as exc:
        others = [c for c in CHANNELS if c != channel]
        raise ReleaseError(
            f"no usable {channel} release for {board}: {exc}. "
            f"Try --channel {' or --channel '.join(others)}, or pass --rootfs with --rootfs-sha256"
        ) from None
    return ReleaseRootfs(path=path, sha256=techo5lib.hash_file(str(path)), version=rel.version,
                         channel=channel)
