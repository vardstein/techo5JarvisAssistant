import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class JodsThemeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.palette = (ROOT / "echod/internal/lib/palette/palette.go").read_text(encoding="utf-8")
        cls.screen = (ROOT / "echod/internal/config/screen.go").read_text(encoding="utf-8")
        cls.setup = (ROOT / "echod/internal/feature/setup/tabs.go").read_text(encoding="utf-8")
        cls.display = (ROOT / "echod/internal/feature/display/display.go").read_text(encoding="utf-8")
        cls.splash = (ROOT / "echod/internal/feature/display/splash.go").read_text(encoding="utf-8")
        cls.welcome = (ROOT / "echod/internal/feature/display/render_welcome.go").read_text(encoding="utf-8")
        cls.settings = (ROOT / "echod/internal/feature/display/settings.go").read_text(encoding="utf-8")
        cls.acceptance = (ROOT / "docs/final-acceptance.md").read_text(encoding="utf-8")

    def test_fresh_jarvis_defaults_to_white_jade(self):
        self.assertIn('const DefaultTheme = "White Jade"', self.screen)
        self.assertIn('{"White Jade", 0xeeeff2, 0x86909d, 0x20242a, 0x7c8794, 0xd8dade}', self.palette)

    def test_canonical_jods_palette_family_is_available(self):
        for row in (
            '{"White Jade", 0xeeeff2, 0x86909d, 0x20242a, 0x7c8794, 0xd8dade}',
            '{"Leaf Jade", 0xedf6f1, 0x3d9a61, 0x1f3127, 0x698474, 0xd2e1d8}',
            '{"Sakura Jade", 0xf8edf3, 0xd66f99, 0x302128, 0x8b7480, 0xe5d9df}',
            '{"Ember Jade", 0xf7ece7, 0xea9468, 0x34180f, 0x8d5a48, 0xead9d2}',
        ):
            self.assertIn(row, self.palette)

    def test_legacy_techo5_presets_are_not_exposed(self):
        for name in (
            "Walnut", "Slate", "Midnight", "Forest", "Plum", "Ocean", "Ember",
            "Mint", "Lavender", "Graphite", "Cherry", "Paper", "Linen",
        ):
            self.assertNotIn('{"' + name + '",', self.palette)
        self.assertIn('Named("Walnut").Name != "White Jade"', (ROOT / "echod/internal/lib/palette/palette_test.go").read_text(encoding="utf-8"))

    def test_setup_surface_uses_jods_shape_and_layering(self):
        self.assertIn("<title>Jarvis Show setup</title>", self.setup)
        for token in (
            '--font-ui:"Segoe UI","Noto Sans",system-ui,sans-serif',
            '--card-radius:18px',
            '--shadow:0 18px 38px',
            'radial-gradient(circle at top right',
            'backdrop-filter:blur(12px)',
            'border-radius:999px',
            'var(--accent-soft)',
        ):
            self.assertIn(token, self.setup)

    def test_theme_is_applied_before_first_run_welcome_draws(self):
        frame = self.display[self.display.index("func (d *Display) frame() time.Duration") :]
        self.assertLess(frame.index("applyTheme(t)"), frame.index("if !config.Get().Screen.Welcomed"))

    def test_runtime_branding_is_the_product_name_not_techo5(self):
        layout = (ROOT / "echod/internal/layout/device_cronos.go").read_text(encoding="utf-8")
        self.assertIn('System       = "Genbu Show"', layout)
        self.assertIn("strings.ToUpper(layout.System)", self.splash)
        for source in (self.splash, self.welcome, self.settings):
            self.assertNotIn('"TECHO5"', source)
            self.assertNotIn('"JARVIS"', source)
            self.assertNotIn('"Jarvis Show"', source)
        self.assertIn('name = layout.System', self.welcome)
        self.assertIn('st.name = layout.System', self.settings)

    def test_live_display_and_klar_checks_are_post_install_e2e(self):
        self.assertIn(
            "J38/J39 are intentionally deferred to the post-install real-device gate below",
            self.acceptance,
        )
        gate_c = self.acceptance.index("## Gate C — first real device per board")
        self.assertGreater(self.acceptance.index("J38 installed-firmware display E2E"), gate_c)
        self.assertGreater(self.acceptance.index("J39 installed-firmware HA/Klar E2E"), gate_c)
        self.assertGreater(self.acceptance.index("tools/live-acceptance.py verify"), gate_c)
        gate_a = self.acceptance[:gate_c]
        self.assertNotIn("tools/live-acceptance.py verify", gate_a)


if __name__ == "__main__":
    unittest.main()
