"""淘宝/天猫 SKU 解析器单元测试（夹具驱动，无浏览器依赖）。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.taobao_sku_parser import (
    parse_sku,
    parse_sku_from_html,
    extract_head_images,
    _price_from_info,
    _normalize_image_url,
)


# 仿真淘宝 skuBase：颜色(红/蓝) × 套餐(标配)，价格库存来自 skuCore
SKU_BASE = {
    "props": [
        {
            "pid": "1627207",
            "name": "颜色分类",
            "valueMap": {
                "101": {"vid": "101", "name": "红色", "image": "//gw.alicdn.com/red_800x800.jpg"},
                "102": {"vid": "102", "name": "蓝色", "image": "//gw.alicdn.com/blue_800x800.jpg"},
            },
        },
        {
            "pid": "5919063",
            "name": "套餐类型",
            "valueMap": {
                "9001": {"vid": "9001", "name": "官方标配"},
            },
        },
    ],
    "skus": [
        {"propPath": "1627207:101;5919063:9001", "skuId": "sku_red"},
        {"propPath": "1627207:102;5919063:9001", "skuId": "sku_blue"},
    ],
}

SKU_CORE = {
    "sku2info": {
        "0": {"quantity": 200, "price": {"priceMoney": "10500", "priceText": "105"}},
        "sku_red": {"quantity": 50, "price": {"priceMoney": "10500", "priceText": "105"}},
        "sku_blue": {"quantity": 9, "price": {"priceMoney": "14900", "priceText": "149"}},
    }
}


def make_html(sku_base, sku_core, components=None):
    parts = [
        '"skuBase":' + json.dumps(sku_base, ensure_ascii=False),
        '"skuCore":' + json.dumps(sku_core, ensure_ascii=False),
    ]
    if components is not None:
        parts.append('"componentsVO":' + json.dumps(components, ensure_ascii=False))
    blob = "{" + ",".join(parts) + "}"
    return f"<html><script>window.__page = {blob};</script></html>"


class TestTaobaoSkuParser(unittest.TestCase):
    def test_parse_two_skus(self):
        skus = parse_sku(SKU_BASE, SKU_CORE)
        self.assertEqual(len(skus), 2)

    def test_spec_price_stock_mapping(self):
        skus = {s["spec1"]: s for s in parse_sku(SKU_BASE, SKU_CORE)}
        self.assertIn("红色", skus)
        self.assertIn("蓝色", skus)
        self.assertAlmostEqual(skus["红色"]["price"], 105.0)
        self.assertAlmostEqual(skus["蓝色"]["price"], 149.0)
        self.assertEqual(skus["红色"]["stock"], 50)
        self.assertEqual(skus["蓝色"]["stock"], 9)

    def test_spec2_and_attrs(self):
        sku = parse_sku(SKU_BASE, SKU_CORE)[0]
        self.assertEqual(sku["spec2"], "官方标配")
        self.assertEqual(sku["sku_attrs"]["颜色分类"], "红色")
        self.assertEqual(sku["sku_attrs"]["套餐类型"], "官方标配")

    def test_sku_image_resolved(self):
        skus = {s["spec1"]: s for s in parse_sku(SKU_BASE, SKU_CORE)}
        self.assertTrue(skus["红色"]["sku_image_url"].startswith("https://"))
        self.assertIn("red", skus["红色"]["sku_image_url"])

    def test_merchant_sku_is_sku_id(self):
        skus = {s["spec1"]: s for s in parse_sku(SKU_BASE, SKU_CORE)}
        self.assertEqual(skus["红色"]["merchant_sku"], "sku_red")

    def test_parse_from_html(self):
        html = make_html(SKU_BASE, SKU_CORE)
        skus = parse_sku_from_html(html)
        self.assertEqual(len(skus), 2)

    def test_price_from_info_money_in_cents(self):
        self.assertAlmostEqual(_price_from_info({"price": {"priceMoney": "10500"}}), 105.0)

    def test_price_from_info_fallback_text(self):
        self.assertAlmostEqual(_price_from_info({"price": {"priceText": "88"}}), 88.0)

    def test_price_from_info_empty(self):
        self.assertEqual(_price_from_info({}), 0.0)

    def test_normalize_image_protocol(self):
        self.assertTrue(_normalize_image_url("//gw.alicdn.com/x.jpg").startswith("https://"))

    def test_extract_head_images(self):
        components = {"headImageVO": {"images": [
            "//gw.alicdn.com/a.jpg", "//gw.alicdn.com/b.jpg", "//gw.alicdn.com/a.jpg",
        ]}}
        html = make_html(SKU_BASE, SKU_CORE, components)
        imgs = extract_head_images(html)
        self.assertEqual(len(imgs), 2)  # 去重后 2 张
        self.assertTrue(all(u.startswith("https://") for u in imgs))

    def test_empty_html_returns_empty(self):
        self.assertEqual(parse_sku_from_html(""), [])
        self.assertEqual(parse_sku_from_html("<html></html>"), [])

    def test_missing_core_still_parses_specs(self):
        skus = parse_sku(SKU_BASE, {})
        self.assertEqual(len(skus), 2)
        self.assertEqual(skus[0]["price"], 0.0)
        self.assertEqual(skus[0]["stock"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
