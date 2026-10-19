"""Factory reset: the screen leaves a mark, and the boot script erases before anything runs.

t5_factory_reset is run here for real, in a temporary directory standing in for userdata."""

from pathlib import Path
import base64
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "tools/linux/techo5-lib.sh"


def reset(root: Path) -> subprocess.CompletedProcess:
    script = 'log() { echo "$*"; }; . "$1"; t5_factory_reset "$2"'
    return subprocess.run(["sh", "-c", script, "sh", str(LIB), str(root)],
                          capture_output=True, text=True, check=False)


class FactoryResetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "data/misc/techo5"
        self.logs = self.root / "data/techo5-linux"
        for rel, text in {
            "data/misc/techo5/profile": "genbu-checkers-v1\n",
            "data/misc/techo5/board.json": '{"id":"checkers"}\n',
            "data/misc/techo5/psk": "b2xkIGtleQ==\n",
            "data/misc/techo5/hass.json": "{}",
            "data/misc/techo5/state.json": "{}",
            "data/misc/techo5/name": "Kitchen\n",
            "data/misc/techo5/root_pw": "$6$salt$hash\n",
            "data/misc/techo5/usb_debug": "on\n",
            "data/misc/techo5/ssh/authorized_keys": "ssh-ed25519 AAAA test\n",
            "data/misc/techo5/bluetooth/pairings": "x",
            "data/misc/techo5/models/okay_nabu.tflite": "x",
            "data/misc/techo5/.hidden": "x",
            "data/techo5-linux/wpa_supplicant.conf": "network={}\n",
            "data/techo5-linux/wpa_supplicant.conf.prev": "network={}\n",
            "data/techo5-linux/dropbear/host_key": "x",
            "data/techo5-linux/techo5.log": "x",
            "data/techo5-linux/techo5.log.1": "x",
            "data/techo5-linux/rescue.log": "kept\n",
            "data/misc/apexdata/com.android.wifi/WifiConfigStore.xml": "<x/>",
            "data/misc/wifi/WifiConfigStore.xml": "<x/>",
            "data/media/0/Download/keep.txt": "kept\n",
        }.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def test_nothing_happens_without_the_mark(self):
        r = reset(self.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.state / "psk").read_text(), "b2xkIGtleQ==\n")
        self.assertTrue((self.logs / "wpa_supplicant.conf").exists())

    def test_the_mark_erases_the_setup_and_keeps_the_rest(self):
        (self.state / "factory_reset").write_text("screen\n")
        r = reset(self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("factory reset: done", r.stdout)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["board.json", "profile", "psk", "setup-mode"])
        self.assertEqual((self.state / "profile").read_text(), "genbu-checkers-v1\n")
        self.assertEqual((self.state / "board.json").read_text(), '{"id":"checkers"}\n')
        # A new random key nobody holds, not none: a missing key would leave the device open.
        key = base64.b64decode((self.state / "psk").read_text())
        self.assertEqual(len(key), 32)
        self.assertNotEqual(key, bytes(32))
        self.assertEqual((self.state / "psk").stat().st_mode & 0o077, 0)
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), ["rescue.log"])
        self.assertFalse((self.root / "data/misc/apexdata/com.android.wifi/WifiConfigStore.xml").exists())
        self.assertFalse((self.root / "data/misc/wifi/WifiConfigStore.xml").exists())
        self.assertTrue((self.root / "data/media/0/Download/keep.txt").exists())

    def test_keep_wifi_keeps_the_networks_and_erases_the_rest(self):
        (self.state / "factory_reset").write_text("ssh\nkeep-wifi\n")
        r = reset(self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("factory reset: keeping the Wi-Fi", r.stdout)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["board.json", "profile", "psk", "setup-mode"])
        self.assertNotEqual((self.state / "psk").read_text(), "b2xkIGtleQ==\n")
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()),
                         ["rescue.log", "wpa_supplicant.conf", "wpa_supplicant.conf.prev"])
        self.assertTrue((self.root / "data/misc/apexdata/com.android.wifi/WifiConfigStore.xml").exists())
        self.assertTrue((self.root / "data/misc/wifi/WifiConfigStore.xml").exists())

    def test_only_a_line_of_its_own_keeps_the_wifi(self):
        (self.state / "factory_reset").write_text("screen keep-wifi-not\n")
        reset(self.root)
        self.assertFalse((self.logs / "wpa_supplicant.conf").exists())

    def test_runs_once(self):
        (self.state / "factory_reset").write_text("screen\n")
        reset(self.root)
        key = (self.state / "psk").read_text()
        self.assertNotEqual(reset(self.root).returncode, 0)
        self.assertEqual((self.state / "psk").read_text(), key)

    def test_wiring(self):
        boot = (ROOT / "tools/linux/rootfs/etc/techo5/boot.sh").read_text()
        # Before the time zone and the models are put back, so a reset unit gets them as a new one.
        self.assertLess(boot.index("t5_factory_reset"), boot.index("default-timezone"))
        self.assertLess(boot.index("mkdir -p /run/lock /run/techo5"), boot.index("t5_factory_reset"))
        settings = (ROOT / "echod/internal/feature/display/settings.go").read_text()
        self.assertIn('layout.StateDir + "/factory_reset"', settings)
        self.assertIn("T5_RESET=/data/misc/techo5/factory_reset", LIB.read_text())
        # What jarvis-show.py --remove-show looks for on the Show before it asks to keep the Wi-Fi.
        from importlib import import_module
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        show_reset = import_module("jarvis_crown.show_reset")
        self.assertEqual(show_reset.LIB, "/lib/techo5-lib.sh")
        self.assertRegex(LIB.read_text(), show_reset.KEEPS_WIFI)


if __name__ == "__main__":
    unittest.main()
