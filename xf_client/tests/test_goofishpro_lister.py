"""闲管家(goofish.pro)上架器 + 打包层价格回填 回归测试。

背景（根因与修复）：
    1) 多平台采集常只产出 sku_list 而无顶层 price。ensure_full_product_package
       原先不回填顶层 price，导致闲管家 fill_product 拿到空售价 → 商品缺价发布，
       且 UI 上架价列、导出也为空。修复：打包层回填顶层 price = SKU 最低有效价。
    2) 闲管家普通模式不支持多规格（需付费升级鱼小铺）。多 SKU 降级单品后买家
       看不到可选规格。修复：把规格/价格清单整理进描述，买家可留言选规格。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.product_package import ensure_full_product_package
from engine.goofishpro_lister import GoofishProLister


class TestPackageBackfillPrice(unittest.TestCase):
    """打包层回填顶层售价为 SKU 最低有效价。"""

    def test_backfill_min_price_when_no_top_price(self):
        item = {
            "title": "多规格商品",
            "sku_list": [
                {"spec1": "黑色", "price": 25.0, "stock": 5},
                {"spec1": "红色", "price": 18.5, "stock": 3},
                {"spec1": "蓝色", "price": 32.0, "stock": 1},
            ],
        }
        out = ensure_full_product_package(item)
        self.assertEqual(float(out["price"]), 18.5)
        self.assertEqual(str(out["original_price"]), "18.5")

    def test_keeps_existing_top_price(self):
        # 已有合法顶层 price 时不覆盖。
        item = {
            "title": "商品",
            "price": 9.9,
            "sku_list": [{"spec1": "黑色", "price": 25.0, "stock": 5}],
        }
        out = ensure_full_product_package(item)
        self.assertEqual(float(out["price"]), 9.9)

    def test_ignores_zero_and_negative_prices(self):
        item = {
            "title": "商品",
            "sku_list": [
                {"spec1": "A", "price": 0, "stock": 5},
                {"spec1": "B", "price": 12.0, "stock": 5},
            ],
        }
        out = ensure_full_product_package(item)
        self.assertEqual(float(out["price"]), 12.0)


class TestSkuSummary(unittest.TestCase):
    """多规格降级单品时的规格摘要文本。"""

    def test_summary_lists_specs_and_prices(self):
        skus = [
            {"spec1": "黑色", "spec2": "L", "price": 25.0},
            {"spec1": "红色", "spec2": "M", "price": 18.5},
        ]
        text = GoofishProLister._format_sku_summary(skus)
        self.assertIn("【可选规格】", text)
        self.assertIn("黑色 L：¥25.00", text)
        self.assertIn("红色 M：¥18.50", text)

    def test_summary_spec_without_price(self):
        skus = [{"spec1": "均码", "spec2": "", "price": 0}]
        text = GoofishProLister._format_sku_summary(skus)
        self.assertIn("· 均码", text)
        self.assertNotIn("¥", text)

    def test_summary_empty_for_no_specs(self):
        self.assertEqual(GoofishProLister._format_sku_summary([]), "")
        self.assertEqual(GoofishProLister._format_sku_summary([{"price": 5}]), "")

    def test_summary_caps_rows(self):
        skus = [{"spec1": f"规格{i}", "price": i + 1} for i in range(50)]
        text = GoofishProLister._format_sku_summary(skus, max_rows=10)
        # 标题行 + 10 行规格。
        self.assertEqual(len(text.splitlines()), 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
