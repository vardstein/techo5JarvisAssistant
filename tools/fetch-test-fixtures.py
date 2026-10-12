#!/usr/bin/env python3

import hashlib
import os
import pathlib
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]

FIXTURES = (
    (
        "https://github.com/HuskerMinion/techo5/releases/download/v1.0.1/techo5-boot-crown-v1.0.1.img",
        ROOT / "build/release-techo5-v1.0.1/techo5-boot-crown-v1.0.1.img",
        "e00bed964d9b2ce31df152d183d2aeb45a7839cc5f7460a93dd97d70715ad1b7",
    ),
    (
        "https://github.com/HuskerMinion/techo5/releases/download/v0.9.21/rootfs-v0.9.21.tar.gz",
        ROOT / "build/release-techo5-v0.9.21/rootfs-v0.9.21.tar.gz",
        "5eb5c9ee216535aae3bb6f29498a23b925b4dcb4fa2f64d1e40350defddb961e",
    ),
)


def sha256(target):
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        actual = sha256(target)
        if actual == expected:
            print(f"PASS: {target.relative_to(ROOT)} already verified")
            return
        print(f"WARN: replacing invalid fixture {target.relative_to(ROOT)}")

    fd, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(fd)
    temporary = pathlib.Path(temporary_name)

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "jarvis-show-test-fixture-fetcher"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            with open(temporary, "wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)

        actual = sha256(temporary)
        if actual != expected:
            raise RuntimeError(
                f"SHA-256 mismatch for {target.name}: "
                f"expected {expected}, got {actual}"
            )

        os.replace(temporary, target)
        print(f"PASS: fetched and verified {target.relative_to(ROOT)}")
    finally:
        temporary.unlink(missing_ok=True)


def main():
    for url, target, expected in FIXTURES:
        fetch(url, target, expected)


if __name__ == "__main__":
    main()
