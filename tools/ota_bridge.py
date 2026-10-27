#!/usr/bin/env python3
"""Which bridge release a Jarvis Show release copies the old manifest names from (GENBU-191).

Releases from GENBU-191 on go by board.json alone and sign only manifest-v2.json themselves. Shows from
before read manifest.json or manifest-<board>.json, so a release still carries those, copied as they
are from a bridge release: the last one that finds the board itself and writes board.json. A Show that
can't be told which board it is stays on the bridge instead of taking a release it can't check.

Bridge releases are pinned by hand in ota_bridge.txt, one tag per line, once each is out and its Shows
have been seen to go by board.json. A release copies from the newest pinned one its channel may carry
and that it ranks above, so a stable release never points stable Shows at a dev build. When there is
none, there is no release.
"""
from __future__ import annotations

import argparse
import sys
from functools import cmp_to_key
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_channels import CARRIES, channel_of, newer  # noqa: E402

PINS = Path(__file__).resolve().parent / "ota_bridge.txt"
# A stable release is read by stable Shows, so it may only point them at a stable bridge.
MAY_CARRY = {**CARRIES, "stable": {"stable"}}


def pinned(text: str) -> list[str]:
    """The tags in ota_bridge.txt's text; # starts a comment."""
    tags = []
    for n, line in enumerate(text.splitlines(), 1):
        tag = line.split("#", 1)[0].strip()
        if not tag:
            continue
        if channel_of(tag) is None:
            raise SystemExit(f"ota_bridge.txt line {n}: {tag!r} is not a release name")
        tags.append(tag)
    return tags


def bridge(tags: list[str], channel: str, version: str) -> str:
    """The bridge a channel's release version copies the old names from."""
    ok = [t for t in tags if channel_of(t) in MAY_CARRY[channel] and newer(version, t)]
    if not ok:
        raise SystemExit(f"no bridge release recorded for {version} on {channel} (GENBU-191): "
                         "pin one in tools/ota_bridge.txt once it is out")
    return max(ok, key=cmp_to_key(lambda a, b: 1 if newer(a, b) else -1 if newer(b, a) else 0))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--channel", required=True, choices=sorted(MAY_CARRY))
    ap.add_argument("--version", required=True)
    a = ap.parse_args(argv)
    print(bridge(pinned(PINS.read_text(encoding="utf-8")), a.channel, a.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
