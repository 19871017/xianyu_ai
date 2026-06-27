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
        self.assertEqual(len(XianyuLister._norm_spec("超" * 50)), 30)


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


if __name__ == "__main__":
    unittest.main()
