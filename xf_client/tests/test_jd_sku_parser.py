"""京东多规格(colorSize)解析单测。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.jd_sku_parser import (
    parse_jd_sku_list,
    extract_color_size_from_html,
    _spec_dimension_keys,
)


class TestParseJdSkuList(unittest.TestCase):
    def test_two_dimensions(self):
        cs = [
            {"skuId": "1001", "颜色": "飞天53度", "尺码": "500ml", "Color": "飞天53度", "Size": "500ml"},
            {"skuId": "1002", "颜色": "飞天53度", "尺码": "1000ml"},
            {"skuId": "1003", "颜色": "金奖", "尺码": "500ml"},
        ]
        skus = parse_jd_sku_list(cs, base_price=1499.0)
        self.assertEqual(len(skus), 3)
        self.assertEqual(skus[0]["spec1"], "飞天53度")
        self.assertEqual(skus[0]["spec2"], "500ml")
        self.assertEqual(skus[0]["source_sku_id"], "1001")
        self.assertEqual(skus[0]["merchant_sku"], "1001")
        self.assertEqual(skus[0]["price"], 1499.0)
        # 英文重复维度键不参与 spec
        self.assertEqual(skus[0]["sku_attrs"], {"颜色": "飞天53度", "尺码": "500ml"})

    def test_single_dimension(self):
        cs = [
            {"skuId": "2001", "版本": "标准版"},
            {"skuId": "2002", "版本": "豪华版"},
        ]
        skus = parse_jd_sku_list(cs, base_price=99.0)
        self.assertEqual(len(skus), 2)
        self.assertEqual(skus[1]["spec1"], "豪华版")
        self.assertEqual(skus[1]["spec2"], "")

    def test_json_string_input(self):
        skus = parse_jd_sku_list('[{"skuId":"3001","颜色":"黑"}]', base_price=10.0)
        self.assertEqual(len(skus), 1)
        self.assertEqual(skus[0]["spec1"], "黑")

    def test_empty_and_invalid(self):
        self.assertEqual(parse_jd_sku_list([]), [])
        self.assertEqual(parse_jd_sku_list("not json"), [])
        self.assertEqual(parse_jd_sku_list(None), [])
        # 只有 skuId 无规格维度 → 无法形成 sku
        self.assertEqual(parse_jd_sku_list([{"skuId": "1"}]), [])

    def test_dedupe_combo(self):
        cs = [
            {"skuId": "1", "颜色": "红"},
            {"skuId": "1", "颜色": "红"},
        ]
        skus = parse_jd_sku_list(cs, base_price=5.0)
        self.assertEqual(len(skus), 1)

    def test_english_only_falls_back(self):
        # 无中文维度时用英文维度
        cs = [{"skuId": "1", "Color": "Red"}, {"skuId": "2", "Color": "Blue"}]
        skus = parse_jd_sku_list(cs, base_price=1.0)
        self.assertEqual(len(skus), 2)
        self.assertEqual(skus[0]["spec1"], "Red")


class TestSpecDimensionKeys(unittest.TestCase):
    def test_prefers_chinese(self):
        cs = [{"skuId": "1", "颜色": "红", "Color": "Red", "尺码": "M", "Size": "M"}]
        keys = _spec_dimension_keys(cs)
        self.assertEqual(keys, ["颜色", "尺码"])

    def test_cap_two(self):
        cs = [{"skuId": "1", "颜色": "红", "尺码": "M", "版本": "A", "套餐": "B"}]
        keys = _spec_dimension_keys(cs)
        self.assertEqual(len(keys), 2)


class TestExtractColorSizeFromHtml(unittest.TestCase):
    def test_extract(self):
        html = 'var x = 1; colorSize: [{"skuId":"100","颜色":"红"}], foo: 2'
        cs = extract_color_size_from_html(html)
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["skuId"], "100")

    def test_no_match(self):
        self.assertEqual(extract_color_size_from_html("no color size here"), [])
        self.assertEqual(extract_color_size_from_html(""), [])


if __name__ == "__main__":
    unittest.main()
