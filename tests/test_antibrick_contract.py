import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown.catalogue import profile_for_board, supported_boards  # noqa: E402
from jarvis_crown.device_gate import KNOWN_UNSUPPORTED_PRODUCTS  # noqa: E402
from jarvis_crown.install import BOOT_SHA256_BY_BOARD, make_install_plan  # noqa: E402,F401
from jarvis_crown.recovery import TWRP_SHA256_BY_BOARD  # noqa: E402
from jarvis_crown.unlock import AMONET_UNLOCK_SHA256  # noqa: E402


class AntiBrickContractTests(unittest.TestCase):
    def test_only_crown_checkers_and_cronos_are_product_targets(self):
        self.assertEqual(set(supported_boards()), {"crown", "checkers", "cronos"})
        self.assertEqual(profile_for_board("crown").fastboot_product, "CROWN")
        self.assertEqual(profile_for_board("checkers").fastboot_product, "CHECKERS")
        self.assertEqual(profile_for_board("cronos").fastboot_product, "CRONOS")
        self.assertNotIn("CRONOS", KNOWN_UNSUPPORTED_PRODUCTS)

    def test_cronos_boot_is_pinned_but_twrp_and_amonet_are_not(self):
        from jarvis_crown.assets import assets_for
        # Upstream TECHO5 v0.7.16 techo5-boot-v0.7.16.img, the last release with a cronos boot image.
        self.assertEqual(
            BOOT_SHA256_BY_BOARD["cronos"],
            "7e9ce93a54fc613cbfab5d15929aa06bfd6f63c2c432d6f8261b3599fc3cd791",
        )
        self.assertIsNone(TWRP_SHA256_BY_BOARD["cronos"])
        amonet = [a for a in assets_for("cronos") if a.kind == "amonet"]
        self.assertEqual(len(amonet), 1)
        self.assertEqual(amonet[0].sha256, "")
        self.assertTrue(amonet[0].manual)

    def test_checkers_boot_and_twrp_are_pinned_but_unlock_stays_blocked(self):
        self.assertEqual(
            BOOT_SHA256_BY_BOARD["checkers"],
            "6fd696dce2592d2237a432e23ed7c032f1643bd1fe475d3094eccd9f7d2079b2",
        )
        self.assertEqual(
            TWRP_SHA256_BY_BOARD["checkers"],
            "dcc75bb651894c343039d45af73c4d900594762f6f55187c1ebdaf12d0ae59f5",
        )
        unlock_source = (ROOT / "tools/jarvis_crown/unlock.py").read_text(encoding="utf-8")
        self.assertIn("Amonet bundle has not been cryptographically pinned", unlock_source)

    def test_crown_destructive_assets_are_cryptographically_pinned(self):
        self.assertEqual(len(AMONET_UNLOCK_SHA256), 6)
        for digest in AMONET_UNLOCK_SHA256.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        # Upstream TECHO5 v1.0.1 techo5-boot-crown-v1.0.1.img, with the quick-tap unmute fix.
        self.assertEqual(
            BOOT_SHA256_BY_BOARD["crown"],
            "e00bed964d9b2ce31df152d183d2aeb45a7839cc5f7460a93dd97d70715ad1b7",
        )
        self.assertRegex(TWRP_SHA256_BY_BOARD["crown"] or "", r"^[0-9a-f]{64}$")

    def test_jarvis_hidden_prestaged_mode_cannot_enable_expdb_logo_patch(self):
        source = (ROOT / "tools/install-show.py").read_text(encoding="utf-8")
        self.assertIn("if a.jarvis_show_prestaged and not a.amazon_logo:", source)
        self.assertIn("Jarvis Show never modifies expdb/kaeru", source)
        install_source = (ROOT / "tools/jarvis_crown/install.py").read_text(encoding="utf-8")
        self.assertIn('"--amazon-logo"', install_source)

    def test_jarvis_owned_recovery_never_uses_fastboot_boot(self):
        owned = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in (
                "tools/jarvis_crown/recovery.py",
                "tools/jarvis_crown/flow.py",
                "tools/jarvis_crown/install.py",
                "tools/jarvis-show.py",
            )
        )
        self.assertNotIn("fastboot boot", owned)
        self.assertNotIn('"boot", str(image)', owned)


    def test_twrp_gate_refuses_legacy_amonet1_boot_layout(self):
        source = (ROOT / "tools/jarvis_crown/recovery.py").read_text(encoding="utf-8")
        self.assertIn("grep -qa microloader", source)
        self.assertIn("legacy Amonet 1.x boot microloader detected", source)
        self.assertIn("cannot prove a modern/plain boot layout", source)

    def test_twrp_handoff_write_targets_are_only_recovery_and_swdl(self):
        source = (ROOT / "tools/jarvis_crown/recovery.py").read_text(encoding="utf-8")
        self.assertIn('"flash", "recovery", str(image)', source)
        self.assertIn('"flash", "swdl", str(image)', source)
        for protected in ("lk", "preloader", "tee1", "tee2", "expdb", "persist", "metadata"):
            self.assertNotIn(f'"flash", "{protected}"', source)

    def test_normal_product_boot_write_is_boot_partition_only(self):
        source = (ROOT / "tools/install-show.py").read_text(encoding="utf-8")
        self.assertIn("fastboot.run('flash', 'boot', boot)", source)
        for protected in ("lk", "preloader", "tee1", "tee2", "persist", "metadata"):
            self.assertNotIn(f"fastboot.run('flash', '{protected}'", source)

    def test_slot_store_is_exactly_system_p12_and_requires_erase_flag(self):
        source = (ROOT / "tools/install-show.py").read_text(encoding="utf-8")
        self.assertIn("slotctl mkstore /dev/mmcblk0p12 --i-know-this-erases-it", source)
        slotctl = (ROOT / "tools/linux/slotctl").read_text(encoding="utf-8")
        self.assertIn('"$t" != "$(booted)"', slotctl)
        self.assertIn('set_state $t "trial $TRIES"', slotctl)
        self.assertIn('set_state $a bad', slotctl)
        self.assertIn('falling back to $o', slotctl)

    def test_ordinary_ota_cannot_write_boot_or_raw_partitions(self):
        source = (ROOT / "echod/internal/update/slots_linux.go").read_text(encoding="utf-8")
        self.assertIn('exec.CommandContext(ctx, slotctl, "install", to)', source)
        for forbidden in ("fastboot", "mmcblk0", "dd if=", "dd of=", "expdb", "preloader", "tee1", "tee2"):
            self.assertNotIn(forbidden, source)

    def test_upstream_slot_store_refuses_reformat_of_existing_store(self):
        source = (ROOT / "tools/install-show.py").read_text(encoding="utf-8")
        self.assertIn("this unit already has a slot store", source)
        self.assertIn("Erasing it would take this unit", source)
        self.assertIn("vendor tree with it", source)


if __name__ == "__main__":
    unittest.main()
