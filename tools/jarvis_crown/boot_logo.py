"""The OpenJade logo in place of Amazon's at boot, on a Show that already runs Jarvis.

What the bootloader paints before Linux starts is compiled into kaeru, amonet's bootloader in the expdb
partition (docs/hardware.md, "Boot logo (LK)"). Crown's and Checkers' kaeru hold Amazon's wordmark in
the same 6105-byte slot, byte for byte, at different places. tools/boot-logo/openjade-wordmark.bin is
that slot with the OpenJade lockup in it (tools/boot-logo/README.md says how it was made).

That slot is drawn 315x170, which is small on Crown's 8" panel. Crown gets a 477x255 picture instead
(tools/boot-logo/openjade-wordmark-crown.bin), and with it the box kaeru draws in: its size is four
instruction immediates in the one function that draws the logo, 30 bytes before it in kaeru. Those bytes
are the only other thing written. kaeru unpacks the picture into the third page of its video memory,
2 MB at 1280x800, so the bigger one has room (docs/hardware.md, "Boot logo (LK)").

The install never touches expdb. This is a step of its own, asked for by name, over the Show's SSH:
the slot is written in place, so kaeru's header, length and pointers stay as they are, and only over a
kaeru this file knows: amonet 2.0.1's, or that one with an OpenJade logo already in it. Afterwards the
whole of kaeru is read back from the flash; if it is not exactly what was meant, what was there goes
back at once. A unit whose kaeru is broken does not start at all, so nothing here writes anywhere else,
and nothing writes over a kaeru it does not know.
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
class Large:
    """A bigger picture for a bigger panel, and the size kaeru draws it at."""
    file: Path  # the slot with the picture in it
    sha: str  # of that slot
    code_at: int  # where the instructions that hold the box's size are
    code_amazon: bytes  # those bytes as amonet 2.0.1 ships them
    code: bytes  # and with the bigger size
    kaeru: str  # sha256 of kaeru with both


@dataclass(frozen=True)
class Kaeru:
    offset: int  # where the wordmark slot is
    length: int  # kaeru's length, the part that is checked
    amazon: str  # sha256 of kaeru as amonet 2.0.1 ships it
    openjade: str  # and with the OpenJade slot in it
    large: Large | None = None  # the board's OpenJade logo is this one instead


KAERU = {
    "checkers": Kaeru(335308, 393688,
                      "8107a24d3a667450a22f8c51ff49fe31b92e214d7c2a26857d33da47d94520bf",
                      "4f686dcbf906540cb1b0a33842fc425250fa3a423349528227ecc7ceb3139345"),
    "crown": Kaeru(288716, 481792,
                   "6a717f0f8bd3502df2e2a811f1a90a2ec83e58f151874e11c2920a9a63dc4fa1",
                   "e8d1fe05273ff74c0224ad98240df5319c46fa4459ebeeb2a24b5a365d50aa10",
                   # 477x255: mvn r3,#476 / subs r0,#255 / addw r2,r4,#477 / add.w r5,r1,#255 at 0x4bd0f600
                   Large(LOGO_FILE.with_name("openjade-wordmark-crown.bin"),
                         "a29902b3ab2977c13a7253da62c1b5f13a9355b679e19b4a825c1ff3fc818dc3", 63488,
                         bytes.fromhex("6ff49d73184444080ef0aefaaa380df1180c4108029404f23b12114c01f1aa05"),
                         bytes.fromhex("6ff4ee73184444080ef0aefaff380df1180c4108029404f2dd12114c01f1ff05"),
                         "32aa461d8a5d3bacb3b3d0a374293e7cd6348ece30ac09bde2ba5364693d4e44")),
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
        # With nothing to send, ssh gets no stdin, or it reads what is waiting for the confirmation.
        feed = {"stdin": subprocess.DEVNULL} if data is None else {"input": data}
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *ssh_options(self.identity, self.name),
                                f"root@{self.host}", command],
                               capture_output=True, timeout=120, check=False, **feed)
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


def write_at(client: Client, block: str, offset: int, data: bytes) -> None:
    client.shell(f"busybox dd of={shlex.quote(block)} bs=1 seek={offset} count={len(data)} conv=notrunc 2>/dev/null"
                 " && busybox sync", data=data)


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


def _pinned(path: Path, sha: str) -> bytes:
    data = path.read_bytes()
    if len(data) != SLOT_SIZE or _sha(data) != sha:
        raise BootLogoError(f"{path} is not the logo this tool knows")
    return data


def put_logo(client: Client, board: str, *, saved_expdb: Path, amazon: bool = False,
             confirm: Callable[[str], str], progress: Callable[[str], None] = lambda _: None) -> str:
    """OpenJade's logo into kaeru's wordmark slot, or Amazon's back with amazon=True. Returns what was done."""
    if board not in KAERU:
        raise BootLogoError(f"no boot logo is known for board {board!r}")
    k = KAERU[board]
    # What kaeru can hold, by its hash: for each, the bytes at the places this writes.
    slot, code = k.offset, k.large.code_at if k.large else None
    states = {k.amazon: ("Amazon's", {slot: amazon_slot(saved_expdb, board)}),
              k.openjade: ("the OpenJade", {slot: _pinned(LOGO_FILE, LOGO_SLOT_SHA256)})}
    if k.large:
        for _, places in states.values():
            places[code] = k.large.code_amazon
        states[k.large.kaeru] = ("the bigger OpenJade", {code: k.large.code, slot: _pinned(k.large.file, k.large.sha)})
    want = k.amazon if amazon else k.large.kaeru if k.large else k.openjade
    which, new = states[want]

    block = expdb_block(client)
    now = kaeru_hash(client, block, k.length)
    if now == want:
        return f"{which} boot logo is already there"
    if now not in states:
        raise BootLogoError(f"kaeru in {block} is not amonet 2.0.1's for {board}; nothing written")
    was, old = states[now]
    progress(f"kaeru in {block} is amonet 2.0.1's for {board}, with {was} boot logo")

    phrase = "AMAZON BOOT LOGO" if amazon else "OPENJADE BOOT LOGO"
    if confirm(phrase) != phrase:
        raise BootLogoError("not confirmed; nothing written")

    # Only what differs. Between the writes, kaeru may hold the bigger box with the smaller picture,
    # whose size it checks, and then it skips the logo; never the other way round.
    order = [code, slot] if new.get(code) == getattr(k.large, "code", None) else [slot, code]
    places = [at for at in order if at is not None and new[at] != old[at]]
    for at in places:
        write_at(client, block, at, new[at])
    if kaeru_hash(client, block, k.length) == want:
        return f"{which} boot logo written into {block} and read back from the flash"

    for at in reversed(places):
        write_at(client, block, at, old[at])
    if kaeru_hash(client, block, k.length) == now:
        raise BootLogoError("the logo did not read back right, so what was there went back; kaeru is as before")
    raise BootLogoError(
        f"kaeru in {block} did not read back right, nor after putting the old one back. Do NOT restart the Show.\n"
        f"  Write the saved copy back while it still runs:\n"
        f"    ssh root@<show> 'busybox dd of={block} bs=4096 && busybox sync' < {saved_expdb}\n"
        f"  and check it reads {k.amazon[:16]}... over the first {k.length} bytes.")
