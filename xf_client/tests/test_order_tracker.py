"""订单跟踪纯逻辑单测：订单归一化 / 订单→商品 / 规格→SKU→源 skuId / 代采计划。

不触碰浏览器，仅验证可单测的匹配与归一化逻辑。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.order_tracker import (
    normalize_order,
    match_order_to_product,
    match_sku_for_order,
    build_reorder_plan,
)


def _product(**kw):
    base = {
        "db_id": 1,
        "title": "马年冰箱贴福字磁吸贴",
        "original_title": "马年冰箱贴福字磁吸贴",
        "xianyu_item_id": "899001122334",
        "source_url": "https://detail.1688.com/offer/723383755110.html",
        "source_platform": "1688",
        "sku_list": [
            {"spec1": "福字红色", "spec2": "", "price": 9.89, "stock": 100,
             "source_sku_id": "5196460270576", "source_spec": "福字红色", "merchant_sku": "5196460270576"},
            {"spec1": "马年蓝色", "spec2": "", "price": 15.6, "stock": 50,
             "source_sku_id": "5196460270577", "source_spec": "马年蓝色", "merchant_sku": "5196460270577"},
        ],
    }
    base.update(kw)
    return base


class TestNormalizeOrder(unittest.TestCase):
    def test_field_aliases(self):
        o = normalize_order({
            "itemId": "899001122334",
            "bizOrderId": "T2026-001",
            "item_title": "马年冰箱贴",
            "buyerNick": "小王",
            "sku_text": "福字红色",
            "buyAmount": "2",
            "payAmount": "¥19.78",
            "address": "上海市浦东新区xx路1号",
        })
        self.assertEqual(o["xianyu_item_id"], "899001122334")
        self.assertEqual(o["platform_order_id"], "T2026-001")
        self.assertEqual(o["buyer_name"], "小王")
        self.assertEqual(o["buyer_spec"], "福字红色")
        self.assertEqual(o["quantity"], 2)
        self.assertEqual(o["order_amount"], "19.78")
        self.assertEqual(o["platform"], "xianyu")

    def test_quantity_default(self):
        o = normalize_order({"item_id": "1"})
        self.assertEqual(o["quantity"], 1)

    def test_amount_extract(self):
        o = normalize_order({"price": "成交价 ¥ 1,234.50 元"})
        self.assertEqual(o["order_amount"], "1234.50")


class TestMatchOrderToProduct(unittest.TestCase):
    def test_match_by_xianyu_id(self):
        prods = [_product(db_id=1), _product(db_id=2, xianyu_item_id="700000000000", title="别的商品")]
        order = normalize_order({"xianyu_item_id": "899001122334", "title": "随便"})
        p = match_order_to_product(order, prods)
        self.assertIsNotNone(p)
        self.assertEqual(p["db_id"], 1)

    def test_match_by_title_when_no_id(self):
        prods = [_product(db_id=5, xianyu_item_id="")]
        order = normalize_order({"title": "马年冰箱贴福字磁吸贴 全新"})
        p = match_order_to_product(order, prods)
        self.assertIsNotNone(p)
        self.assertEqual(p["db_id"], 5)

    def test_no_match(self):
        prods = [_product(db_id=1, xianyu_item_id="111", title="完全不相干的东西")]
        order = normalize_order({"xianyu_item_id": "999", "title": "另一个无关商品啊"})
        self.assertIsNone(match_order_to_product(order, prods))

    def test_empty_products(self):
        self.assertIsNone(match_order_to_product(normalize_order({"item_id": "1"}), []))


class TestMatchSku(unittest.TestCase):
    def test_exact_spec(self):
        order = normalize_order({"sku_text": "福字红色"})
        r = match_sku_for_order(order, _product())
        self.assertTrue(r["ok"])
        self.assertEqual(r["source_sku_id"], "5196460270576")
        self.assertEqual(r["score"], 1.0)

    def test_second_spec(self):
        order = normalize_order({"sku_text": "马年蓝色"})
        r = match_sku_for_order(order, _product())
        self.assertEqual(r["source_sku_id"], "5196460270577")

    def test_single_sku_direct(self):
        prod = _product(sku_list=[{"spec1": "默认", "source_sku_id": "X1"}])
        order = normalize_order({"sku_text": ""})
        r = match_sku_for_order(order, prod)
        self.assertEqual(r["source_sku_id"], "X1")
        self.assertEqual(r["score"], 1.0)

    def test_no_spec_multi_fallback(self):
        order = normalize_order({"sku_text": ""})
        r = match_sku_for_order(order, _product())
        self.assertTrue(r["ok"])
        self.assertEqual(r["score"], 0.0)
        self.assertIn("回退", r["note"])

    def test_no_product(self):
        r = match_sku_for_order(normalize_order({}), None)
        self.assertFalse(r["ok"])


class TestReorderPlan(unittest.TestCase):
    def test_plan_exact(self):
        order = normalize_order({
            "sku_text": "福字红色", "buyAmount": "3",
            "buyer_name": "小王", "phone": "13800000000",
            "address": "上海市浦东新区xx路1号",
        })
        plan = build_reorder_plan(order, _product())
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["source_platform"], "1688")
        self.assertEqual(plan["source_sku_id"], "5196460270576")
        self.assertEqual(plan["quantity"], 3)
        self.assertEqual(plan["ship_to"]["name"], "小王")
        self.assertEqual(plan["ship_to"]["address"], "上海市浦东新区xx路1号")
        self.assertEqual(plan["spec_score"], 1.0)

    def test_plan_no_source_url(self):
        prod = _product(source_url="")
        order = normalize_order({"sku_text": "福字红色"})
        plan = build_reorder_plan(order, prod)
        self.assertFalse(plan["ok"])
        self.assertIn("源商品链接", plan["note"])

    def test_plan_fuzzy_warns(self):
        # 买家规格与任一 SKU 互不包含，但字符部分重叠 → 模糊命中（score<1）。
        order = normalize_order({"sku_text": "红色款"})
        plan = build_reorder_plan(order, _product())
        self.assertTrue(plan["source_url"])
        self.assertLess(plan["spec_score"], 1.0)
        self.assertGreater(plan["spec_score"], 0.0)
        self.assertIn("人工核对", plan["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
