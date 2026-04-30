from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from app.extractors.site_extractors import _remove_noise


class _FakeSoup:
    def __init__(self, all_tags: list[object]) -> None:
        self._all_tags = all_tags

    def find_all(self, name: object) -> list[object]:
        if name is True:
            return self._all_tags
        return []


class SiteExtractorsSmokeTest(unittest.TestCase):
    def test_remove_noise_handles_none_and_tags_without_attrs(self) -> None:
        removable_tag = BeautifulSoup(
            '<div class="nav sidebar">remove me</div>', "html.parser"
        ).div
        malformed_tag = BeautifulSoup("<section>broken</section>", "html.parser").section
        malformed_tag.attrs = None
        soup = _FakeSoup([None, malformed_tag, removable_tag])

        _remove_noise(soup, keywords=("nav", "sidebar"))

        self.assertIsNone(removable_tag.parent)
        self.assertIsNone(malformed_tag.attrs)


if __name__ == "__main__":
    unittest.main()
