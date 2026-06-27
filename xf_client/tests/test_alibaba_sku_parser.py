"""1688 SKU 解析器单元测试（夹具驱动，无浏览器依赖）。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.alibaba_sku_parser import (
    extract_init_json,
    parse_sku_model,
    parse_sku_from_html,
    _normalize_image_url,
)


# 仿真 1688 skuModel：颜色(红/蓝) × 尺寸(S/M)，4 个组合，价格库存各不同
SKU_MODEL = {
    "skuProps": [
        {
            "prop": "颜色",
            "value": [
                {"vid": "101", "name": "红色", "imageUrl": "//cbu01.alicdn.com/red_800x800.jpg"},
                {"vid": "102", "name": "蓝色", "imageUrl": "//cbu01.alicdn.com/blue_800x800.jpg"},
            ],
        },
        {
            "prop": "尺寸",
            "value": [
                {"vid": "201", "name": "S"},
                {"vid": "202", "name": "M"},
            ],
        },
    ],
    "skuInfoMap": {
        "101>201": {"skuId": "s1", "price": "9.90", "canBookCount": 100},
        "101>202": {"skuId": "s2", "price": "10.50", "canBookCount": 50},
        "102>201": {"skuId": "s3", "price": "9.90", "canBookCount": 0},
        "102>202": {"skuId": "s4", "price": "11.00", "canBookCount": 200},
    },
}


def make_html(model):
    blob = json.dumps({"data": {"skuModel": model}}, ensure_ascii=False)
    return f"<html><script>window.__INIT_DATA__ = {blob};</script></html>"


class TestSkuParser(unittest.TestCase):
    def test_parse_four_skus(self):
        skus = parse_sku_model(SKU_MODEL)
        self.assertEqual(len(skus), 4)

    def test_spec_and_price_mapping(self):
        skus = {(s["spec1"], s["spec2"]): s for s in parse_sku_model(SKU_MODEL)}
        self.assertAlmostEqual(skus[("红色", "S")]["price"], 9.90)
        self.assertAlmostEqual(skus[("蓝色", "M")]["price"], 11.00)
        self.assertEqual(skus[("红色", "S")]["stock"], 100)
        self.assertEqual(skus[("蓝色", "S")]["stock"], 0)

    def test_sku_image_and_attrs(self):
        skus = parse_sku_model(SKU_MODEL)
        red = next(s for s in skus if s["spec1"] == "红色")
        self.assertTrue(red["sku_image_url"].startswith("https://"))
        self.assertEqual(red["sku_attrs"]["颜色"], "红色")
        self.assertIn("尺寸", red["sku_attrs"])

    def test_merchant_sku_preserved(self):
        skus = parse_sku_model(SKU_MODEL)
        self.assertTrue(all(s["merchant_sku"] for s in skus))

    def test_extract_from_html(self):
        html = make_html(SKU_MODEL)
        data = extract_init_json(html)
        self.assertIsInstance(data, dict)
        skus = parse_sku_from_html(html)
        self.assertEqual(len(skus), 4)

    def test_single_spec_model(self):
        model = {
            "skuProps": [
                {"prop": "规格", "value": [
                    {"vid": "1", "name": "套餐A"},
                    {"vid": "2", "name": "套餐B"},
                ]},
            ],
            "skuInfoMap": {
                "1": {"skuId": "a", "price": 19.9, "canBookCount": 10},
                "2": {"skuId": "b", "price": 29.9, "canBookCount": 5},
            },
        }
        skus = parse_sku_model(model)
        self.assertEqual(len(skus), 2)
        self.assertEqual(skus[0]["spec2"], "")
        self.assertEqual({s["spec1"] for s in skus}, {"套餐A", "套餐B"})

    def test_empty_on_garbage(self):
        self.assertEqual(parse_sku_from_html("<html>no data</html>"), [])
        self.assertEqual(parse_sku_model({}), [])

    def test_image_url_normalization(self):
        self.assertEqual(
            _normalize_image_url("//cbu01.alicdn.com/x_800x800.jpg"),
            "https://cbu01.alicdn.com/x.jpg",
        )
        self.assertEqual(
            _normalize_image_url("https://cbu01.alicdn.com/x.jpg_.webp"),
            "https://cbu01.alicdn.com/x.jpg",
        )


class TestPackageIntegration(unittest.TestCase):
    """验证解析结果能被打包层 normalize_sku_list 直接消费。"""

    def test_feeds_normalize_sku_list(self):
        from engine.product_package import normalize_sku_list
        skus = parse_sku_model(SKU_MODEL)
        item = {"title": "测试商品", "price": 9.9, "sku_list": skus}
        normalized = normalize_sku_list(item)
        self.assertEqual(len(normalized), 4)
        for n in normalized:
            self.assertIn("spec1", n)
            self.assertIn("price", n)
            self.assertGreaterEqual(n["price"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# 真实 1688 ICE 详情页结构（offer 723383755110 实采，规格名拼接 key + specAttrs）
REAL_SKU_MODEL = {
    "skuProps": [
        {"fid": 3216, "prop": "颜色", "value": [
            {"name": "黑色套装（不含发箍）", "imageUrl": "https://cbu01.alicdn.com/a_!!2621361605-0-cib.jpg"},
            {"name": "黑色套装+L25发箍", "imageUrl": "https://cbu01.alicdn.com/b_!!2621361605-0-cib.jpg"},
            {"name": "黑色套装+L25发箍+L28羽毛", "imageUrl": "https://cbu01.alicdn.com/c_!!2621361605-0-cib.jpg"},
        ]},
        {"fid": 450, "prop": "尺码", "value": [{"name": "均码"}]},
    ],
    "skuInfoMap": {
        "黑色套装（不含发箍）>均码": {"specId": "fd8d", "specAttrs": "黑色套装（不含发箍）>均码", "price": "4.50", "discountPrice": "4.50", "canBookCount": 190, "skuId": 5196460270576},
        "黑色套装+L25发箍>均码": {"specId": "9724", "specAttrs": "黑色套装+L25发箍>均码", "price": "7.00", "discountPrice": "7.00", "canBookCount": 1838, "skuId": 5028895629090},
        "黑色套装+L25发箍+L28羽毛>均码": {"specId": "927c", "specAttrs": "黑色套装+L25发箍+L28羽毛>均码", "price": "10.50", "discountPrice": "10.50", "canBookCount": 1899, "skuId": 5028895629089},
    },
}


class TestRealIcePage(unittest.TestCase):
    """真实 ICE 详情页结构回归（规格名拼接 key，无 vid）。"""

    def test_parse_three_real_skus(self):
        skus = parse_sku_model(REAL_SKU_MODEL)
        self.assertEqual(len(skus), 3)

    def test_spec_split_and_price(self):
        skus = {s["spec1"]: s for s in parse_sku_model(REAL_SKU_MODEL)}
        self.assertAlmostEqual(skus["黑色套装（不含发箍）"]["price"], 4.50)
        self.assertAlmostEqual(skus["黑色套装+L25发箍+L28羽毛"]["price"], 10.50)
        # spec2 应正确拆出尺码
        self.assertEqual(skus["黑色套装+L25发箍"]["spec2"], "均码")
        self.assertEqual(skus["黑色套装+L25发箍"]["stock"], 1838)

    def test_attrs_and_image_mapping(self):
        skus = parse_sku_model(REAL_SKU_MODEL)
        s = next(x for x in skus if x["spec1"] == "黑色套装+L25发箍")
        self.assertEqual(s["sku_attrs"]["颜色"], "黑色套装+L25发箍")
        self.assertEqual(s["sku_attrs"]["尺码"], "均码")
        self.assertTrue(s["sku_image_url"].startswith("https://"))

    def test_merchant_sku_from_skuid(self):
        skus = parse_sku_model(REAL_SKU_MODEL)
        self.assertEqual(skus[0]["merchant_sku"], "5196460270576")

    def test_blob_extraction_with_html_entity(self):
        # skuInfoMap key 在 HTML 里 '>' 被转义成 &gt;，解析器应能处理 blob 提取
        blob = json.dumps({"skuModel": REAL_SKU_MODEL}, ensure_ascii=False)
        html = f"<html><script>var x={{\"data\":{blob}}};</script></html>"
        skus = parse_sku_from_html(html)
        self.assertEqual(len(skus), 3)
