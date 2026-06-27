"""来源追溯字段（source traceability）单元测试。

验证 normalize_sku_list / ensure_full_product_package 会保留并归一化：
- 每个 SKU 的 source_sku_id / source_spec（用于回上游按规格下单）
- 商品包级 source 块（platform / url / item_id / seller）
- 根据源链接推断平台标识
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.product_package import (
    normalize_sku_list,
    ensure_full_product_package,
    _infer_source_platform,
    PACKAGE_ATTR_KEY,
)


class TestInferSourcePlatform(unittest.TestCase):
    def test_1688(self):
        self.assertEqual(
            _infer_source_platform("https://detail.1688.com/offer/723383755110.html"),
            "1688",
        )

    def test_taobao_and_tmall(self):
        self.assertEqual(_infer_source_platform("https://item.taobao.com/x.htm"), "taobao")
        self.assertEqual(_infer_source_platform("https://detail.tmall.com/x.htm"), "taobao")

    def test_pdd(self):
        self.assertEqual(_infer_source_platform("https://mobile.yangkeduo.com/x"), "pdd")
        self.assertEqual(_infer_source_platform("https://www.pinduoduo.com/x"), "pdd")

    def test_jd(self):
        self.assertEqual(_infer_source_platform("https://item.jd.com/123.html"), "jd")

    def test_unknown_and_empty(self):
        self.assertEqual(_infer_source_platform("https://example.com/x"), "")
        self.assertEqual(_infer_source_platform(""), "")
        self.assertEqual(_infer_source_platform(None), "")


class TestSkuSourceFields(unittest.TestCase):
    def test_sku_id_from_skuId(self):
        skus = normalize_sku_list({
            "sku_list": [
                {"spec1": "红色", "price": "9.9", "skuId": 5196460270576,
                 "specAttrs": "红色>均码"},
            ]
        })
        self.assertEqual(skus[0]["source_sku_id"], "5196460270576")
        self.assertEqual(skus[0]["source_spec"], "红色>均码")

    def test_sku_id_falls_back_to_merchant_sku(self):
        skus = normalize_sku_list({
            "sku_list": [{"spec1": "蓝色", "price": "5", "merchant_sku": "ABC-1"}]
        })
        self.assertEqual(skus[0]["source_sku_id"], "ABC-1")

    def test_empty_sku_has_source_keys(self):
        skus = normalize_sku_list({"price": "3"})
        self.assertEqual(len(skus), 1)
        self.assertIn("source_sku_id", skus[0])
        self.assertIn("source_spec", skus[0])
        self.assertEqual(skus[0]["source_sku_id"], "")


class TestPackageSourceBlock(unittest.TestCase):
    def test_source_block_inferred_from_url(self):
        item = ensure_full_product_package({
            "title": "测试商品",
            "source_url": "https://detail.1688.com/offer/723383755110.html",
            "source_item_id": "723383755110",
            "seller": "某某商行",
            "sku_list": [{"spec1": "默认", "price": "1", "skuId": "999"}],
        })
        pkg = item["attributes"][PACKAGE_ATTR_KEY]
        self.assertIn("source", pkg)
        self.assertEqual(pkg["source"]["platform"], "1688")
        self.assertEqual(pkg["source"]["url"],
                         "https://detail.1688.com/offer/723383755110.html")
        self.assertEqual(pkg["source"]["item_id"], "723383755110")
        self.assertEqual(pkg["source"]["seller"], "某某商行")

    def test_source_platform_explicit_wins(self):
        item = ensure_full_product_package({
            "title": "x",
            "source_platform": "taobao",
            "source_url": "https://example.com/x",
            "sku_list": [{"spec1": "默认", "price": "1"}],
        })
        self.assertEqual(item["source_platform"], "taobao")

    def test_link_used_when_source_url_missing(self):
        item = ensure_full_product_package({
            "title": "x",
            "link": "https://item.jd.com/123.html",
            "sku_list": [{"spec1": "默认", "price": "1"}],
        })
        self.assertEqual(item["source_url"], "https://item.jd.com/123.html")
        self.assertEqual(item["source_platform"], "jd")


if __name__ == "__main__":
    unittest.main()
