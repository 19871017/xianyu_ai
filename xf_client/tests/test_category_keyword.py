"""品类词提取单测：保证闲管家分类级联搜索能拿到可命中的品类词。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.product_package import extract_category_keyword, ensure_full_product_package


class TestCategoryKeyword(unittest.TestCase):
    def test_dress_from_title(self):
        self.assertEqual(
            extract_category_keyword("2026年波西米亚度假风优雅长裙大码女装连衣裙"),
            "连衣裙",
        )

    def test_shoes_from_title(self):
        self.assertEqual(
            extract_category_keyword("夏季新款男士休闲运动鞋透气跑步鞋"),
            "运动鞋",
        )

    def test_prefers_category_field(self):
        self.assertEqual(
            extract_category_keyword("随便的标题", "奢品/女装/连衣裙/连衣裙"),
            "连衣裙",
        )

    def test_fallback_tail_chinese(self):
        # 词库未命中时回退到末尾中文，且去掉"厂家"噪声
        kw = extract_category_keyword("某某某某神奇商品厂家")
        self.assertTrue(kw and "厂家" not in kw)

    def test_empty(self):
        self.assertEqual(extract_category_keyword(""), "")

    def test_package_sets_category_keyword(self):
        item = {"title": "2026新款大码女装连衣裙", "price": 20.0, "sku_list": []}
        pkg = ensure_full_product_package(item)
        self.assertEqual(pkg.get("category_keyword"), "连衣裙")


if __name__ == "__main__":
    unittest.main(verbosity=2)
