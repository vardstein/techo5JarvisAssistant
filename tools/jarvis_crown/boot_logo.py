"""The OpenJade logo in place of Amazon's at boot, on a Show that already runs Jarvis.

What the bootloader paints before Linux starts is compiled into kaeru, amonet's bootloader in the expdb
partition (docs/hardware.md, "Boot logo (LK)"). Crown's and Checkers' kaeru hold Amazon's wordmark in
the same 6105-byte slot, byte for byte, at different places. tools/boot-logo/openjade-wordmark.bin is
that slot with the OpenJade lockup in it (tools/boot-logo/README.md says how it was made).

The install never touches expdb. This is a step of its own, asked for by name, over the Show's SSH:
the slot is written in place, so kaeru's header, length and pointers stay as they are, and only over the
kaeru amonet 2.0.1 put there. Afterwards the whole of kaeru is read back from the flash; if it is not
exactly what was meant, the slot that was there goes back at once. A unit whose kaeru is broken does not
start at all, so nothing here writes anywhere else, and nothing writes over a kaeru it does not know.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shlex
import subprocess
from typing import Callable, Protocol

from jarvis_crown.show_ssh import ssh_options

SLOT_SIZE = 6105
LOGO_FILE = Path(__file__).resolve().parents[1] / "boot-logo" / "openjade-wordmark.bin"
LOGO_SLOT_SHA256 = "c02f0d8180ed97bae4281c1bd9be4649903866b6e6c9025edb9145ad539dd5db"
AMAZON_SLOT_SHA256 = "471f83ad804f8bebcf0a3f659aa36bc5fbe102af145e17138065f0a9d2093e68"


@dataclass(frozen=True)
class Kaeru:
    offset: int  # where the wordmark slot is
    length: int  # kaeru's length, the part that is checked
    amazon: str  # sha256 of kaeru as amonet 2.0.1 ships it
    openjade: str  # and with the OpenJade slot in it


KAERU = {
    "checkers": Kaeru(335308, 393688,
                      "8107a24d3a667450a22f8c51ff49fe31b92e214d7c2a26857d33da47d94520bf",
                      "4f686dcbf906540cb1b0a33842fc425250fa3a423349528227ecc7ceb3139345"),
    "crown": Kaeru(288716, 481792,
                   "6a717f0f8bd3502df2e2a811f1a90a2ec83e58f151874e11c2920a9a63dc4fa1",
                   "e8d1fe05273ff74c0224ad98240df5319c46fa4459ebeeb2a24b5a365d50aa10"),
}


class BootLogoError(RuntimeError):
    pass


class Client(Protocol):
    def shell(self, command: str, data: bytes | None = None) -> str: ...


class SshClient:
    """root on the Show over SSH, with the Show's own key (show_ssh.py); never asks for a password."""

    def __init__(self, host: str, identity: Path | None = None, name: str | None = None):
        self.host = host
        self.identity = identity
        self.name = name  # the Show's name, whose pinned host key ssh checks

    def shell(self, command: str, data: bytes | None = None) -> str:
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *ssh_options(self.identity, self.name),
                                f"root@{self.host}", command],
                               input=data, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BootLogoError(f"ssh to {self.host}: {exc}") from exc
        if r.returncode != 0:
            raise BootLogoError(f"on the Show, {command!r} failed: {r.stderr.decode(errors='replace').strip() or r.returncode}")
        return r.stdout.decode(errors="replace")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expdb_block(client: Client) -> str:
    """/dev/mmcblk0pN of the one partition named expdb."""
    out = client.shell("for f in /sys/class/block/mmcblk0p*/uevent; do "
                       "busybox grep -qx PARTNAME=expdb \"$f\" && echo \"$f\"; done; true").split()
    if len(out) != 1:
        raise BootLogoError(f"expected one partition named expdb, found {len(out)}")
    return "/dev/" + out[0].split("/")[-2]


def kaeru_hash(client: Client, block: str, length: int) -> str:
    """Read from the flash, not from what the kernel still holds of it."""
    client.shell("busybox sync; echo 3 > /proc/sys/vm/drop_caches")
    out = client.shell(f"busybox head -c {length} {shlex.quote(block)} | busybox sha256sum").split()
    if not out:
        raise BootLogoError("the Show gave no hash of expdb")
    return out[0]


def write_slot(client: Client, block: str, offset: int, slot: bytes) -> None:
    assert len(slot) == SLOT_SIZE
    client.shell(f"busybox dd of={shlex.quote(block)} bs=1 seek={offset} count={SLOT_SIZE} conv=notrunc 2>/dev/null"
                 " && busybox sync", data=slot)


def amazon_slot(saved_expdb: Path, board: str) -> bytes:
    """Amazon's slot, from the expdb the installer saved off this unit before it wrote anything."""
    try:
        with open(saved_expdb, "rb") as f:
            f.seek(KAERU[board].offset)
            slot = f.read(SLOT_SIZE)
    except OSError as exc:
        raise BootLogoError(f"cannot read the saved expdb {saved_expdb}: {exc}") from exc
    if _sha(slot) != AMAZON_SLOT_SHA256:
        raise BootLogoError(f"{saved_expdb} does not hold Amazon's logo where kaeru keeps it")
    return slot


def put_logo(client: Client, board: str, *, saved_expdb: Path, amazon: bool = False,
             confirm: Callable[[str], str], progress: Callable[[str], None] = lambda _: None) -> str:
    """OpenJade's logo into kaeru's wordmark slot, or Amazon's back with amazon=True. Returns what was done."""
    if board not in KAERU:
        raise BootLogoError(f"no boot logo is known for board {board!r}")
    k = KAERU[board]
    logo = LOGO_FILE.read_bytes()
    if len(logo) != SLOT_SIZE or _sha(logo) != LOGO_SLOT_SHA256:
        raise BootLogoError(f"{LOGO_FILE} is not the logo this tool knows")
    amazons = amazon_slot(saved_expdb, board)
    if amazon:
        new_slot, old_slot, want, other = amazons, logo, k.amazon, k.openjade
    else:
        new_slot, old_slot, want, other = logo, amazons, k.openjade, k.amazon
    which = "Amazon's" if amazon else "the OpenJade"

    block = expdb_block(client)
    now = kaeru_hash(client, block, k.length)
    if now == want:
        return f"{which} boot logo is already there"
    if now != other:
        raise BootLogoError(f"kaeru in {block} is not amonet 2.0.1's for {board}; nothing written")
    progress(f"kaeru in {block} is amonet 2.0.1's for {board}")

    phrase = "AMAZON BOOT LOGO" if amazon else "OPENJADE BOOT LOGO"
    if confirm(phrase) != phrase:
        raise BootLogoError("not confirmed; nothing written")

    write_slot(client, block, k.offset, new_slot)
    if kaeru_hash(client, block, k.length) == want:
        return f"{which} boot logo written into {block} and read back from the flash"

    write_slot(client, block, k.offset, old_slot)
    if kaeru_hash(client, block, k.length) == now:
        raise BootLogoError("the logo did not read back right, so the slot that was there went back; kaeru is as before")
    raise BootLogoError(
        f"kaeru in {block} did not read back right, nor after putting the old slot back. Do NOT restart the Show.\n"
        f"  Write the saved copy back while it still runs:\n"
        f"    ssh root@<show> 'busybox dd of={block} bs=4096 && busybox sync' < {saved_expdb}\n"
        f"  and check it reads {k.amazon[:16]}... over the first {k.length} bytes.")
