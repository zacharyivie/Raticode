"""Google Play listing checks. Run: python3 -m unittest discover -s scripts -p 'test_*.py'

The store limits come from Play Console's store listing rules.
"""
import re
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
LISTING = ROOT / 'play/listing/en-US'


def png(path):
    """Width, height, bit depth and colour type from a PNG's header."""
    data = path.read_bytes()[:26]
    assert data[:8] == b'\x89PNG\r\n\x1a\n', path
    width, height = struct.unpack('>II', data[16:24])
    return width, height, data[24], data[25]


class PlayListingTest(unittest.TestCase):
    def test_text_fits_plays_limits(self):
        for name, limit in (('title.txt', 30), ('short-description.txt', 80), ('full-description.txt', 4000)):
            with self.subTest(name):
                text = (LISTING / name).read_text(encoding='utf8').strip()
                self.assertTrue(text)
                self.assertLessEqual(len(text), limit)

    def test_feature_graphic_is_1024_by_500_without_alpha(self):
        # Colour type 2 is RGB; Play refuses a feature graphic with an alpha channel.
        self.assertEqual(png(ROOT / 'play/graphics/feature-graphic.png'), (1024, 500, 8, 2))
        self.assertLess((ROOT / 'play/graphics/feature-graphic.png').stat().st_size, 15 * 1024 * 1024)

    def test_icon_is_512_square_with_alpha_under_1_mb(self):
        icon = REPOSITORY / 'website/public/brand/roundel-512.png'
        self.assertEqual(png(icon), (512, 512, 8, 6))
        self.assertLess(icon.stat().st_size, 1024 * 1024)

    def test_in_app_privacy_link_is_the_websites_privacy_page(self):
        screens = (ROOT / 'app/src/main/java/dev/raticode/mobile/ui/DesktopScreens.kt').read_text(encoding='utf8')
        url = re.search(r'PRIVACY_POLICY_URL = "([^"]+)"', screens).group(1)
        site = re.search(r"defaultSiteUrl = '([^']+)'", (REPOSITORY / 'website/data/site.ts').read_text()).group(1)
        self.assertEqual(url, site + 'docs/privacy')
        policy = (REPOSITORY / 'website/docs/privacy.md').read_text(encoding='utf8')
        self.assertIn('## Raticode Mobile', policy)
        self.assertIn('dev.raticode.mobile', policy)


if __name__ == '__main__':
    unittest.main()
