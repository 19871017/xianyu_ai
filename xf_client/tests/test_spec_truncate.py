"""规格值「碰撞感知边界截断」回归测试。

闲鱼规格值上限 12 字，旧逻辑硬截断会切在词中间（如 `12pro max (6`、
`淡蓝细条纹+蓝`）。新逻辑优先在分隔符处断词，但仅当不会让两个不同完整值
被截成同名时才采用，否则整轴回退硬截断（保区分度优先于结尾美观）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.product_package import (
    normalize_sku_list,
    _boundary_truncate,
    _truncate_axis_values,
    XIANYU_SPEC_VALUE_MAXLEN as MAXLEN,
)


class TestBoundaryTruncate(unittest.TestCase):
    def test_short_value_untouched(self):
        self.assertEqual(_boundary_truncate("12pro", MAXLEN), "12pro")

    def test_breaks_at_separator_not_midword(self):
        # `12pro max (6.7)` 应断到 `12pro max`，不是硬截断的 `12pro max (6`
        out = _boundary_truncate("12pro max (6.7)", MAXLEN)
        self.assertLessEqual(len(out), MAXLEN)
        self.assertEqual(out, "12pro max")

    def test_trailing_separator_stripped(self):
        out = _boundary_truncate("淡蓝细条纹+蓝红拼格 爱心磁吸支架", MAXLEN)
        self.assertLessEqual(len(out), MAXLEN)
        self.assertFalse(out.endswith("+"))

    def test_no_separator_falls_back_to_hard_cut(self):
        s = "abcdefghijklmnopqrstuvwxyz"
        out = _boundary_truncate(s, MAXLEN)
        self.assertEqual(out, s[:MAXLEN])


class TestTruncateAxisValues(unittest.TestCase):
    def test_collision_falls_back_to_hard_cut(self):
        # 两个仅在 12 字后才不同的值，边界截断会撞名 → 整轴回退硬截断保区分。
        vals = [
            "蓝边磁吸 淡蓝细条纹+蓝红拼格 爱心磁吸支架",
            "蓝边磁吸 淡蓝细条纹",
        ]
        m = _truncate_axis_values(vals, MAXLEN)
        self.assertEqual(len(set(m.values())), 2)

    def test_no_collision_uses_boundary(self):
        vals = ["12pro max (6.7)", "13promax", "14pro"]
        m = _truncate_axis_values(vals, MAXLEN)
        self.assertEqual(m["12pro max (6.7)"], "12pro max")
        self.assertEqual(len(set(m.values())), 3)


class TestNormalizeIntegration(unittest.TestCase):
    def test_model_axis_clean_break_no_merge(self):
        item = {
            "title": "手机壳",
            "sku_list": [
                {"spec1": "蓝色", "spec2": "12pro max (6.7)", "price": 1, "stock": 1},
                {"spec1": "蓝色", "spec2": "13promax", "price": 1, "stock": 1},
            ],
        }
        skus = normalize_sku_list(item)
        spec2s = {s["spec2"] for s in skus}
        self.assertIn("12pro max", spec2s)
        self.assertEqual(len(spec2s), 2)

    def test_colliding_colors_preserved(self):
        item = {
            "title": "手机壳",
            "sku_list": [
                {"spec1": "蓝边磁吸 淡蓝细条纹+蓝红拼格 爱心磁吸支架",
                 "spec2": "11", "price": 1, "stock": 1},
                {"spec1": "蓝边磁吸 淡蓝细条纹", "spec2": "11", "price": 2, "stock": 1},
            ],
        }
        skus = normalize_sku_list(item)
        spec1s = {s["spec1"] for s in skus}
        self.assertEqual(len(spec1s), 2)
        for s in skus:
            self.assertLessEqual(len(s["spec1"]), MAXLEN)
        # 完整值仍在 source_spec 可溯源
        joined = " ".join(s["source_spec"] for s in skus)
        self.assertIn("爱心磁吸支架", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
