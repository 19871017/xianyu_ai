"""源商品复检 compare_source 纯逻辑回归测试。

覆盖五类风险：亏本(below_cost)、源价上涨(price_up)、整体售罄(sold_out)、
部分规格消失(sku_gone)、单规格售罄(sku_sold_out)，以及重采为空(offline)。
告警级别取最高(critical>warn>info>none)。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.source_recheck import compare_source


def _item(skus):
    return {"sku_list": skus}


class TestCompareSource(unittest.TestCase):
    def test_normal_no_alert(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 100, "source_spec": "红"}])
        new = _item([{"spec1": "红", "price": 10, "stock": 100, "source_spec": "红"}])
        r = compare_source(old, new, listing_price=20)
        self.assertEqual(r["level"], "none")
        self.assertEqual(r["alerts"], [])

    def test_offline_when_new_empty(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        r = compare_source(old, None, listing_price=20)
        self.assertEqual(r["level"], "critical")
        self.assertEqual(r["alerts"][0]["type"], "offline")

    def test_sold_out_all_zero(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        new = _item([{"spec1": "红", "price": 10, "stock": 0, "source_spec": "红"}])
        r = compare_source(old, new, listing_price=20)
        types = {a["type"] for a in r["alerts"]}
        self.assertIn("sold_out", types)
        self.assertEqual(r["level"], "critical")

    def test_sku_gone(self):
        old = _item([
            {"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"},
            {"spec1": "蓝", "price": 10, "stock": 5, "source_spec": "蓝"},
        ])
        new = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        r = compare_source(old, new, listing_price=20)
        types = {a["type"] for a in r["alerts"]}
        self.assertIn("sku_gone", types)

    def test_single_sku_sold_out(self):
        old = _item([
            {"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"},
            {"spec1": "蓝", "price": 10, "stock": 5, "source_spec": "蓝"},
        ])
        new = _item([
            {"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"},
            {"spec1": "蓝", "price": 10, "stock": 0, "source_spec": "蓝"},
        ])
        r = compare_source(old, new, listing_price=20)
        types = {a["type"] for a in r["alerts"]}
        self.assertIn("sku_sold_out", types)

    def test_price_up_over_threshold(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        new = _item([{"spec1": "红", "price": 12, "stock": 5, "source_spec": "红"}])
        r = compare_source(old, new, listing_price=20, price_up_pct=10)
        types = {a["type"] for a in r["alerts"]}
        self.assertIn("price_up", types)

    def test_price_up_under_threshold_no_alert(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        new = _item([{"spec1": "红", "price": 10.5, "stock": 5, "source_spec": "红"}])
        r = compare_source(old, new, listing_price=20, price_up_pct=10)
        types = {a["type"] for a in r["alerts"]}
        self.assertNotIn("price_up", types)

    def test_below_cost_critical(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        new = _item([{"spec1": "红", "price": 18, "stock": 5, "source_spec": "红"}])
        # 闲鱼售价 15 ≤ 源最低 18 → 亏本
        r = compare_source(old, new, listing_price=15)
        types = {a["type"] for a in r["alerts"]}
        self.assertIn("below_cost", types)
        self.assertEqual(r["level"], "critical")

    def test_below_cost_skipped_when_no_listing_price(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        new = _item([{"spec1": "红", "price": 18, "stock": 5, "source_spec": "红"}])
        r = compare_source(old, new, listing_price=0)
        types = {a["type"] for a in r["alerts"]}
        self.assertNotIn("below_cost", types)

    def test_min_price_reported(self):
        old = _item([{"spec1": "红", "price": 10, "stock": 5, "source_spec": "红"}])
        new = _item([
            {"spec1": "红", "price": 12, "stock": 5, "source_spec": "红"},
            {"spec1": "蓝", "price": 9, "stock": 5, "source_spec": "蓝"},
        ])
        r = compare_source(old, new, listing_price=20)
        self.assertEqual(r["old_min_price"], 10.0)
        self.assertEqual(r["new_min_price"], 9.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
