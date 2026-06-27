"""采集筛选/排序单元测试（纯数据，无浏览器依赖）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.collect_filter import parse_number, filter_items, item_sales


ITEMS = [
    {"title": "A", "price": 9.9,  "wants": "1.2万", "views": "3000",  "sales": "50000"},
    {"title": "B", "price": 19.9, "wants": "500",   "views": "800",   "sales": "120"},
    {"title": "C", "price": 5.0,  "wants": "30",    "views": "100",   "sales": "5"},
    {"title": "D", "price": 99.0, "wants": "8000+", "views": "2万",   "sales": "0"},
]


class TestParseNumber(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_number("3000"), 3000)
        self.assertEqual(parse_number(12.5), 12.5)

    def test_wan_yi(self):
        self.assertEqual(parse_number("1.2万"), 12000)
        self.assertEqual(parse_number("2万"), 20000)
        self.assertEqual(parse_number("1亿"), 1e8)

    def test_noise(self):
        self.assertEqual(parse_number("8000+"), 8000)
        self.assertEqual(parse_number("¥9.9"), 9.9)
        self.assertEqual(parse_number("已拼5万件"), 50000)
        self.assertEqual(parse_number(""), 0.0)
        self.assertEqual(parse_number(None), 0.0)


class TestFilter(unittest.TestCase):
    def test_price_range(self):
        out = filter_items(ITEMS, min_price=6, max_price=20)
        titles = {i["title"] for i in out}
        self.assertEqual(titles, {"A", "B"})

    def test_min_sales(self):
        out = filter_items(ITEMS, min_sales=100)
        self.assertEqual({i["title"] for i in out}, {"A", "B"})

    def test_min_wants_with_wan(self):
        out = filter_items(ITEMS, min_wants=1000)
        # A=12000, D=8000 满足
        self.assertEqual({i["title"] for i in out}, {"A", "D"})

    def test_min_views_with_wan(self):
        out = filter_items(ITEMS, min_views=5000)
        # D views=2万=20000
        self.assertEqual({i["title"] for i in out}, {"D"})

    def test_sort_price_asc(self):
        out = filter_items(ITEMS, sort_by="price", order="asc")
        self.assertEqual([i["title"] for i in out], ["C", "A", "B", "D"])

    def test_sort_sales_desc(self):
        out = filter_items(ITEMS, sort_by="sales", order="desc")
        self.assertEqual(out[0]["title"], "A")  # sales 50000 最高

    def test_no_filter_returns_all(self):
        out = filter_items(ITEMS)
        self.assertEqual(len(out), 4)

    def test_does_not_mutate_input(self):
        before = list(ITEMS)
        filter_items(ITEMS, sort_by="price")
        self.assertEqual(ITEMS, before)


class TestItemSales(unittest.TestCase):
    def test_fallback_to_wants(self):
        self.assertEqual(item_sales({"wants": "1.2万"}), 12000)
        self.assertEqual(item_sales({"sales": "300", "wants": "10"}), 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
