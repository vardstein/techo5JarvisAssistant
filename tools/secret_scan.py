#!/usr/bin/env python3
"""Look for secrets in what is about to be pushed. The repo is public, so anything here is out for good.

Without options it checks every tracked file (and every untracked one that is not ignored) for things
that are secret wherever they appear: private key bodies, access tokens of the usual services, Home
Assistant tokens (JWTs) and passwords or keys written into code outside the tests. CI runs it this way.

--here adds what only this machine knows, and is meant for the moment before a push: addresses on the
networks this machine is on, its home directory, and our own public SSH keys. It also checks the lines
added by the commits that no origin branch has yet, so something added and removed again before a push
is still caught.

It prints where and what kind, never the value. A line that has to hold something that looks like a
secret (a fixture, an example) is let through with "secret-scan: allow" on it.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW = "secret-scan: allow"

ANYWHERE = {
    "access token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}"
        r"|sk-(?:ant-)?[A-Za-z0-9_-]{20,}|plane_api_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{35})"),
    "Home Assistant token (JWT)": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
KEY_HEAD = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
KEY_BODY = re.compile(r"^[\s\"'`]*[A-Za-z0-9+/=]{40,}")
ASSIGNED = re.compile(
    r"(?i)\b\w*(?:password|passwd|passphrase|secret|token|api_?key|psk|seed)\w*[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']")
NOT_A_SECRET = re.compile(r"(?i)example|dummy|fake|placeholder|changeme|redacted|xxxx|\$\{|\$\(|<[a-z_]+>|\{\{")
PRIVATE_V4 = re.compile(r"\b(?:10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith("tests/") or "/tests/" in path or "/testdata/" in path
            or name.endswith("_test.go") or name.startswith("test_"))


class Local:
    """What only this machine knows: its networks, its home and our public keys."""

    def __init__(self):
        out = subprocess.run(["ip", "-4", "addr"], capture_output=True, text=True).stdout
        self.nets = [n for n in (ipaddress.ip_interface(a).network for a in re.findall(r"inet (\S+)", out))
                     if not n.is_loopback]
        self.home = str(Path.home())
        self.keys = set()
        for pub in Path.home().glob(".ssh/*.pub"):
            try:
                self.keys |= {line.split()[1] for line in pub.read_text().splitlines() if line.startswith("ssh-")}
            except (OSError, IndexError):
                pass

    def findings(self, line: str):
        for a in PRIVATE_V4.findall(line):
            try:
                if any(ipaddress.ip_address(a) in n for n in self.nets):
                    yield "address on this machine's network"
            except ValueError:
                pass
        if re.search(re.escape(self.home) + r"\b", line):
            yield "this machine's home directory"
        for body in re.findall(r"\bssh-(?:ed25519|rsa|ecdsa[\w-]*) (AAAA[A-Za-z0-9+/=]+)", line):
            if body in self.keys:
                yield "one of our own public SSH keys"


def scan_lines(path: str, lines, local: Local | None = None):
    """Yield (line number, kind) for each finding in a file's lines."""
    test = is_test(path)
    prev_head = False
    for n, line in enumerate(lines, 1):
        if ALLOW in line:
            prev_head = False
            continue
        if prev_head and KEY_BODY.match(line):
            yield n - 1, "private key"
        prev_head = bool(KEY_HEAD.search(line))
        for kind, rx in ANYWHERE.items():
            if rx.search(line):
                yield n, kind
        if not test:
            for value in ASSIGNED.findall(line):
                if not NOT_A_SECRET.search(value):
                    yield n, "password or key in the code"
        if local:
            for kind in local.findings(line):
                yield n, kind


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          errors="replace", check=True).stdout


def scan_tree(local: Local | None):
    for path in git("ls-files", "-co", "--exclude-standard").splitlines():
        try:
            data = (ROOT / path).read_bytes()
        except (IsADirectoryError, FileNotFoundError):
            continue
        if b"\0" in data[:8192]:
            continue
        for n, kind in scan_lines(path, data.decode("utf-8", "replace").splitlines(), local):
            yield f"{path}:{n}", kind


def scan_unpushed(local: Local | None):
    """Lines added by commits that are on no origin branch yet."""
    log = git("log", "HEAD", "--not", "--remotes=origin", "-p", "--no-color", "-U0", "--format=@@commit %h")
    commit, path, added = "", "", []

    def flush():
        for _, kind in scan_lines(path, added, local):
            yield f"{commit} {path}", kind

    for line in log.splitlines():
        if line.startswith("@@commit ") or line.startswith("+++ "):
            yield from flush()
            added = []
            if line.startswith("@@commit "):
                commit = line.split()[1]
            else:
                path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+"):
            added.append(line[1:])
    yield from flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--here", action="store_true", help="also check this machine's addresses, home and keys, "
                    "and the commits not pushed yet")
    args = ap.parse_args(argv)
    local = Local() if args.here else None
    found = sorted(set(scan_tree(local)) | (set(scan_unpushed(local)) if args.here else set()))
    for where, kind in found:
        print(f"{where}: {kind}")
    if found:
        print(f"{len(found)} finding(s). Take them out, or mark a fixture with '{ALLOW}'.", file=sys.stderr)
        return 1
    print("secret scan: clean" + (" (with this machine's checks)" if args.here else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
