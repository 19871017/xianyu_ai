"""拼多多 _find_goods_list 解析：商品列表识别（排除搜索热词等干扰）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.pdd_collector import PddCollector


class TestFindGoodsList(unittest.TestCase):
    def setUp(self):
        # 不触发浏览器初始化，仅复用纯解析方法
        self.c = PddCollector.__new__(PddCollector)

    def test_hot_query_string_list_not_matched(self):
        """搜索热词接口返回的字符串列表不应被误判为商品。"""
        data = {
            "items": ["耳机", "丝袜", "蓝牙耳机"],
            "hotqs": [{"q": "耳机", "tag_list": []}],
        }
        self.assertEqual(self.c._find_goods_list(data), [])

    def test_real_goods_list_matched(self):
        """含强特征字段的列表应被识别为商品列表。"""
        goods = [
            {"goods_id": "1001", "goods_name": "蓝牙耳机", "min_group_price": 5990},
            {"goods_id": "1002", "goods_name": "有线耳机", "min_group_price": 1990},
        ]
        data = {"result": {"goods_list": goods}}
        self.assertEqual(self.c._find_goods_list(data), goods)

    def test_camel_case_goods_list_matched(self):
        goods = [{"goodsId": "2001", "goodsName": "x", "minGroupPrice": 999}]
        data = {"data": {"items": goods}}
        self.assertEqual(self.c._find_goods_list(data), goods)

    def test_weak_feature_list_not_matched(self):
        """仅含 name/title 等弱特征的列表（如导航/筛选项）不应被误判。"""
        data = {"list": [{"name": "全部"}, {"name": "销量"}]}
        self.assertEqual(self.c._find_goods_list(data), [])

    def test_nested_goods_list_found(self):
        goods = [{"item_id": "3001", "title": "x", "group_price": 888}]
        data = {"a": {"b": {"c": {"searchResult": goods}}}}
        self.assertEqual(self.c._find_goods_list(data), goods)


if __name__ == "__main__":
    unittest.main()
