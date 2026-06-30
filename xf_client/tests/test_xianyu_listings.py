"""闲鱼在售商品纯逻辑单测：归一化 + 汇总。

不触碰浏览器，仅验证可单测的归一化与汇总逻辑。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.xianyu_listings import normalize_listing, summarize_listings


class TestNormalizeListing(unittest.TestCase):
    def test_basic_fields(self):
        out = normalize_listing({
            "itemId": "899001122334",
            "title": "马年冰箱贴福字磁吸贴",
            "price": "9.89",
            "wants": "39",
            "views": "402",
            "href": "https://www.goofish.com/item?id=899001122334",
        })
        self.assertEqual(out["item_id"], "899001122334")
        self.assertEqual(out["title"], "马年冰箱贴福字磁吸贴")
        self.assertAlmostEqual(out["price"], 9.89)
        self.assertEqual(out["wants"], 39)
        self.assertEqual(out["views"], 402)
        self.assertIn("899001122334", out["link"])

    def test_price_with_symbol_and_comma(self):
        out = normalize_listing({"price": "¥1,628.00"})
        self.assertAlmostEqual(out["price"], 1628.0)

    def test_empty_and_garbage(self):
        out = normalize_listing({})
        self.assertEqual(out["item_id"], "")
        self.assertEqual(out["price"], 0.0)
        self.assertEqual(out["wants"], 0)
        self.assertEqual(out["views"], 0)

    def test_non_numeric_counts(self):
        out = normalize_listing({"wants": "想要", "views": "—"})
        self.assertEqual(out["wants"], 0)
        self.assertEqual(out["views"], 0)


class TestSummarizeListings(unittest.TestCase):
    def test_summary(self):
        listings = [
            {"item_id": "1", "price": "10", "wants": "5", "views": "100"},
            {"item_id": "2", "price": "20", "wants": "3", "views": "50"},
            {"item_id": "3", "price": "0", "wants": "0", "views": "0"},
        ]
        s = summarize_listings(listings)
        self.assertEqual(s["active_listings"], 3)
        self.assertEqual(s["total_wants"], 8)
        self.assertEqual(s["total_views"], 150)
        # 均价只统计价格>0的：(10+20)/2=15
        self.assertAlmostEqual(s["avg_price"], 15.0)
        self.assertEqual(len(s["listings"]), 3)

    def test_empty(self):
        s = summarize_listings([])
        self.assertEqual(s["active_listings"], 0)
        self.assertEqual(s["total_wants"], 0)
        self.assertEqual(s["total_views"], 0)
        self.assertEqual(s["avg_price"], 0.0)

    def test_ignores_non_dict(self):
        s = summarize_listings([{"price": "10"}, None, "x", 5])
        self.assertEqual(s["active_listings"], 1)


if __name__ == "__main__":
    unittest.main()
