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


class TestXianyuSpecNormalize(unittest.TestCase):
    """规格按闲鱼「最大12字」规整，且 source_spec 保留完整原始规格（回上游下单用）。"""

    def test_spec_truncated_to_12_source_spec_full(self):
        item = {
            "title": "情趣套装",
            "source_url": "https://detail.1688.com/offer/1.html",
            "sku_list": [
                {"spec1": "黑色套装+L25发箍+L28羽毛+超长加项", "spec2": "", "price": 10.5, "stock": 9},
            ],
        }
        skus = normalize_sku_list(item)
        self.assertEqual(len(skus), 1)
        # 闲鱼展示规格值截到 12 字
        self.assertLessEqual(len(skus[0]["spec1"]), 12)
        # source_spec 保留完整原始规格（>12 字）
        self.assertIn("超长加项", skus[0]["source_spec"])

    def test_two_axis_source_spec_join(self):
        item = {
            "title": "x",
            "sku_list": [
                {"spec1": "超长颜色规格名称abcdefg", "spec2": "超长尺码规格名称hijklmn", "price": 1, "stock": 1},
            ],
        }
        skus = normalize_sku_list(item)
        self.assertLessEqual(len(skus[0]["spec1"]), 12)
        self.assertLessEqual(len(skus[0]["spec2"]), 12)
        # 双轴时 source_spec 用 > 连接完整规格
        self.assertIn(">", skus[0]["source_spec"])

    def test_explicit_source_spec_kept(self):
        item = {
            "title": "x",
            "sku_list": [
                {"spec1": "红色", "spec2": "", "source_spec": "原始规格值", "price": 1, "stock": 1},
            ],
        }
        skus = normalize_sku_list(item)
        self.assertEqual(skus[0]["source_spec"], "原始规格值")


if __name__ == "__main__":
    unittest.main()
