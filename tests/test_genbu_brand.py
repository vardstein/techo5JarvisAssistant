import pathlib
import struct
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BRAND = ROOT / "ha" / "custom_components" / "genbu" / "brand"

# Home Assistant (2026.3 on) serves a custom integration's own brand/ folder ahead of the brands CDN.
# Icons are square, 256 and 512 for @2x, and transparent; dark_ is the same mark for the dark theme.
ICONS = {"icon.png": 256, "icon@2x.png": 512, "dark_icon.png": 256, "dark_icon@2x.png": 512}
PNG = b"\x89PNG\r\n\x1a\n"


def header(path):
    data = path.read_bytes()
    if data[:8] != PNG or data[12:16] != b"IHDR":
        raise AssertionError(f"{path.name} is not a PNG")
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, colour, data


class GenbuBrandTest(unittest.TestCase):
    def test_the_icons_are_square_and_the_size_home_assistant_asks_for(self):
        for name, size in ICONS.items():
            with self.subTest(name):
                width, height, _, _ = header(BRAND / name)
                self.assertEqual((width, height), (size, size))

    def test_the_icons_are_transparent(self):
        for name in ICONS:
            with self.subTest(name):
                _, _, colour, data = header(BRAND / name)
                # Colour type 4 or 6 carries alpha; 2 or 3 with a tRNS chunk does too.
                self.assertTrue(colour in (4, 6) or b"tRNS" in data, f"{name} has no transparency")

    def test_nothing_else_is_in_the_folder(self):
        self.assertEqual(sorted(p.name for p in BRAND.iterdir()), sorted(ICONS))


if __name__ == "__main__":
    unittest.main()
