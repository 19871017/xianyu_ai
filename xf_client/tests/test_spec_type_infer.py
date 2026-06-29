"""闲鱼规格类型推断回归测试。

背景（根因）：
    闲鱼发布页「规格类型」是只读固定下拉（颜色/尺码/容量/份数/大小/高度/总量），
    不可自定义。早期 _infer_spec_type 只按规格值文本反推关键词，且按关键词累计
    次数打分，导致「黑色套装」里 套+装 双命中份数压过颜色 → 误判「份数」。

    修复（三层兜底）：
      1) 优先用采集保留的原始类型名（sku_attrs 的 key）映射闲鱼 7 项（最权威）。
      2) 无原始名时关键词反推：每个规格值归给命中的「最长」关键词所属类型，
         再按命中规格值个数打分，平局按选项顺序（颜色优先）。
      3) 都不命中回退「颜色」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.xianyu_lister import XianyuLister


class TestMapSpecName(unittest.TestCase):
    """原始类型名 → 闲鱼固定 7 项 映射（最权威路径）。"""

    def test_exact_match(self):
        self.assertEqual(XianyuLister._map_spec_name("颜色"), "颜色")
        self.assertEqual(XianyuLister._map_spec_name("尺码"), "尺码")

    def test_synonym_map(self):
        # 闲鱼无「机型/套装」选项，映射到语义最接近项。
        self.assertEqual(XianyuLister._map_spec_name("机型"), "颜色")
        self.assertEqual(XianyuLister._map_spec_name("套装"), "份数")
        self.assertEqual(XianyuLister._map_spec_name("重量"), "容量")

    def test_contains_match(self):
        # 「颜色分类」含「颜色」。
        self.assertEqual(XianyuLister._map_spec_name("颜色分类"), "颜色")

    def test_unknown_returns_empty(self):
        # 「材质」不含任何已知映射词 → 空串（交给关键词反推兜底）。
        self.assertEqual(XianyuLister._map_spec_name("材质"), "")
        self.assertEqual(XianyuLister._map_spec_name(""), "")


class TestInferSpecType(unittest.TestCase):
    """推断主入口：原始名优先，文本反推兜底。"""

    def test_raw_name_wins_over_text(self):
        # 核心回归：套装色值带原始名「颜色」时必须判颜色，不能被「份数」抢。
        vals = ["黑色套装（不含发箍）", "黑色套装+L25发箍"]
        self.assertEqual(XianyuLister._infer_spec_type(vals, "颜色"), "颜色")

    def test_fallback_color_for_suit(self):
        # 无原始名时，改进打分也应判颜色（每个值都含「色」）。
        vals = ["黑色套装（不含发箍）", "黑色套装+L25发箍"]
        self.assertEqual(XianyuLister._infer_spec_type(vals), "颜色")

    def test_fallback_capacity_ml(self):
        # 「100ml」最长关键词 ml(容量) 应胜过 m/l(尺码)。
        self.assertEqual(XianyuLister._infer_spec_type(["100ml", "200ml", "500ml"]), "容量")

    def test_fallback_capacity_weight(self):
        self.assertEqual(XianyuLister._infer_spec_type(["500g", "1kg"]), "容量")

    def test_fallback_size(self):
        self.assertEqual(XianyuLister._infer_spec_type(["S", "M", "L", "XL"]), "尺码")

    def test_fallback_count(self):
        self.assertEqual(XianyuLister._infer_spec_type(["3件套", "5件套", "单件"]), "份数")

    def test_fallback_pure_color(self):
        self.assertEqual(XianyuLister._infer_spec_type(["红色", "蓝色", "黑色"]), "颜色")

    def test_empty_returns_color(self):
        self.assertEqual(XianyuLister._infer_spec_type([]), "颜色")


class TestSpecAxisNames(unittest.TestCase):
    """从 sku_attrs 保序取两个轴的原始类型名。"""

    def test_extract_ordered_names(self):
        sku_list = [
            {"spec1": "黑色", "spec2": "均码",
             "sku_attrs": {"颜色": "黑色", "尺码": "均码"}},
        ]
        self.assertEqual(XianyuLister._spec_axis_names(sku_list), ("颜色", "尺码"))

    def test_single_axis(self):
        sku_list = [{"spec1": "红色", "sku_attrs": {"颜色": "红色"}}]
        self.assertEqual(XianyuLister._spec_axis_names(sku_list), ("颜色", ""))

    def test_no_attrs_returns_empty(self):
        self.assertEqual(XianyuLister._spec_axis_names([{"spec1": "红色"}]), ("", ""))
        self.assertEqual(XianyuLister._spec_axis_names([]), ("", ""))


class TestSpecTypeExclude(unittest.TestCase):
    """exclude：避免两个规格轴推断成同一类型（闲鱼同一商品两轴不可重名）。"""

    def test_excluded_mapping_falls_through(self):
        # 「适用型号」映射本应为「颜色」，但颜色已被第一轴占用，应顺延。
        t = XianyuLister._infer_spec_type(["17promax", "16promax"], "适用型号", exclude=("颜色",))
        self.assertNotEqual(t, "颜色")
        self.assertIn(t, ["尺码", "容量", "份数", "大小", "高度", "总量"])

    def test_excluded_keyword_falls_through(self):
        # 颜色值但颜色已占用：关键词反推也要避开「颜色」。
        t = XianyuLister._infer_spec_type(["红色", "蓝色"], "", exclude=("颜色",))
        self.assertNotEqual(t, "颜色")

    def test_no_exclude_keeps_old_behavior(self):
        # 不传 exclude 时行为不变。
        self.assertEqual(XianyuLister._infer_spec_type(["红色", "蓝色"]), "颜色")
        self.assertEqual(XianyuLister._infer_spec_type(["17promax"], "机型"), "颜色")

    def test_first_axis_color_second_axis_not_color(self):
        # 双轴手机壳场景：第一轴颜色，第二轴(机型)排除颜色后不重名。
        t1 = XianyuLister._infer_spec_type(["蓝边磁吸", "粉边磁吸"], "颜色")
        t2 = XianyuLister._infer_spec_type(["17promax", "16promax"], "适用型号", exclude=(t1,))
        self.assertEqual(t1, "颜色")
        self.assertNotEqual(t1, t2)



if __name__ == "__main__":
    unittest.main(verbosity=2)
