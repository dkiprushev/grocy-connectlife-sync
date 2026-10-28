"""Branding must be inside the directory HACS installs."""
import struct
import unittest
from pathlib import Path


class BrandAssetsTest(unittest.TestCase):
    def test_local_brand_images_are_packaged(self):
        root = Path(__file__).resolve().parents[1]
        reference = (root / 'brand/icon.png').read_bytes()
        for filename in ('icon.png', 'dark_icon.png'):
            with self.subTest(filename=filename):
                asset = root / 'custom_components/hisense_food_manager/brand' / filename
                self.assertTrue(asset.is_file(), f'Missing installable brand asset: {asset}')
                data = asset.read_bytes()
                self.assertEqual(data, reference)
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', data[16:24]), (256, 256))
