from __future__ import annotations

import unittest

from cryoet_organizer.tabs.gallery import (
    _parse_gallery_float,
    _parse_gallery_float_bounds,
    _parse_gallery_float_range,
)


class GalleryFilterParsingTests(unittest.TestCase):
    def test_resolution_filter_accepts_decimal_comma(self) -> None:
        self.assertEqual(_parse_gallery_float("4.5"), 4.5)
        self.assertEqual(_parse_gallery_float("4,5"), 4.5)
        self.assertIsNone(_parse_gallery_float(""))
        self.assertIsNone(_parse_gallery_float("not a number"))

    def test_defocus_range_accepts_spacing_commas_and_reversed_bounds(self) -> None:
        self.assertEqual(_parse_gallery_float_range("2-4"), (2.0, 4.0))
        self.assertEqual(_parse_gallery_float_range("2,5 - 4,5"), (2.5, 4.5))
        self.assertEqual(_parse_gallery_float_range("4 - 2"), (2.0, 4.0))
        self.assertEqual(_parse_gallery_float_range("-4 - -2"), (-4.0, -2.0))
        self.assertIsNone(_parse_gallery_float_range(""))
        self.assertIsNone(_parse_gallery_float_range("2 to 4"))

    def test_defocus_bounds_parser_uses_two_fields(self) -> None:
        self.assertEqual(_parse_gallery_float_bounds("2", "4"), (2.0, 4.0))
        self.assertEqual(_parse_gallery_float_bounds("2,5", "4,5"), (2.5, 4.5))
        self.assertEqual(_parse_gallery_float_bounds("4", "2"), (2.0, 4.0))
        self.assertIsNone(_parse_gallery_float_bounds("", "4"))
        self.assertIsNone(_parse_gallery_float_bounds("2", ""))


if __name__ == "__main__":
    unittest.main()
