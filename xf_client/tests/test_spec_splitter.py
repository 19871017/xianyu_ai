"""按主规格拆单 spec_splitter 回归测试（纯逻辑，离线可重复）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.spec_splitter import (
    split_by_primary_spec, count_cartesian_gaps,
)


def _dual_item():
    """2 色 × 机型，含笛卡尔空缺（红只有 A/B，蓝只有 A）。"""
    return {
        "db_id": 7,
        "xianyu_item_id": "old123",
        "status": "listed_xianyu",
        "title": "手机壳",
        "main_images": ["m0.jpg"],
        "sku_list": [
            {"spec1": "红", "spec2": "iPhoneA", "price": 20, "stock": 5,
             "sku_image": "red.jpg", "sku_attrs": {"颜色": "红", "适用型号": "iPhoneA"}},
            {"spec1": "红", "spec2": "iPhoneB", "price": 22, "stock": 3,
             "sku_image": "red.jpg", "sku_attrs": {"颜色": "红", "适用型号": "iPhoneB"}},
            {"spec1": "蓝", "spec2": "iPhoneA", "price": 25, "stock": 8,
             "sku_image": "blue.jpg", "sku_attrs": {"颜色": "蓝", "适用型号": "iPhoneA"}},
        ],
    }


class TestCountGaps(unittest.TestCase):
    def test_gaps(self):
        g = count_cartesian_gaps(_dual_item())
        # 2 色 × 2 机型 = 4 笛卡尔，真实 3 → 1 空缺
        self.assertEqual(g["axis1"], 2)
        self.assertEqual(g["axis2"], 2)
        self.assertEqual(g["real"], 3)
        self.assertEqual(g["cartesian"], 4)
        self.assertEqual(g["gaps"], 1)

    def test_single_axis_no_gaps(self):
        item = {"sku_list": [{"spec1": "红", "spec2": ""}, {"spec1": "蓝", "spec2": ""}]}
        g = count_cartesian_gaps(item)
        self.assertEqual(g["gaps"], 0)
        self.assertEqual(g["cartesian"], 0)


class TestSplit(unittest.TestCase):
    def test_splits_into_children_no_gaps(self):
        children = split_by_primary_spec(_dual_item())
        # 颜色(2) <= 机型(2)，auto 选 spec1 颜色拆 → 2 个子商品
        self.assertEqual(len(children), 2)
        total = sum(len(c["sku_list"]) for c in children)
        self.assertEqual(total, 3)  # 无空缺，子 SKU 总数 = 真实组合数

    def test_children_are_single_axis(self):
        children = split_by_primary_spec(_dual_item())
        for c in children:
            for sku in c["sku_list"]:
                self.assertEqual(sku["spec2"], "")
                self.assertTrue(sku["spec1"])

    def test_child_metadata_reset(self):
        children = split_by_primary_spec(_dual_item())
        for c in children:
            self.assertNotIn("db_id", c)
            self.assertEqual(c["xianyu_item_id"], "")
            self.assertEqual(c["status"], "collected")
            self.assertEqual(c["split_from"], 7)
            self.assertTrue(c["split_spec_value"])

    def test_title_carries_spec(self):
        children = split_by_primary_spec(_dual_item())
        titles = [c["title"] for c in children]
        self.assertTrue(any("红" in t for t in titles))
        self.assertTrue(any("蓝" in t for t in titles))

    def test_price_backfilled_to_group_min(self):
        children = split_by_primary_spec(_dual_item())
        red = next(c for c in children if c["split_spec_value"] == "红")
        self.assertEqual(red["price"], 20)  # 红组 min(20,22)

    def test_secondary_attrs_drop_primary(self):
        children = split_by_primary_spec(_dual_item())
        red = next(c for c in children if c["split_spec_value"] == "红")
        for sku in red["sku_list"]:
            self.assertNotIn("颜色", sku["sku_attrs"])
            self.assertIn("适用型号", sku["sku_attrs"])

    def test_force_axis_spec2(self):
        children = split_by_primary_spec(_dual_item(), split_axis="spec2")
        # 按机型拆 → 2 个机型(iPhoneA/iPhoneB)
        self.assertEqual(len(children), 2)

    def test_single_axis_returns_as_is(self):
        item = {"title": "T", "sku_list": [
            {"spec1": "红", "spec2": "", "price": 9},
            {"spec1": "蓝", "spec2": "", "price": 9},
        ]}
        out = split_by_primary_spec(item)
        self.assertEqual(len(out), 1)

    def test_no_sku_returns_as_is(self):
        out = split_by_primary_spec({"title": "T", "sku_list": []})
        self.assertEqual(len(out), 1)

    def test_max_children_caps(self):
        skus = [{"spec1": f"c{i}", "spec2": "A", "price": 9} for i in range(10)]
        out = split_by_primary_spec({"title": "T", "sku_list": skus}, max_children=3)
        self.assertLessEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
