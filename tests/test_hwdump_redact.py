"""GENBU-188: hwdump.sh takes out what names one unit before its dump goes into the public repo, and the
dumps saved there name none."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "hwdump.sh"
DUMPS = ROOT / "docs" / "dumps"

# Made-up values, in the shapes the device prints them.
SAMPLE = """\
console=tty0 androidboot.serialno=G000AB0000000000 androidboot.bootreason=reboot
name=8GTF4R
cid=150100424a54463452023000000000ff
02:00:00:00:00:01
[ro.boot.btmacaddr]: [02:00:00:00:00:02]
[persist.adb.wifi.guid]: [adb-G000AB0000000000-abcdef]
[ro.bt.bdaddr_path]: [/data/vendor/bt/addr]
[wifi.interface]: [wlan0]
board_id=0001
serial=G000AB0000000000
mac_addr=020000000001
bt_mac_addr=020000000002
MemTotal: 1000 kB
"""

WANT = """\
console=tty0 androidboot.serialno=<redacted> androidboot.bootreason=reboot
name=8GTF4R
cid=<redacted>
<redacted-mac>
[ro.boot.btmacaddr]: [<redacted>]
[persist.adb.wifi.guid]: [<redacted>]
[ro.bt.bdaddr_path]: [/data/vendor/bt/addr]
[wifi.interface]: [wlan0]
board_id=0001
serial=<redacted>
mac_addr=<redacted>
bt_mac_addr=<redacted>
MemTotal: 1000 kB
"""

NAMES_A_UNIT = re.compile(
    r"serialno=(?!<redacted>)\S|^(serial|mac_addr|bt_mac_addr|cid)=(?!<redacted>)\S"
    r"|^\[[^]]*(serial|mac|bdaddr|guid)[^]]*\]: \[(?!<redacted>)[^]/]+\]|[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}",
    re.MULTILINE)


@unittest.skipUnless(shutil.which("sh") and shutil.which("sed"), "needs sh and sed")
class Redact(unittest.TestCase):
    def test_serials_and_addresses_are_taken_out_and_the_rest_left(self):
        out = subprocess.run(["sh", str(SCRIPT), "--redact"], input=SAMPLE, capture_output=True, text=True,
                             check=True).stdout
        self.assertEqual(out, WANT)
        self.assertIsNone(NAMES_A_UNIT.search(out))
        self.assertIsNotNone(NAMES_A_UNIT.search(SAMPLE))


class SavedDumps(unittest.TestCase):
    def test_no_saved_dump_names_its_unit(self):
        for dump in DUMPS.glob("*.txt"):
            hit = NAMES_A_UNIT.search(dump.read_text(errors="replace"))
            # The line number only: the value is what must not be printed.
            line = dump.read_text(errors="replace")[:hit.start()].count("\n") + 1 if hit else 0
            self.assertIsNone(hit, f"{dump.name}:{line} names its unit; sh tools/hwdump.sh --redact cleans it")


if __name__ == "__main__":
    unittest.main()
