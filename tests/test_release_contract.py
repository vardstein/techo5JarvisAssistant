import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_official_release_namespace_is_consistent(self):
        expected = "vardstein/techo5JarvisAssistant"
        for rel in (
            "tools/release-jarvis-show.sh",
            "tools/install-show.py",
            "tools/techo5lib.py",
            "tools/linux/deploy-rootfs.sh",
        ):
            self.assertIn(expected, (ROOT / rel).read_text(encoding="utf-8"), rel)
        self.assertIn(
            "https://github.com/vardstein/techo5JarvisAssistant/releases",
            (ROOT / "echod/internal/update/releases_cronos.go").read_text(encoding="utf-8"),
        )

    def test_rootfs_builder_emits_shared_product_marker(self):
        source = (ROOT / "tools/linux/mkrootfs.sh").read_text(encoding="utf-8")
        self.assertIn('jarvis-show-release.json', source)
        self.assertIn('"product": "jarvis-show-v1"', source)
        self.assertIn('"boards": ["crown", "checkers"]', source)
        self.assertIn('"version": "$VERSION"', source)

    def test_release_workflow_uses_secret_and_attests_payloads(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("secrets.JARVIS_SHOW_SIGN_KEY", source)
        self.assertIn("actions/attest-build-provenance", source)
        self.assertIn("jarvis-show-rootfs-${{ steps.meta.outputs.version }}.tar.gz", source)
        self.assertIn("tools/release-jarvis-show.sh", source)

    def test_channels_are_moved_by_the_checking_mover(self):
        # Parallel pushes once left a channel holding one release's manifest and another's signature;
        # release_channels.py works the target out from the release list and reads the channel back.
        source = (ROOT / "tools/release-jarvis-show.sh").read_text(encoding="utf-8")
        self.assertIn('tools/release_channels.py" "${channel_args[@]}" "${rolling[@]}"', source)
        self.assertIn('--version "$VERSION"', source)
        self.assertNotIn("manifest.json.sig\" --repo \"$REPO\" --clobber", source)

    def test_release_workflow_direct_shell_entrypoints_are_executable(self):
        for rel in (
            "tools/security-regression.sh",
            "tools/linux/deploy-rootfs.sh",
            "tools/release-jarvis-show.sh",
        ):
            mode = (ROOT / rel).stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, f"{rel} is invoked directly but is not executable")

    def test_manual_ci_build_produces_unsigned_rc_rootfs_without_publishing(self):
        source = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("if: github.event_name == 'workflow_dispatch'", source)
        self.assertIn("python3 tools/fetch-inputs.py --device show", source)
        self.assertIn('tools/linux/deploy-rootfs.sh --out "bin/jarvis-show-rootfs-$VERSION.tar.gz"', source)
        self.assertIn("jarvis-show-rootfs-${{ steps.meta.outputs.version }}", source)
        self.assertNotIn("release-jarvis-show.sh", source)
        self.assertNotIn("JARVIS_SHOW_SIGN_KEY", source)

    def test_inherited_generic_publisher_is_disabled(self):
        source = (ROOT / "tools/release.ps1").read_text(encoding="utf-8")
        self.assertIn("inherited TECHO5 publisher is disabled in Jarvis Show", source)

    def test_release_tool_does_not_build_rootfs(self):
        source = (ROOT / "tools/release-jarvis-show.sh").read_text(encoding="utf-8")
        self.assertIn("This script NEVER builds a rootfs or boot image", source)
        self.assertNotIn("deploy-rootfs.sh", source)
        self.assertIn("mkmanifest", source)


if __name__ == "__main__":
    unittest.main()

class SupplyChainReleaseContractTests(unittest.TestCase):
    def test_release_manifest_is_bound_to_product_and_both_first_gen_boards(self):
        source = (ROOT / "tools/release-jarvis-show.sh").read_text(encoding="utf-8")
        self.assertIn('-product "jarvis-show-v1"', source)
        self.assertIn('-board crown', source)
        self.assertIn('-board checkers', source)
        self.assertIn('ident.version != sys.argv[3]', source)
        self.assertIn("signing key permissions are too open", source)
        self.assertIn("signing key must not be a symlink", source)

    def test_rootfs_build_never_allows_untrusted_apks(self):
        source = (ROOT / "tools/linux/mkrootfs.sh").read_text(encoding="utf-8")
        self.assertNotIn("--allow-untrusted", source)
        self.assertIn("refusing an untrusted release build", source)

    def test_bootstrap_inputs_are_authenticated_before_extraction(self):
        source = (ROOT / "tools/fetch-inputs.py").read_text(encoding="utf-8")
        self.assertIn("apk_signature_mismatch", source)
        self.assertIn("_alpine_keys_archive = alpine(out)", source)
        self.assertIn("openssl", source)
        self.assertIn("instead of substituting bytes", source)

    def test_go_actions_are_commit_pinned(self):
        source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        uses = [line.strip() for line in source.splitlines() if line.strip().startswith("uses:")]
        self.assertTrue(uses)
        for line in uses:
            ref = line.rsplit("@", 1)[-1].split()[0]
            self.assertRegex(ref, r"^[0-9a-f]{40}$", line)

    def test_python_and_go_installers_embed_same_release_public_key(self):
        import re
        python = (ROOT / "tools/techo5lib.py").read_text(encoding="utf-8")
        go = (ROOT / "echod/internal/update/trust.go").read_text(encoding="utf-8")
        p = re.search(r"RELEASE_KEY = '([^']+)'", python)
        g = re.search(r'releaseKey = "([^"]+)"', go)
        self.assertIsNotNone(p)
        self.assertIsNotNone(g)
        self.assertEqual(p.group(1), g.group(1))

    def test_host_installer_binds_signed_release_to_product_and_detected_board(self):
        lib = (ROOT / "tools/techo5lib.py").read_text(encoding="utf-8")
        installer = (ROOT / "tools/install-show.py").read_text(encoding="utf-8")
        self.assertIn("expected_product=None, expected_board=None", lib)
        self.assertIn("signed but belongs to product", lib)
        self.assertIn("signed but does not support board", lib)
        self.assertIn("expected_product='jarvis-show-v1', expected_board=dev", installer)

    def test_release_script_rejects_world_readable_signing_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            binary = tmp / "echod-arm"
            rootfs = tmp / "rootfs.tar.gz"
            key = tmp / "sign.seed"
            binary.write_bytes(b"binary")
            rootfs.write_bytes(b"not reached")
            key.write_text("not reached\n", encoding="utf-8")
            os.chmod(key, 0o644)
            result = subprocess.run(
                [
                    str(ROOT / "tools/release-jarvis-show.sh"),
                    "--version", "v1.2.3",
                    "--notes", "test",
                    "--binary", str(binary),
                    "--rootfs", str(rootfs),
                    "--sign-key", str(key),
                    "--out", str(tmp / "out"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("signing key permissions are too open", result.stderr)
