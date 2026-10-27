#!/usr/bin/env python3
"""Point the rolling update channels (channel-staging, channel-dev) at the newest release they may carry.

A channel is a GitHub release holding only manifest.json and manifest.json.sig, copied from the release
it points at. Each channel follows a rule rather than the last run that touched it: dev carries the
newest dev, staging or stable release, staging the newest staging or stable one. So a run never asks
"is mine newer than what the channel holds?", it works out what the channel should hold from the
release list and makes it so, then reads the channel back. That matters when dev, staging and stable
are pushed at once: three runs write the same two files, and gh's --clobber is a delete and then an
upload, so one run can leave the manifest of one release next to the signature of another. Every run
works towards the same pair and checks its work afterwards, so the last one to look puts right whatever
an earlier one left half done.

A release's channel comes from its name, as in release-jarvis-show.sh: -stable.N or no suffix is
stable, -staging.N or -rc.N is staging, any other prerelease is dev.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from functools import cmp_to_key
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_crown.catalogue import later_boards, load_or_exit  # noqa: E402
from techo5lib import JARVIS_SHOW_PRODUCT  # noqa: E402

load_or_exit()  # GENBU-173: a broken catalogue stops here, in plain words
PAIR = ("manifest.json", "manifest.json.sig")
# Boards older Shows' echod doesn't know read their own manifest (echod ManifestName); a release carries
# it, copied from its bridge release (GENBU-191), once the bridge has it. A channel copies it along when
# its release has it. Which boards
# those are comes from the board catalogue's first_boards (GENBU-176), of the product these releases are
# (GENBU-192: another product would have its own releases).
EXTRA_PAIRS = tuple((f"manifest-{b}.json", f"manifest-{b}.json.sig") for b in later_boards(JARVIS_SHOW_PRODUCT))
# And Shows whose board.json is checked read manifest-v2.json, every board alike (GENBU-191).
EXTRA_PAIRS += (("manifest-v2.json", "manifest-v2.json.sig"),)
EXTRA = tuple(name for two in EXTRA_PAIRS for name in two)
RANK = {"dev": 0, "staging": 1, "stable": 2}
# Which releases a channel may carry.
CARRIES = {"dev": {"dev", "staging", "stable"}, "staging": {"staging", "stable"}}


class ChannelError(RuntimeError):
    pass


def channel_of(version: str) -> str | None:
    """The channel a release name belongs to, or None when it is not a release name."""
    if not parse(version):
        return None
    if "-" not in version or "-stable." in version:
        return "stable"
    if "-staging." in version or "-rc." in version:
        return "staging"
    return "dev"


def parse(v: str):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?", v or "")
    if not m:
        return None
    core = tuple(map(int, m.group(1, 2, 3)))
    pre = m.group(4)
    fields = [] if pre is None else [int(x) if x.isdigit() else x for x in pre.split(".")]
    return core, pre is None, fields


def _channel_build(fields):
    if len(fields) < 2 or fields[0] not in RANK or not all(isinstance(x, int) for x in fields[1:]):
        return None
    return fields[1:], RANK[fields[0]]


def newer(a: str, b: str) -> bool:
    """True when a ranks above b. CI builds (dev.N, staging.N, stable.N) rank by their numbers whatever
    channel they name: N is the shared run number, and it is how the Show and Home Assistant rank them
    (echod internal/update/version.go)."""
    if not b:
        return True
    pa, pb = parse(a), parse(b)
    if not pa or not pb:
        return False
    if pa[0] != pb[0]:
        return pa[0] > pb[0]
    if pa[1] != pb[1]:
        return pa[1] and not pb[1]
    ca, cb = _channel_build(pa[2]), _channel_build(pb[2])
    if ca and cb:
        n = max(len(ca[0]), len(cb[0]))
        xa, xb = ca[0] + [0] * (n - len(ca[0])), cb[0] + [0] * (n - len(cb[0]))
        return (xa, ca[1]) > (xb, cb[1])
    for x, y in zip(pa[2], pb[2]):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return x > y
        return str(x) > str(y)
    return len(pa[2]) > len(pb[2])


def target(releases: list[dict], channel: str) -> str | None:
    """The newest published release the channel may carry that has its signed manifest."""
    ok = []
    for r in releases:
        tag = r.get("tag_name", "")
        names = {a.get("name") for a in r.get("assets", [])}
        if r.get("draft") or tag.startswith("channel-") or not set(PAIR) <= names:
            continue
        if channel_of(tag) in CARRIES[channel]:
            ok.append(tag)
    if not ok:
        return None
    return max(ok, key=cmp_to_key(lambda a, b: 1 if newer(a, b) else -1 if newer(b, a) else 0))


class GitHub:
    """The few gh calls a channel move needs."""

    def __init__(self, repo: str):
        self.repo = repo

    def _gh(self, *args: str, out: bool = False) -> str:
        p = subprocess.run(["gh", *args], text=True, capture_output=True)
        if p.returncode:
            raise ChannelError(f"gh {' '.join(args[:3])}: {p.stderr.strip() or p.returncode}")
        return p.stdout if out else ""

    def releases(self) -> list[dict]:
        pages = self._gh("api", "--paginate", "--slurp", f"repos/{self.repo}/releases?per_page=100", out=True)
        return [r for page in json.loads(pages) for r in page]

    def exists(self, tag: str) -> bool:
        try:
            self._gh("release", "view", tag, "--repo", self.repo)
            return True
        except ChannelError:
            return False

    def create(self, tag: str, title: str, notes: str, commit: str | None) -> None:
        args = ["release", "create", tag, "--repo", self.repo, "--prerelease", "--latest=false",
                "--title", title, "--notes", notes]
        if commit:
            args += ["--target", commit]
        self._gh(*args)

    def download(self, tag: str, into: Path) -> dict[str, bytes]:
        """The tag's manifests as they are now; a file the release lacks is left out."""
        got = {}
        for name in PAIR + EXTRA:
            try:
                self._gh("release", "download", tag, "--repo", self.repo, "-p", name, "-D", str(into), "--clobber")
            except ChannelError:
                continue
            f = into / name
            if f.exists():
                got[name] = f.read_bytes()
                f.unlink()
        return got

    def upload(self, tag: str, files: list[Path]) -> None:
        self._gh("release", "upload", tag, *map(str, files), "--repo", self.repo, "--clobber")


def move(gh, channel: str, *, own: str | None = None, commit: str | None = None, attempts: int = 8,
         nap=time.sleep, log=print) -> str:
    """Make the channel hold the signed manifest of the release it should point at; returns that tag.
    own is the release this run just made. GitHub's release list can lag behind a release create, so
    until own shows up with its manifest, the run waits rather than settle on an older release."""
    tag = f"channel-{channel}"
    if not gh.exists(tag):
        try:
            gh.create(tag, f"Jarvis Show {channel} channel",
                      f"Rolling Jarvis Show {channel} channel. It holds only the signed manifest of the release it points at.",
                      commit)
        except ChannelError:
            # Another run may have made it in the meantime.
            if not gh.exists(tag):
                raise
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for n in range(attempts):
            releases = gh.releases()
            want = target(releases, channel)
            if own and channel_of(own) in CARRIES[channel] and target(
                    [r for r in releases if r.get("tag_name") == own], channel) is None:
                log(f"{channel} channel: {own} is not listed with its manifest yet")
                nap(min(2 ** n, 30))
                continue
            if want is None:
                raise ChannelError(f"no release with a signed manifest for the {channel} channel")
            pair = gh.download(want, work)
            if not set(PAIR) <= set(pair) or any(len(set(pair) & set(two)) == 1 for two in EXTRA_PAIRS):
                raise ChannelError(f"{want} is missing its signed manifest")
            # A board manifest an older release lacks stays on the channel as it was: that board's Shows
            # keep the last release that listed it, which is the one they could take anyway.
            have = gh.download(tag, work)
            if {k: v for k, v in have.items() if k in pair} == pair:
                log(f"PASS: {channel} channel holds {want}")
                return want
            log(f"{channel} channel: putting {want}'s signed manifest in place (try {n + 1})")
            files = []
            for name, data in pair.items():
                (work / name).write_bytes(data)
                files.append(work / name)
            try:
                gh.upload(tag, files)
            except ChannelError as e:
                log(f"{channel} channel: {e}")
            # Another run may be writing the same files; give it room before reading back.
            nap(min(2 ** n, 30) + random.uniform(0, 2))
    raise ChannelError(f"the {channel} channel did not settle on one release after {attempts} tries")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--target", help="commit a new channel tag is made on")
    ap.add_argument("--version", help="the release this run made; wait until GitHub lists it")
    ap.add_argument("channels", nargs="+", choices=sorted(CARRIES))
    args = ap.parse_args(argv)
    gh = GitHub(args.repo)
    try:
        for channel in args.channels:
            move(gh, channel, own=args.version, commit=args.target)
    except ChannelError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
