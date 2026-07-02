"""闲鱼多规格发布纯逻辑单测（不触发浏览器）。

只覆盖 XianyuLister 中与 DOM 无关的纯函数：
- 规格值归一 _norm_spec
- 从 sku_list 提取规格轴 _collect_spec_axes（去重保序）
- 规格类型推断 _infer_spec_type（命中关键词 / 兜底「颜色」）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.xianyu_lister import XianyuLister, SPEC_TYPE_OPTIONS


class TestNormSpec(unittest.TestCase):
    def test_strip_and_truncate(self):
        self.assertEqual(XianyuLister._norm_spec("  红色  "), "红色")
        self.assertEqual(XianyuLister._norm_spec(None), "")
        self.assertEqual(len(XianyuLister._norm_spec("超" * 50)), 12)


class TestCollectSpecAxes(unittest.TestCase):
    def setUp(self):
        self.lister = XianyuLister()

    def test_single_axis_dedup_order(self):
        skus = [
            {"spec1": "红色", "spec2": ""},
            {"spec1": "蓝色", "spec2": ""},
            {"spec1": "红色", "spec2": ""},
        ]
        v1, v2 = self.lister._collect_spec_axes(skus)
        self.assertEqual(v1, ["红色", "蓝色"])
        self.assertEqual(v2, [])

    def test_two_axes(self):
        skus = [
            {"spec1": "红色", "spec2": "S"},
            {"spec1": "红色", "spec2": "M"},
            {"spec1": "蓝色", "spec2": "S"},
        ]
        v1, v2 = self.lister._collect_spec_axes(skus)
        self.assertEqual(v1, ["红色", "蓝色"])
        self.assertEqual(v2, ["S", "M"])


class TestInferSpecType(unittest.TestCase):
    def test_color(self):
        self.assertEqual(XianyuLister._infer_spec_type(["红色", "蓝色"]), "颜色")

    def test_size(self):
        self.assertEqual(XianyuLister._infer_spec_type(["S码", "M码", "L码"]), "尺码")

    def test_count(self):
        self.assertEqual(XianyuLister._infer_spec_type(["1个装", "2个装"]), "份数")

    def test_fallback_color(self):
        # 无任何关键词命中时回退到第一项「颜色」。
        result = XianyuLister._infer_spec_type(["xyz", "abc"])
        self.assertEqual(result, SPEC_TYPE_OPTIONS[0])
        self.assertEqual(result, "颜色")


class TestStripEmoji(unittest.TestCase):
    """闲鱼禁止标题/描述含 emoji，发布前清洗。"""

    def test_removes_common_emoji(self):
        out = XianyuLister._strip_emoji("全新现货🌸 磁吸支架💗 颜值在线✨")
        self.assertNotIn("🌸", out)
        self.assertNotIn("💗", out)
        self.assertNotIn("✨", out)
        self.assertIn("全新现货", out)
        self.assertIn("磁吸支架", out)

    def test_removes_flags_and_symbols(self):
        out = XianyuLister._strip_emoji("发货快📱🚚 好评🇨🇳 价廉➡️")
        for ch in ("📱", "🚚", "🇨", "🇳", "➡"):
            self.assertNotIn(ch, out)
        self.assertIn("发货快", out)

    def test_keeps_cjk_and_punctuation(self):
        text = "适配iPhone 11—17全系列（含Pro/ProMax），下单备注机型~"
        out = XianyuLister._strip_emoji(text)
        self.assertEqual(out, text)

    def test_empty_and_none(self):
        self.assertEqual(XianyuLister._strip_emoji(""), "")
        self.assertEqual(XianyuLister._strip_emoji(None), "")

    def test_collapses_blank_lines(self):
        out = XianyuLister._strip_emoji("第一行✨\n\n\n\n第二行🌸")
        self.assertNotIn("\n\n\n", out)
        self.assertIn("第一行", out)
        self.assertIn("第二行", out)


if __name__ == "__main__":
    unittest.main()


class TestClampStock(unittest.TestCase):
    def test_within_range(self):
        self.assertEqual(XianyuLister._clamp_stock(500), 500)
        self.assertEqual(XianyuLister._clamp_stock("888"), 888)

    def test_over_max_clamped(self):
        self.assertEqual(XianyuLister._clamp_stock(99999), 10000)
        self.assertEqual(XianyuLister._clamp_stock("123456"), 10000)
        self.assertEqual(XianyuLister._clamp_stock("12,345"), 10000)

    def test_negative_to_zero(self):
        self.assertEqual(XianyuLister._clamp_stock(-5), 0)

    def test_invalid_uses_default(self):
        self.assertEqual(XianyuLister._clamp_stock(""), 100)
        self.assertEqual(XianyuLister._clamp_stock(None), 100)
        self.assertEqual(XianyuLister._clamp_stock("abc"), 100)

    def test_float_string(self):
        self.assertEqual(XianyuLister._clamp_stock("10000.0"), 10000)


class TestPadAxisValues(unittest.TestCase):
    def test_already_two_no_pad(self):
        vals, pads = XianyuLister._pad_axis_values(["红色", "蓝色"])
        self.assertEqual(vals, ["红色", "蓝色"])
        self.assertEqual(pads, set())

    def test_single_padded_to_two(self):
        vals, pads = XianyuLister._pad_axis_values(["红色"])
        self.assertEqual(len(vals), 2)
        self.assertEqual(vals[0], "红色")
        self.assertEqual(len(pads), 1)
        self.assertIn(vals[1], pads)

    def test_pad_avoids_collision(self):
        vals, pads = XianyuLister._pad_axis_values(["其它"])
        self.assertEqual(len(vals), 2)
        self.assertNotEqual(vals[0], vals[1])
