"""apply_product_edits（商品编辑合并）单元测试。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.product_package import apply_product_edits, ensure_full_product_package


def _base_item():
    return ensure_full_product_package({
        "item_id": "1688_x",
        "platform": "1688",
        "title": "原标题",
        "description": "原描述",
        "price": 10.0,
        "source_url": "https://detail.1688.com/offer/123.html",
        "sku_list": [
            {"spec1": "红色", "spec2": "大", "price": 10.0, "stock": 5, "skuId": "s1"},
            {"spec1": "蓝色", "spec2": "小", "price": 12.0, "stock": 3, "skuId": "s2"},
        ],
    })


class TestApplyEdits(unittest.TestCase):
    def test_text_edits(self):
        out = apply_product_edits(_base_item(), {
            "title": "新标题",
            "description": "新描述",
            "short_title": "短",
            "brand": "某牌",
        })
        self.assertEqual(out["title"], "新标题")
        self.assertEqual(out["description"], "新描述")
        self.assertEqual(out["short_title"], "短")
        self.assertEqual(out["brand"], "某牌")

    def test_tags_string_split(self):
        out = apply_product_edits(_base_item(), {"tags": "复古, 全新，包邮"})
        self.assertEqual(out["tags"], ["复古", "全新", "包邮"])

    def test_unified_price_overrides(self):
        out = apply_product_edits(_base_item(), {"new_price": "19.9"})
        self.assertEqual(float(out["new_price"]), 19.9)
        self.assertEqual(float(out["price"]), 19.9)

    def test_sku_price_stock_by_index(self):
        out = apply_product_edits(_base_item(), {
            "sku_edits": [
                {"index": 0, "price": "8.5", "stock": "20"},
                {"index": 1, "price": "0", "stock": "0"},
            ]
        })
        skus = out["sku_list"]
        self.assertEqual(float(skus[0]["price"]), 8.5)
        self.assertEqual(int(skus[0]["stock"]), 20)
        # price=0 被忽略（保持原值），stock=0 合法置 0。
        self.assertEqual(float(skus[1]["price"]), 12.0)
        self.assertEqual(int(skus[1]["stock"]), 0)

    def test_sku_edit_out_of_range_ignored(self):
        out = apply_product_edits(_base_item(), {
            "sku_edits": [{"index": 99, "price": "5"}]
        })
        self.assertEqual(len(out["sku_list"]), 2)

    def test_untouched_fields_preserved(self):
        out = apply_product_edits(_base_item(), {"title": "只改标题"})
        self.assertEqual(out["description"], "原描述")
        self.assertEqual(out["source_url"], "https://detail.1688.com/offer/123.html")

    def test_empty_edits_noop(self):
        out = apply_product_edits(_base_item(), {})
        self.assertEqual(out["title"], "原标题")


if __name__ == "__main__":
    unittest.main(verbosity=2)
