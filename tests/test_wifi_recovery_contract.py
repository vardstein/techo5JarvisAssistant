from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WifiRecoveryContractTests(unittest.TestCase):
    def read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_installer_allows_first_boot_without_saved_wifi(self):
        src = self.read("tools/install-show.py")
        self.assertNotIn("LineageOS has no saved Wi-Fi network: join one first", src)
        self.assertIn("first boot will open the on-screen Wi-Fi picker", src)
        self.assertIn("USB recovery remains available", src)

    def test_one_frontend_exposes_usb_wifi_recovery(self):
        src = self.read("tools/jarvis-show.py")
        self.assertRegex(src, r'choices=\[[^]]*"wifi"')
        self.assertIn('wifi recovery requires --wifi NETWORK', src)
        self.assertIn('tools" / "show-wifi.py"', src)
        self.assertIn('"--serial"', src)
        self.assertIn('"--passphrase-file"', src)

    def test_boot_with_no_saved_network_keeps_supplicant_for_screen(self):
        lib = self.read("tools/linux/techo5-lib.sh")
        boot = self.read("tools/linux/rootfs/etc/techo5/boot.sh")
        runner = self.read("tools/linux/rootfs/usr/local/sbin/techo5-run")
        self.assertIn("wifi: no network saved yet", lib)
        self.assertIn("udhcpc -i wlan0 -b", lib)
        self.assertIn("No network saved: nothing a restart or a reboot could join", boot)
        self.assertIn("pidof wpa_supplicant >/dev/null || t5_wifi_up", boot)
        # No network saved means no association, and the runner only waits while associated.
        self.assertIn("grep -q '^wpa_state=COMPLETED' || break", runner)

    def test_show_opens_wifi_picker_without_address(self):
        display = self.read("echod/internal/feature/display/display.go")
        splash = self.read("echod/internal/feature/display/splash.go")
        sheet = self.read("echod/internal/feature/display/sheet.go")
        self.assertIn("time.Sleep(noAddressWait)", display)
        self.assertIn("wifi.Current(context.Background()).Address == \"\"", display)
        self.assertIn("d.openWifi()", display)
        self.assertIn("noAddressWait = 45 * time.Second", splash)
        self.assertIn('wifiRow.id, wifiRow.kind, wifiRow.button = "wifi", ctlButton, "Change"', sheet)

    def test_failed_wifi_join_can_be_corrected_without_bricking_network_state(self):
        wifi = self.read("echod/internal/lib/wifi/wifi.go")
        self.assertIn("if len(old) > 0", wifi)
        self.assertIn('os.WriteFile(Conf, old, 0o600)', wifi)
        self.assertIn('return fmt.Errorf("wifi: could not join %q (%s)"', wifi)
        self.assertIn("if ssidOf(b) != ssid", wifi)  # corrected passphrase replaces same SSID

    def test_post_install_message_names_both_screen_and_usb_recovery(self):
        src = self.read("tools/install-show.py")
        self.assertIn("Settings -> Connections -> Wi-Fi", src)
        self.assertIn("jarvis-show.py wifi --wifi YOUR_NETWORK", src)


if __name__ == "__main__":
    unittest.main()
