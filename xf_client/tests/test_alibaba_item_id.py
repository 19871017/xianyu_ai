"""_extract_item_id 单测：覆盖 1688 多种搜索结果链接形态（无浏览器依赖）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.alibaba_collector import AlibabaCollector


class TestExtractItemId(unittest.TestCase):
    def setUp(self):
        # __init__ 不开浏览器，可安全构造
        self.c = AlibabaCollector()

    def test_classic_offer_path(self):
        url = "https://detail.1688.com/offer/723383755110.html?spm=a312h.xxx"
        self.assertEqual(self.c._extract_item_id(url), "723383755110")

    def test_mobile_offerid_query(self):
        url = "http://detail.m.1688.com/page/index.html?offerId=981792531267&sortType=&pageId="
        self.assertEqual(self.c._extract_item_id(url), "981792531267")

    def test_fallback_long_number(self):
        url = "https://x.1688.com/abc/1039635283265"
        self.assertEqual(self.c._extract_item_id(url), "1039635283265")

    def test_empty(self):
        self.assertEqual(self.c._extract_item_id(""), "")

    def test_no_id(self):
        self.assertEqual(self.c._extract_item_id("https://www.1688.com/"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
