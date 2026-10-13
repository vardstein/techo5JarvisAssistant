"""GENBU-163 slice a: the board catalogue gives exactly what boards.py and profiles/*.json give today.

Every field of today's owners is either compared with the catalogue here or listed with the reason it is
not a board fact. A new field in boards.py or a profile fails this test until someone decides where it
belongs. The catalogue under test is the real one; the validator tests copy it and break one thing.
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from dataclasses import fields

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import assets as code_assets  # noqa: E402
from jarvis_crown import boards, install, recovery, unlock  # noqa: E402
from jarvis_crown.catalogue import Catalogue, CatalogueError  # noqa: E402

PROFILES_DIR = ROOT / "profiles"
PRODUCT = json.loads((PROFILES_DIR / "genbu-show-v1.json").read_text())
TWRP_MEMBER = "amonet/bin/twrp.img"  # the path recovery._pinned_twrp opens inside the amonet folder

# BoardProfile fields that are not board facts. Both phrases are the same on every board, so they
# move into the generic step code (oddc answer 7); test_confirmations_are_not_board_facts proves it.
NOT_BOARD_FACTS = {"unlock_confirmation", "install_confirmation"}

# profiles/<board>-v1.json keys that do not come from the board entity, and why.
PROFILE_KEYS_ELSEWHERE = {
    "platform": "the product entity (profiles/genbu-show-v1.json product)",
    "install_confirmation": "generic step code",
    "unlock_confirmation": "generic step code",
    "music": "copy of the product entity's music block",
    "destructive_assets": "prose plus copies of the asset pins; the pins are compared below",
}


class CatalogueParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = Catalogue()
        cls.resolved = {b: cls.cat.resolve(b) for b in cls.cat.boards()}

    def test_same_board_set_everywhere(self):
        expected = set(boards.PROFILES)
        self.assertEqual(set(self.cat.boards()), expected)
        self.assertEqual(set(install.SUPPORTED_BOARDS), expected)
        self.assertEqual(set(PRODUCT["boards"]), expected)
        self.assertEqual(set(code_assets.ASSETS), expected)

    def test_board_profile_fields(self):
        names = {f.name for f in fields(boards.BoardProfile)}
        compared = {"board", "fastboot_product", "model", "product_id", "display_width", "display_height",
                    "amonet_dir_name", "amonet_kaeru_name"}
        self.assertEqual(names - NOT_BOARD_FACTS, compared, "BoardProfile grew a field: decide its owner")
        for name, profile in boards.PROFILES.items():
            r = self.resolved[name]
            with self.subTest(board=name):
                self.assertEqual(profile.board, r["id"])
                self.assertEqual(profile.fastboot_product, r["identity"]["fastboot_product"])
                self.assertEqual(profile.model, r["model"])
                self.assertEqual(profile.product_id, r["profile_id"])
                self.assertEqual(profile.display_width, r["display"]["width"])
                self.assertEqual(profile.display_height, r["display"]["height"])
                self.assertEqual(profile.amonet_dir_name, r["assets"]["amonet"]["file"].removesuffix(".zip"))
                self.assertEqual(profile.amonet_kaeru_name, pathlib.PurePosixPath(r["assets"]["amonet"]["kaeru"]).name)

    def test_confirmations_are_not_board_facts(self):
        self.assertEqual({p.unlock_confirmation for p in boards.PROFILES.values()}, {unlock.CONFIRM_PHRASE})
        self.assertEqual({p.install_confirmation for p in boards.PROFILES.values()}, {"ERASE"})

    def test_product_lookup_matches(self):
        for product, board in boards.PRODUCT_TO_BOARD.items():
            self.assertEqual(self.cat.board_for("fastboot_product", product), board)
            self.assertEqual(boards.profile_for_product(product).board, board)

    def test_twrp_device_is_the_board_name_recovery_checks(self):
        # recovery.verify_twrp_board compares ro.product.device with the board name.
        for name in boards.PROFILES:
            self.assertEqual(self.cat.board_for("twrp_device", name), name)

    def test_board_profiles_json(self):
        files = sorted(p.name for p in PROFILES_DIR.glob("*-v1.json") if p.name != "genbu-show-v1.json")
        # cronos has no profile file today; boards.py alone carries it.
        self.assertEqual(files, ["checkers-v1.json", "crown-v1.json"])
        for file in files:
            data = json.loads((PROFILES_DIR / file).read_text())
            r = self.resolved[data["board"]]
            with self.subTest(file=file):
                compared = {"product", "board", "fastboot_product", "model", "display"}
                self.assertEqual(set(data) - compared - set(PROFILE_KEYS_ELSEWHERE), set(),
                                 "profile grew a key: decide its owner")
                self.assertEqual(data["product"], r["profile_id"])
                self.assertEqual(data["fastboot_product"], r["identity"]["fastboot_product"])
                self.assertEqual(data["model"], r["model"])
                self.assertEqual(data["display"], r["display"])
                self.assertEqual(data["platform"], PRODUCT["product"])
                self.assertEqual(data["install_confirmation"], "ERASE")
                self.assertEqual(data["unlock_confirmation"], unlock.CONFIRM_PHRASE)
                if "music" in data:
                    self.assertEqual(data["music"], PRODUCT["music"])
                if "destructive_assets" in data:
                    da = data["destructive_assets"]
                    self.assertEqual(set(da), {"status", "boot", "twrp", "amonet"})
                    self.assertEqual(da["boot"]["sha256"], r["assets"]["boot"]["sha256"])
                    self.assertIn(r["assets"]["boot"]["file"], da["boot"]["source"])
                    self.assertEqual(da["twrp"]["sha256"], r["assets"]["amonet"]["members"][TWRP_MEMBER])
                    self.assertIn(r["assets"]["amonet"]["file"], da["twrp"]["source"])

    def test_download_assets(self):
        for name, pinned in code_assets.ASSETS.items():
            r = self.resolved[name]["assets"]
            self.assertEqual(sorted(a.kind for a in pinned), sorted(r))
            for a in pinned:
                c = r[a.kind]
                with self.subTest(board=name, kind=a.kind):
                    self.assertEqual(a.name, c["file"])
                    self.assertEqual(a.url, c["url"])
                    self.assertEqual(a.source, c["source"])
                    self.assertEqual(a.manual, c["manual"])
                    # Today "" and 0 mean "not pinned"; the catalogue leaves the pin out.
                    self.assertEqual(a.sha256, c.get("sha256", ""))
                    self.assertEqual(a.size, c.get("size", 0))

    def test_boot_pins(self):
        for name, sha in install.BOOT_SHA256_BY_BOARD.items():
            self.assertEqual(sha, self.resolved[name]["assets"]["boot"].get("sha256"))

    def test_twrp_pins(self):
        self.assertEqual(recovery.TWRP_SHA256_BY_BOARD["crown"], recovery.TWRP_SHA256)
        for name, sha in recovery.TWRP_SHA256_BY_BOARD.items():
            members = self.resolved[name]["assets"]["amonet"].get("members", {})
            self.assertEqual(sha, members.get(TWRP_MEMBER), name)

    def test_unlock_pins(self):
        # unlock.py verifies these files on crown and refuses every other board unless someone passes
        # a hash manifest. The catalogue says the same thing: only crown's zip pins all of them.
        for name in boards.PROFILES:
            members = self.resolved[name]["assets"]["amonet"].get("members", {})
            pinned = {rel: members.get("amonet/" + rel) for rel in unlock.AMONET_UNLOCK_SHA256}
            if name == "crown":
                self.assertEqual(pinned, unlock.AMONET_UNLOCK_SHA256)
            else:
                self.assertIn(None, pinned.values(), name)

    def test_unlock_policy(self):
        for name in boards.PROFILES:
            self.assertEqual(self.resolved[name]["policy"], {"install": {"unlock": "amonet-kaeru"}})


class CatalogueRefusesTest(unittest.TestCase):
    def setUp(self):
        self.cat = Catalogue()

    def test_unknown_or_inexact_board(self):
        for name in ("echo-show-8-gen2", "Crown", " crown", "crown ", "", "generic"):
            with self.subTest(name=name), self.assertRaises(CatalogueError):
                self.cat.resolve(name)

    def test_unknown_or_inexact_product(self):
        # boards.profile_for_product folds case and strips spaces; the catalogue does not.
        for product in ("crown", "CROWN ", "CHECKERS2", "", "LAGUNA"):
            with self.subTest(product=product), self.assertRaises(CatalogueError):
                self.cat.board_for("fastboot_product", product)

    def test_no_guessing_identity_keys(self):
        with self.assertRaises(CatalogueError):
            self.cat.board_for("dt_compatible", "amazon,crown")

    def test_explain_names_the_owner(self):
        value, owner = self.cat.explain("crown", "assets.boot.sha256")
        self.assertEqual(value, install.CROWN_BOOT_SHA256)
        self.assertEqual(owner, "catalogue/assets/techo5-boot-crown-v1.0.1.json#/sha256")
        value, owner = self.cat.explain("checkers", "display.height")
        self.assertEqual((value, owner), (480, "catalogue/boards/checkers.json#/display/height"))
        with self.assertRaises(CatalogueError):
            self.cat.explain("cronos", "assets.amonet.sha256")


class CatalogueValidatorTest(unittest.TestCase):
    """Copy the real catalogue, break one thing, expect a refusal."""

    def broken(self, rel, change):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        root = tmp / "catalogue"
        shutil.copytree(ROOT / "catalogue", root)
        path = root / rel
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))
        with self.assertRaises(CatalogueError) as ctx:
            Catalogue(root)
        return str(ctx.exception)

    def test_unknown_key(self):
        self.assertIn("unknown", self.broken("boards/crown.json", lambda d: d.update(camera=True)))

    def test_missing_key(self):
        self.assertIn("missing", self.broken("boards/crown.json", lambda d: d.pop("display")))

    def test_unlock_outside_closed_set(self):
        self.broken("boards/crown.json", lambda d: d["policy"]["install"].update(unlock="none"))

    def test_identity_placeholder_key(self):
        self.broken("boards/crown.json", lambda d: d["identity"].update(dt_compatible="unknown"))

    def test_shared_identity(self):
        self.assertIn("names both",
                      self.broken("boards/cronos.json", lambda d: d["identity"].update(fastboot_product="CROWN")))

    def test_wrong_asset_kind(self):
        self.broken("boards/crown.json", lambda d: d["assets"].update(boot="lineage-18.1-20260904-UNOFFICIAL-crown"))

    def test_sha_without_size(self):
        self.broken("assets/techo5-boot-v0.7.16.json", lambda d: d.pop("size"))

    def test_bad_sha(self):
        self.broken("assets/techo5-boot-v0.7.16.json", lambda d: d.update(sha256="UNKNOWN"))

    def test_member_pins_need_zip_pin(self):
        self.broken("assets/amonet-cronos-v2.0.1.json",
                    lambda d: d.update(members={TWRP_MEMBER: "0" * 64}))

    def test_id_must_match_file(self):
        self.broken("boards/crown.json", lambda d: d.update(id="crown2"))


if __name__ == "__main__":
    unittest.main()
