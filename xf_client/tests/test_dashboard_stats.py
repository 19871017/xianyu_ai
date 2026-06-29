"""经营概览统计 compute_dashboard 回归测试（纯逻辑，离线）。

覆盖：商品按状态/平台/多规格、关注浏览合计；订单成交额/匹配率；
利润加价率与毛利；风险计数。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.dashboard_stats import compute_dashboard


class TestComputeDashboard(unittest.TestCase):
    def setUp(self):
        self.products = [
            {  # 已上架闲鱼，多规格，源价50→售价80（加价60%，毛利30）
                "status": "listed_xianyu", "platform": "1688",
                "original_price": "50", "new_price": "80", "wants": "10", "views": "100",
                "sku_list": [{"spec1": "红"}, {"spec1": "蓝"}],
            },
            {  # 已上架闲管家，单规格，源价20→售价30（加价50%，毛利10）
                "status": "listed_goofishpro", "platform": "taobao",
                "original_price": "20", "new_price": "30", "wants": "5", "views": "50",
                "sku_list": [{"spec1": "默认"}],
            },
            {  # 待处理，不计入已上架与利润
                "status": "collected", "platform": "1688",
                "original_price": "15", "new_price": "", "wants": "0", "views": "0",
                "sku_list": [{"spec1": "默认"}],
            },
        ]
        self.orders = [
            {"order_status": "completed", "order_amount": "80", "match_status": "matched"},
            {"order_status": "pending", "order_amount": "30", "match_status": "unmatched"},
        ]
        self.rechecks = [
            {"level": "critical"}, {"level": "warn"}, {"level": "none"},
        ]

    def test_product_counts(self):
        d = compute_dashboard(self.products, self.orders, self.rechecks)
        self.assertEqual(d["products"]["total"], 3)
        self.assertEqual(d["products"]["listed"], 2)
        self.assertEqual(d["products"]["multi_sku"], 1)
        self.assertEqual(d["products"]["total_wants"], 15)
        self.assertEqual(d["products"]["total_views"], 150)

    def test_by_platform_sorted(self):
        d = compute_dashboard(self.products, self.orders, self.rechecks)
        bp = d["products"]["by_platform"]
        # 1688 出现 2 次应排第一。
        self.assertEqual(bp[0]["key"], "1688")
        self.assertEqual(bp[0]["count"], 2)
        self.assertEqual(bp[0]["label"], "1688")

    def test_by_status_labels(self):
        d = compute_dashboard(self.products, self.orders, self.rechecks)
        labels = {x["key"]: x["label"] for x in d["products"]["by_status"]}
        self.assertEqual(labels["listed_xianyu"], "已上架闲鱼")
        self.assertEqual(labels["collected"], "待处理")

    def test_orders(self):
        d = compute_dashboard(self.products, self.orders, self.rechecks)
        self.assertEqual(d["orders"]["total"], 2)
        self.assertEqual(d["orders"]["revenue"], 110.0)
        self.assertEqual(d["orders"]["matched"], 1)
        self.assertEqual(d["orders"]["match_rate"], 50.0)

    def test_profit(self):
        d = compute_dashboard(self.products, self.orders, self.rechecks)
        # 两个已上架：加价率 60% 和 50% → 均值 55；毛利 30+10=40，均值 20。
        self.assertEqual(d["profit"]["sample"], 2)
        self.assertEqual(d["profit"]["avg_markup_pct"], 55.0)
        self.assertEqual(d["profit"]["total_gross_margin"], 40.0)
        self.assertEqual(d["profit"]["avg_gross_margin"], 20.0)

    def test_risk(self):
        d = compute_dashboard(self.products, self.orders, self.rechecks)
        self.assertEqual(d["risk"]["critical"], 1)
        self.assertEqual(d["risk"]["warn"], 1)

    def test_empty_inputs(self):
        d = compute_dashboard()
        self.assertEqual(d["products"]["total"], 0)
        self.assertEqual(d["orders"]["revenue"], 0.0)
        self.assertEqual(d["profit"]["sample"], 0)
        self.assertEqual(d["risk"]["critical"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
