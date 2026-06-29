"""利润测算 + 选品打分 回归测试。

全部为纯逻辑测试，注入固定输入，离线可重复。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.profit_score import (
    compute_profit, score_product, rank_products,
)


class TestComputeProfit(unittest.TestCase):
    def test_basic_profit(self):
        # 源价10，售价30，运费5，费率0.6% → 成本15，费0.18，净14.82
        r = compute_profit(10, 30, shipping_cost=5, platform_fee_pct=0.6)
        self.assertEqual(r["cost"], 15.0)
        self.assertEqual(r["platform_fee"], 0.18)
        self.assertEqual(r["net_profit"], 14.82)
        self.assertTrue(r["profitable"])

    def test_loss_detected(self):
        # 源价30，售价30，运费5 → 净利为负
        r = compute_profit(30, 30, shipping_cost=5, platform_fee_pct=0.6)
        self.assertFalse(r["profitable"])
        self.assertLess(r["net_profit"], 0)

    def test_markup_pct(self):
        r = compute_profit(10, 20, shipping_cost=0, platform_fee_pct=0)
        self.assertEqual(r["markup_pct"], 100.0)

    def test_zero_sell_no_crash(self):
        r = compute_profit(10, 0)
        self.assertEqual(r["net_margin_pct"], 0.0)
        self.assertFalse(r["profitable"])

    def test_net_margin_pct(self):
        # 售价100，成本含源50+运0+费0 → 净50，净利率50%
        r = compute_profit(50, 100, shipping_cost=0, platform_fee_pct=0)
        self.assertEqual(r["net_margin_pct"], 50.0)


class TestScoreProduct(unittest.TestCase):
    def test_high_quality_product(self):
        p = {
            "original_price": "20", "new_price": "60", "wants": "500",
            "sku_list": [
                {"price": 20, "stock": 100},
                {"price": 22, "stock": 100},
                {"price": 24, "stock": 100},
            ],
        }
        res = score_product(p, shipping_cost=5, platform_fee_pct=0.6)
        self.assertGreaterEqual(res["score"], 75)
        self.assertEqual(res["grade"], "A")
        self.assertTrue(res["profit"]["profitable"])

    def test_loss_product_low_score(self):
        p = {"original_price": "50", "new_price": "50", "sku_list": []}
        res = score_product(p, shipping_cost=5)
        self.assertFalse(res["profit"]["profitable"])
        self.assertLess(res["score"], 40)
        self.assertTrue(any("亏损" in r for r in res["reasons"]))

    def test_uses_sku_min_price_as_source(self):
        # SKU 最低价 15 作为源价，而非 original_price 的 99
        p = {
            "original_price": "99", "new_price": "60",
            "sku_list": [{"price": 30}, {"price": 15}, {"price": 20}],
        }
        res = score_product(p, shipping_cost=0, platform_fee_pct=0)
        self.assertEqual(res["profit"]["source_price"], 15.0)

    def test_missing_wants_neutral(self):
        p = {"original_price": "20", "new_price": "60", "sku_list": []}
        res = score_product(p)
        self.assertIsNone(res["signals"]["wants"])
        self.assertTrue(any("无需求数据" in r for r in res["reasons"]))

    def test_grade_boundaries(self):
        # 纯亏损商品应为 D
        p = {"original_price": "100", "new_price": "80", "sku_list": []}
        res = score_product(p, shipping_cost=5)
        self.assertEqual(res["grade"], "D")

    def test_target_markup_projects_sell_price(self):
        # 售价未加价（=源价）时，按目标加价率推算售价
        p = {"original_price": "100", "new_price": "100", "sku_list": []}
        res = score_product(p, shipping_cost=0, platform_fee_pct=0,
                            target_markup_pct=50)
        self.assertTrue(res["projected"])
        self.assertEqual(res["profit"]["sell_price"], 150.0)
        self.assertTrue(res["profit"]["profitable"])
        self.assertTrue(any("推算" in r for r in res["reasons"]))

    def test_target_markup_zero_no_projection(self):
        # target=0 不推算，保持原售价
        p = {"original_price": "100", "new_price": "100", "sku_list": []}
        res = score_product(p, shipping_cost=0, platform_fee_pct=0,
                            target_markup_pct=0)
        self.assertFalse(res["projected"])
        self.assertEqual(res["profit"]["sell_price"], 100.0)

    def test_target_markup_skipped_when_already_priced(self):
        # 已加价的商品不被推算覆盖
        p = {"original_price": "100", "new_price": "200", "sku_list": []}
        res = score_product(p, shipping_cost=0, platform_fee_pct=0,
                            target_markup_pct=50)
        self.assertFalse(res["projected"])
        self.assertEqual(res["profit"]["sell_price"], 200.0)


class TestRankProducts(unittest.TestCase):
    def test_sorts_by_score_desc(self):
        good = {"original_price": "10", "new_price": "60", "wants": "800",
                "sku_list": [{"price": 10, "stock": 200}, {"price": 11, "stock": 200}]}
        bad = {"original_price": "55", "new_price": "55", "sku_list": []}
        ranked = rank_products([bad, good], shipping_cost=5)
        self.assertEqual(ranked[0]["product"], good)
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_empty_list(self):
        self.assertEqual(rank_products([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
