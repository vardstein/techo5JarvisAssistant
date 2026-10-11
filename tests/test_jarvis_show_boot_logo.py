import hashlib
import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import boot_logo as bl  # noqa: E402

OFFSET, LENGTH = 1000, 9000
AMAZON = os.urandom(bl.SLOT_SIZE)  # stands in for Amazon's picture, which is not in the repo
LOGO = bl.LOGO_FILE.read_bytes()


def sha(b):
    return hashlib.sha256(b).hexdigest()


CROWN = bl.KAERU["crown"].large
CODE_AT = 200
BIG = CROWN.file.read_bytes()


def kaeru(slot, code=CROWN.code_amazon):
    base = bytearray(hashlib.sha256(b"kaeru").digest() * (LENGTH // 32 + 1))[:LENGTH]
    base[CODE_AT:CODE_AT + len(code)] = code
    base[OFFSET:OFFSET + bl.SLOT_SIZE] = slot
    return bytes(base)


class FakeShow:
    """expdb as bytes, answering the commands boot_logo sends over SSH."""

    def __init__(self, slot):
        self.expdb = bytearray(kaeru(slot) + bytes(4096))
        self.writes = []

    def shell(self, command, data=None):
        if "PARTNAME=expdb" in command:
            return "/sys/class/block/mmcblk0p7/uevent\n"
        if "drop_caches" in command:
            return ""
        m = re.fullmatch(r"busybox head -c (\d+) /dev/mmcblk0p7 \| busybox sha256sum", command)
        if m:
            return sha(bytes(self.expdb[:int(m[1])])) + "  -\n"
        m = re.fullmatch(r"busybox dd of=/dev/mmcblk0p7 bs=1 seek=(\d+) count=(\d+) conv=notrunc 2>/dev/null && busybox sync",
                         command)
        if m:
            at, n = int(m[1]), int(m[2])
            self.writes.append(at)
            self.expdb[at:at + n] = data[:n]
            return ""
        raise AssertionError(f"unexpected command {command!r}")


class BootLogoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.saved = pathlib.Path(tmp.name) / "p7-expdb.img"
        self.saved.write_bytes(kaeru(AMAZON))
        board = bl.Kaeru(OFFSET, LENGTH, sha(kaeru(AMAZON)), sha(kaeru(LOGO)))
        for patch in (mock.patch.dict(bl.KAERU, {"test": board}),
                      mock.patch.object(bl, "AMAZON_SLOT_SHA256", sha(AMAZON))):
            patch.start()
            self.addCleanup(patch.stop)

    def put(self, show, **kw):
        kw.setdefault("confirm", lambda phrase: phrase)
        return bl.put_logo(show, "test", saved_expdb=self.saved, **kw)

    def test_the_logo_file_is_the_pinned_one(self):
        self.assertEqual(len(LOGO), bl.SLOT_SIZE)
        self.assertEqual(sha(LOGO), bl.LOGO_SLOT_SHA256)

    def test_the_logo_goes_into_the_slot_and_nowhere_else(self):
        show = FakeShow(AMAZON)
        self.assertIn("written", self.put(show))
        self.assertEqual(bytes(show.expdb[:LENGTH]), kaeru(LOGO))
        self.assertEqual(show.writes, [OFFSET])

    def test_amazons_logo_goes_back(self):
        show = FakeShow(LOGO)
        self.put(show, amazon=True)
        self.assertEqual(bytes(show.expdb[:LENGTH]), kaeru(AMAZON))

    def test_nothing_is_written_twice(self):
        show = FakeShow(LOGO)
        self.assertIn("already", self.put(show))
        self.assertEqual(show.writes, [])

    def test_an_unknown_kaeru_is_left_alone(self):
        show = FakeShow(os.urandom(bl.SLOT_SIZE))
        with self.assertRaisesRegex(bl.BootLogoError, "nothing written"):
            self.put(show)
        self.assertEqual(show.writes, [])

    def test_without_the_phrase_nothing_is_written(self):
        show = FakeShow(AMAZON)
        with self.assertRaisesRegex(bl.BootLogoError, "not confirmed"):
            self.put(show, confirm=lambda phrase: "")
        self.assertEqual(show.writes, [])

    def test_a_bad_read_back_puts_the_old_slot_back(self):
        show = FakeShow(AMAZON)
        orig = show.shell

        def corrupting(command, data=None):
            if "dd of=" in command and not show.writes:
                data = bytes(len(data))  # the first write lands as zeros
            return orig(command, data)
        show.shell = corrupting
        with self.assertRaisesRegex(bl.BootLogoError, "went back"):
            self.put(show)
        self.assertEqual(bytes(show.expdb[:LENGTH]), kaeru(AMAZON))
        self.assertEqual(show.writes, [OFFSET, OFFSET])

    def test_a_saved_expdb_without_amazons_logo_stops_it(self):
        self.saved.write_bytes(kaeru(LOGO))
        with self.assertRaisesRegex(bl.BootLogoError, "does not hold Amazon"):
            self.put(FakeShow(AMAZON))

    def test_the_real_boards_are_pinned(self):
        self.assertEqual(bl.KAERU["checkers"].offset, 335308)
        self.assertEqual(bl.KAERU["crown"].offset, 288716)
        for k in (bl.KAERU["checkers"], bl.KAERU["crown"]):
            self.assertRegex(k.amazon, r"^[0-9a-f]{64}$")
            self.assertRegex(k.openjade, r"^[0-9a-f]{64}$")
            self.assertGreater(k.length, k.offset + bl.SLOT_SIZE)


class LargeLogoTests(BootLogoTests):
    """A board with a bigger logo: the slot and the box's size, in an order that is safe to stop in."""

    def setUp(self):
        super().setUp()
        large = bl.Large(CROWN.file, CROWN.sha, CODE_AT, CROWN.code_amazon, CROWN.code, sha(self.big()))
        board = bl.Kaeru(OFFSET, LENGTH, sha(kaeru(AMAZON)), sha(kaeru(LOGO)), large)
        patch = mock.patch.dict(bl.KAERU, {"test": board})
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def big():
        return kaeru(BIG, CROWN.code)

    # The cases above, with the bigger logo as the one that is wanted.
    def test_the_logo_goes_into_the_slot_and_nowhere_else(self):
        show = FakeShow(AMAZON)
        self.assertIn("bigger OpenJade boot logo written", self.put(show))
        self.assertEqual(bytes(show.expdb[:LENGTH]), self.big())
        self.assertEqual(show.writes, [CODE_AT, OFFSET])

    def test_amazons_logo_goes_back(self):
        show = FakeShow(AMAZON)
        show.expdb[:LENGTH] = self.big()
        self.put(show, amazon=True)
        self.assertEqual(bytes(show.expdb[:LENGTH]), kaeru(AMAZON))
        self.assertEqual(show.writes, [OFFSET, CODE_AT])  # the picture first, so the box is never too small

    def test_nothing_is_written_twice(self):
        show = FakeShow(AMAZON)
        show.expdb[:LENGTH] = self.big()
        self.assertIn("already", self.put(show))
        self.assertEqual(show.writes, [])

    def test_the_smaller_openjade_logo_becomes_the_bigger_one(self):
        show = FakeShow(LOGO)
        self.put(show)
        self.assertEqual(bytes(show.expdb[:LENGTH]), self.big())
        self.assertEqual(show.writes, [CODE_AT, OFFSET])

    def test_amazons_logo_goes_back_from_the_smaller_one_too(self):
        show = FakeShow(LOGO)
        self.put(show, amazon=True)
        self.assertEqual(bytes(show.expdb[:LENGTH]), kaeru(AMAZON))
        self.assertEqual(show.writes, [OFFSET])

    def test_a_bad_read_back_puts_the_old_slot_back(self):
        show = FakeShow(AMAZON)
        orig = show.shell

        def corrupting(command, data=None):
            if "dd of=" in command and len(show.writes) == 1:
                data = bytes(len(data))  # the picture lands as zeros
            return orig(command, data)
        show.shell = corrupting
        with self.assertRaisesRegex(bl.BootLogoError, "went back"):
            self.put(show)
        self.assertEqual(bytes(show.expdb[:LENGTH]), kaeru(AMAZON))
        self.assertEqual(show.writes, [CODE_AT, OFFSET, OFFSET, CODE_AT])

    def test_the_bigger_logo_file_is_the_pinned_one(self):
        self.assertEqual(len(BIG), bl.SLOT_SIZE)
        self.assertEqual(sha(BIG), CROWN.sha)

    def test_only_the_four_sizes_change_in_the_code(self):
        self.assertEqual(len(CROWN.code), len(CROWN.code_amazon))
        changed = [i for i, (a, b) in enumerate(zip(CROWN.code_amazon, CROWN.code)) if a != b]
        self.assertEqual([CROWN.code_at + i for i in changed], [63490, 63500, 63512, 63518])


if __name__ == "__main__":
    unittest.main()
