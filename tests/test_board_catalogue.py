"""GENBU-163: the installer reads its boards from catalogue/ and still gives exactly what it gave before.

tests/fixtures/board-catalogue-baseline.json froze what boards.py and the pin constants in install.py,
recovery.py, unlock.py and assets.py gave at d448873e, dev's last commit where they owned those facts
(first frozen at 7d601d0; redone when dev was merged in, for dev's cronos review and model name).
The parity tests below feed the catalogue-backed code the same inputs and expect the same answers,
error messages included. The two confirmation phrases left the board profile on the way (oddc
answer 7), so they are compared with the step constants that own them now.

The validator tests copy the real catalogue, break one thing, and expect a refusal.
"""
import argparse
import contextlib
import dataclasses
import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from jarvis_crown import assets, catalogue, device_gate, install, recovery, unlock  # noqa: E402
from jarvis_crown.catalogue import Catalogue, CatalogueError  # noqa: E402

BASELINE = json.loads((ROOT / "tests" / "fixtures" / "board-catalogue-baseline.json").read_text())

# Fields BoardProfile had before the move that are step facts, not board facts, and who owns them now.
MOVED_TO_STEPS = {
    "install_confirmation": install.FINAL_CONFIRM_PHRASE,
    "unlock_confirmation": unlock.CONFIRM_PHRASE,
}


def outcome(fn, value):
    """Same shape as the generator's outcome(), so the two can be compared as data."""
    try:
        return {"ok": dataclasses.asdict(fn(value))}
    except Exception as exc:  # noqa: BLE001 - the type and text are what we compare
        return {"error": type(exc).__name__, "message": str(exc)}


def without_steps(frozen: dict) -> dict:
    """A frozen profile minus the phrases, after checking they match the step constants."""
    rest = dict(frozen)
    for name, owner in MOVED_TO_STEPS.items():
        if rest.pop(name) != owner:
            raise AssertionError(f"{name} {frozen[name]!r} is not the step's {owner!r}")
    return rest


def frozen_outcome(frozen: dict) -> dict:
    return {"ok": without_steps(frozen["ok"])} if "ok" in frozen else frozen


@dataclasses.dataclass
class _Wrap:
    assets: tuple

    def __post_init__(self):
        self.assets = [dataclasses.asdict(a) for a in self.assets]


def board_choices(path: pathlib.Path) -> list[str]:
    """The --board choices a script gives argparse, caught on the way in, as the generator did."""
    seen: list[str] = []
    real_add = argparse.ArgumentParser.add_argument

    def spy(self, *names, **kwargs):
        if "--board" in names:
            seen.extend(kwargs["choices"])
        return real_add(self, *names, **kwargs)

    argparse.ArgumentParser.add_argument = spy
    try:
        spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_") + "_parity", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old_argv = sys.argv
        sys.argv = [path.name, "--help"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                (module.parse_args if hasattr(module, "parse_args") else module.main)()
        except SystemExit:
            pass
        finally:
            sys.argv = old_argv
    finally:
        argparse.ArgumentParser.add_argument = real_add
    return seen


class BaselineParityTest(unittest.TestCase):
    def test_profiles(self):
        frozen = BASELINE["profiles"]
        self.assertEqual(list(catalogue.supported_boards()), list(frozen))
        for name, profile in frozen.items():
            with self.subTest(board=name):
                self.assertEqual(dataclasses.asdict(catalogue.profile_for_board(name)), without_steps(profile))

    def test_profile_fields_are_the_old_ones_minus_the_phrases(self):
        old = set(next(iter(BASELINE["profiles"].values())))
        new = {f.name for f in dataclasses.fields(catalogue.BoardProfile)}
        self.assertEqual(new, old - set(MOVED_TO_STEPS))

    def test_product_to_board(self):
        mapping = {}
        for name in catalogue.supported_boards():
            mapping[catalogue.profile_for_board(name).fastboot_product] = name
        self.assertEqual(mapping, BASELINE["product_to_board"])
        cat = catalogue.default()
        for product, board in BASELINE["product_to_board"].items():
            self.assertEqual(cat.board_for("fastboot_product", product), board)

    def test_lookups_including_case_folding(self):
        # OPEN DECISION (GENBU-163 Q8): today's lookups fold case and strip spaces. Kept as it was.
        for name, frozen in BASELINE["profile_for_board"].items():
            with self.subTest(board=name):
                self.assertEqual(outcome(catalogue.profile_for_board, name), frozen_outcome(frozen))
        for product, frozen in BASELINE["profile_for_product"].items():
            with self.subTest(product=product):
                self.assertEqual(outcome(catalogue.profile_for_product, product), frozen_outcome(frozen))

    def test_device_gate(self):
        self.assertEqual(device_gate.KNOWN_UNSUPPORTED_PRODUCTS, BASELINE["known_unsupported_products"])
        self.assertEqual(device_gate.SUPPORTED_SUMMARY, BASELINE["supported_summary"])
        self.assertEqual(sorted(device_gate.SUPPORTED_PRODUCTS), BASELINE["device_gate_supported_products"])
        for product, message in BASELINE["unsupported_product_message"].items():
            with self.subTest(product=product):
                self.assertEqual(device_gate.unsupported_product_message(product), message)

    def test_install_constants(self):
        frozen = BASELINE["install"]
        self.assertEqual(install.BOOT_SHA256_BY_BOARD, frozen["BOOT_SHA256_BY_BOARD"])
        self.assertEqual(install.CROWN_BOOT_SHA256, frozen["CROWN_BOOT_SHA256"])
        self.assertEqual(install.CHECKERS_BOOT_SHA256, frozen["CHECKERS_BOOT_SHA256"])
        self.assertEqual(install.CRONOS_BOOT_SHA256, frozen["CRONOS_BOOT_SHA256"])
        self.assertEqual(list(install.SUPPORTED_BOARDS), frozen["SUPPORTED_BOARDS"])
        self.assertEqual(install.PROFILE_ID, frozen["PROFILE_ID"])
        self.assertEqual(install.FINAL_CONFIRM_PHRASE, frozen["FINAL_CONFIRM_PHRASE"])

    def test_recovery_constants(self):
        frozen = BASELINE["recovery"]
        self.assertEqual(recovery.TWRP_SHA256, frozen["TWRP_SHA256"])
        self.assertEqual(recovery.TWRP_SHA256_BY_BOARD, frozen["TWRP_SHA256_BY_BOARD"])

    def test_unlock_constants(self):
        frozen = BASELINE["unlock"]
        self.assertEqual(unlock.CONFIRM_PHRASE, frozen["CONFIRM_PHRASE"])
        self.assertEqual(unlock.AMONET_UNLOCK_SHA256, frozen["AMONET_UNLOCK_SHA256"])

    def test_assets(self):
        frozen = BASELINE["assets"]
        self.assertEqual({b: [dataclasses.asdict(a) for a in t] for b, t in assets.ASSETS.items()}, frozen["ASSETS"])
        for name, expected in frozen["assets_for"].items():
            with self.subTest(board=name):
                self.assertEqual(outcome(lambda x: _Wrap(assets.assets_for(x)), name), expected)
        for name, expected in frozen["amonet_bundle_dir"].items():
            self.assertEqual(str(assets.amonet_bundle_dir(pathlib.Path("CACHE"), name)), expected)

    def test_board_choices(self):
        for rel, expected in BASELINE["board_choices"].items():
            with self.subTest(script=rel):
                self.assertEqual(board_choices(ROOT / rel), expected)


class CatalogueFactsTest(unittest.TestCase):
    """What the catalogue says beyond the frozen values, checked against the code that relies on it."""

    @classmethod
    def setUpClass(cls):
        cls.cat = Catalogue()

    def test_one_product_lists_every_board(self):
        product = self.cat.product()
        self.assertEqual(product["id"], install.PROFILE_ID)
        # mkrootfs.sh, techo5lib and echod read the rootfs marker's "product"; it must stay this string.
        self.assertEqual(product["id"], "genbu-show-v1")
        self.assertEqual(product["boards"], list(catalogue.supported_boards()))
        self.assertEqual(sorted(product["boards"]), self.cat.boards())

    def test_twrp_device_is_the_board_name_recovery_checks(self):
        # recovery.verify_twrp_board compares ro.product.device with the board name.
        for name in self.cat.boards():
            self.assertEqual(self.cat.board_for("twrp_device", name), name)

    def test_unlock_pins_only_complete_on_crown(self):
        # unlock.py refuses every board but crown in code; the catalogue agrees that only crown's
        # zip pins every file the unlock needs. Opening another board takes the evidence gate first.
        for name in self.cat.boards():
            members = self.cat.resolve(name)["assets"]["amonet"].get("members", {})
            pinned = {rel: members.get("amonet/" + rel) for rel in unlock.UNLOCK_FILES}
            if name == "crown":
                self.assertEqual(pinned, unlock.AMONET_UNLOCK_SHA256)
            else:
                self.assertIn(None, pinned.values(), name)

    def test_unlock_policy(self):
        for name in self.cat.boards():
            self.assertEqual(self.cat.resolve(name)["policy"], {"install": {"unlock": "amonet-kaeru"}})

    def test_check_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(catalogue.main(["check"]), 0)
        self.assertIn("ok: 1 product, 3 boards", out.getvalue())


class CatalogueRefusesTest(unittest.TestCase):
    def setUp(self):
        self.cat = Catalogue()

    def test_unknown_or_inexact_board(self):
        for name in ("echo-show-8-gen2", "Crown", " crown", "crown ", "", "generic"):
            with self.subTest(name=name), self.assertRaises(CatalogueError):
                self.cat.resolve(name)
            with self.subTest(profile=name), self.assertRaises(CatalogueError):
                self.cat.profile(name)

    def test_unknown_or_inexact_product(self):
        # profile_for_product folds case and strips spaces before it asks (Q8); the catalogue itself does not.
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
        # A lineage zip pins only itself, never its members.
        with self.assertRaises(CatalogueError):
            self.cat.explain("cronos", "assets.lineage.members")


class CatalogueValidatorTest(unittest.TestCase):
    """Copy the real catalogue, break one thing, expect a refusal."""

    def copy(self) -> pathlib.Path:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        root = tmp / "catalogue"
        shutil.copytree(ROOT / "catalogue", root)
        return root

    def broken(self, rel, change):
        root = self.copy()
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
        def unpin(d):
            del d["sha256"], d["size"]
        self.broken("assets/amonet-cronos-v2.0.1.json", unpin)
        self.broken("assets/lineage-18.1-20260904-UNOFFICIAL-cronos.json",
                    lambda d: (d.pop("sha256"), d.pop("size"), d.update(members={"a": "0" * 64})))

    def test_id_must_match_file(self):
        self.broken("boards/crown.json", lambda d: d.update(id="crown2"))

    def test_wordmark(self):
        crown = "assets/amonet-crown-v2.0.1.json"
        self.assertIn("not in", self.broken(crown, lambda d: d["wordmark"]["logos"].update(amazon="0" * 64)))
        self.assertIn("non-empty", self.broken(crown, lambda d: d["wordmark"].update(logos={})))
        self.assertIn("wrong form", self.broken(crown, lambda d: d["wordmark"].update(stock="UNKNOWN")))
        self.assertIn("positive", self.broken(crown, lambda d: d["wordmark"].update(offset=0)))
        self.assertIn("needs logos.openjade",
                      self.broken(crown, lambda d: d["wordmark"]["logos"].pop("openjade")))
        self.assertIn("differ in length",
                      self.broken(crown, lambda d: d["wordmark"]["openjade_large"].update(code="ab")))
        self.assertIn("wrong form",
                      self.broken(crown, lambda d: d["wordmark"]["openjade_large"].update(code="abc")))
        self.assertIn("amonet zips only", self.broken("assets/techo5-boot-v0.7.16.json",
                                                       lambda d: d.update(wordmark={})))

    def test_notes_must_be_text(self):
        self.broken("boards/cronos.json", lambda d: d.update(notes=""))
        self.broken("assets/techo5-boot-v0.7.16.json", lambda d: d.update(notes=["a list"]))

    def test_product_unknown_key(self):
        self.assertIn("unknown", self.broken("products/genbu-show-v1.json", lambda d: d.update(camera={"x": 1})))

    def test_product_missing_block(self):
        self.assertIn("missing", self.broken("products/genbu-show-v1.json", lambda d: d.pop("security")))

    def test_product_empty_block(self):
        self.broken("products/genbu-show-v1.json", lambda d: d.update(music={}))

    def test_product_unknown_board(self):
        self.assertIn("no board", self.broken("products/genbu-show-v1.json", lambda d: d["boards"].append("laguna")))

    def test_product_board_twice(self):
        self.assertIn("twice", self.broken("products/genbu-show-v1.json", lambda d: d["boards"].append("crown")))

    def test_board_no_product_lists(self):
        self.assertIn("no product lists",
                      self.broken("products/genbu-show-v1.json", lambda d: d["boards"].remove("cronos")))

    def test_product_bad_version(self):
        self.broken("products/genbu-show-v1.json", lambda d: d.update(version=0))

    def test_two_products_refuse_to_pick(self):
        root = self.copy()
        shutil.copy(root / "products" / "genbu-show-v1.json", root / "products" / "genbu-show-v2.json")
        data = json.loads((root / "products" / "genbu-show-v2.json").read_text())
        data["id"] = "genbu-show-v2"
        (root / "products" / "genbu-show-v2.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(CatalogueError, "exactly one product"):
            Catalogue(root).product()


class BrokenCatalogueStopsPlainlyTest(unittest.TestCase):
    """GENBU-173: one bad board file stops every command, --help and preflight too, with one plain message
    naming the file, never a traceback. Run on a copy of tools/ and catalogue/, with no device."""

    def test_every_tool_names_the_file(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for part in ("tools", "catalogue"):
            shutil.copytree(ROOT / part, tmp / part, ignore=shutil.ignore_patterns("__pycache__"))
        board = tmp / "catalogue" / "boards" / "cronos.json"
        data = json.loads(board.read_text())
        data["camera"] = True
        board.write_text(json.dumps(data))
        for tool, args in (("jarvis-show.py", ["--help"]), ("jarvis-show.py", ["preflight", "--board", "crown"]),
                           ("jarvis-crown.py", ["--help"]), ("fetch-show-assets.py", ["--help"]),
                           ("install-show.py", ["--help"])):
            with self.subTest(tool=tool, args=args):
                run = subprocess.run([sys.executable, str(tmp / "tools" / tool), *args], cwd=tmp,
                                     capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
                self.assertEqual(run.returncode, 1)
                self.assertNotIn("Traceback", run.stderr)
                self.assertIn("no Show was touched", run.stderr)
                self.assertIn("cronos.json: unknown ['camera']", run.stderr)
                self.assertEqual(run.stdout, "")


class OneWordmarkSlotTest(unittest.TestCase):
    """GENBU-175: boot_logo.py and install-show.py read kaeru's wordmark slot from the same catalogue block,
    so they can't disagree on where it is or what stock kaeru looks like. Each keeps its own logo's hash."""

    def test_both_tools_read_the_catalogue(self):
        from jarvis_crown import boot_logo
        spec = importlib.util.spec_from_file_location("install_show", ROOT / "tools" / "install-show.py")
        show = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(show)
        cat = catalogue.default()
        for board in catalogue.supported_boards():
            w = cat.resolve(board)["assets"]["amonet"]["wordmark"]
            with self.subTest(board=board):
                k = boot_logo.KAERU[board]
                self.assertEqual((k.offset, k.length, k.amazon, k.openjade),
                                 (w["offset"], w["length"], w["stock"], w["logos"]["openjade"]))
                if "techo5" in w["logos"]:
                    self.assertEqual(show.LOGO_KAERU[board], dict(offset=w["offset"], length=w["length"],
                                                                  stock=w["stock"], logo=w["logos"]["techo5"]))
                else:
                    self.assertNotIn(board, show.LOGO_KAERU)
        self.assertEqual(sorted(show.LOGO_KAERU), ["cronos", "crown"])
        self.assertEqual(boot_logo.KAERU["crown"].large.code_at, 63488)
        self.assertIsNone(boot_logo.KAERU["checkers"].large)


class ProductIdSpelledOnceTest(unittest.TestCase):
    """GENBU-174: the product id lives in catalogue/products/. Only the places that can't read the catalogue
    spell it out, each once, and each must agree with it. Renaming the product means changing these too,
    which this test then says."""

    # A rootfs is built on the device in plain sh, echod has it compiled in, and techo5lib.py is copied
    # to the other TECHO5 repos. This list may only get shorter.
    SPELLED = {"tools/linux/mkrootfs.sh": 1, "echod/internal/update/manifest.go": 1, "tools/techo5lib.py": 1}

    def test_spelled_only_where_the_catalogue_cant_be_read(self):
        product = catalogue.default().product()["id"]
        found = {}
        for part in ("tools", "echod", "dashcast", "ha", ".github"):
            for path in sorted((ROOT / part).rglob("*")):
                if not path.is_file() or path.name.endswith("_test.go") or "__pycache__" in path.parts:
                    continue
                try:
                    n = path.read_text(encoding="utf-8").count(product)
                except UnicodeDecodeError:
                    continue
                if n:
                    found[path.relative_to(ROOT).as_posix()] = n
        self.assertEqual(found, self.SPELLED)

    def test_those_places_agree_with_the_catalogue(self):
        import re
        product = catalogue.default().product()["id"]
        sys.path.insert(0, str(ROOT / "tools"))
        import techo5lib
        self.assertEqual(techo5lib.JARVIS_SHOW_PRODUCT, product)
        go = (ROOT / "echod/internal/update/manifest.go").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'const JarvisShowProduct = "([^"]+)"', go).group(1), product)
        sh = (ROOT / "tools/linux/mkrootfs.sh").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'"product": "([^"]+)"', sh).group(1), product)


if __name__ == "__main__":
    unittest.main()
