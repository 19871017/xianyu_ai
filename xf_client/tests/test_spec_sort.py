"""机型规格标准排序 回归测试（纯逻辑，离线可重复）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.spec_sort import (
    iphone_model_sort_key, is_iphone_model_axis, sort_skus_by_spec,
)


class TestIsModelAxis(unittest.TestCase):
    def test_iphone_models_detected(self):
        self.assertTrue(is_iphone_model_axis(["13promax", "14", "15pro"]))

    def test_colors_not_model_axis(self):
        self.assertFalse(is_iphone_model_axis(["红色", "蓝色", "黑色"]))

    def test_sizes_not_model_axis(self):
        self.assertFalse(is_iphone_model_axis(["S", "M", "L"]))

    def test_empty_not_model(self):
        self.assertFalse(is_iphone_model_axis([]))


class TestModelSortKey(unittest.TestCase):
    def test_generation_ascending(self):
        vals = ["14", "12", "13", "11"]
        self.assertEqual(sorted(vals, key=iphone_model_sort_key), ["11", "12", "13", "14"])

    def test_variant_order_within_generation(self):
        # 同代：标准 < pro < promax
        vals = ["13promax", "13", "13pro"]
        self.assertEqual(sorted(vals, key=iphone_model_sort_key), ["13", "13pro", "13promax"])

    def test_full_range_sorted(self):
        vals = ["16promax", "11", "13", "12pro", "15", "14promax"]
        out = sorted(vals, key=iphone_model_sort_key)
        self.assertEqual(out, ["11", "12pro", "13", "14promax", "15", "16promax"])

    def test_mini_before_pro(self):
        vals = ["13pro", "13mini", "13"]
        self.assertEqual(sorted(vals, key=iphone_model_sort_key), ["13", "13mini", "13pro"])


class TestSortSkus(unittest.TestCase):
    def test_sorts_only_model_axis(self):
        skus = [
            {"spec1": "14", "price": 10},
            {"spec1": "12", "price": 11},
            {"spec1": "13", "price": 12},
        ]
        out = sort_skus_by_spec(skus, "spec1")
        self.assertEqual([s["spec1"] for s in out], ["12", "13", "14"])

    def test_non_model_axis_unchanged(self):
        skus = [{"spec1": "红色"}, {"spec1": "蓝色"}, {"spec1": "黑色"}]
        out = sort_skus_by_spec(skus, "spec1")
        # 非机型轴保持原序，不强排。
        self.assertEqual([s["spec1"] for s in out], ["红色", "蓝色", "黑色"])

    def test_preserves_other_fields(self):
        skus = [{"spec1": "13", "price": 9.9, "stock": 5}, {"spec1": "12", "price": 8.8, "stock": 3}]
        out = sort_skus_by_spec(skus, "spec1")
        self.assertEqual(out[0]["spec1"], "12")
        self.assertEqual(out[0]["price"], 8.8)
        self.assertEqual(out[0]["stock"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
