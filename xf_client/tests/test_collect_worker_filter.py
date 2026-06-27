"""CollectWorker 筛选接线层测试（离屏，不启动真实采集器/浏览器）。

验证 UI → worker → collect_filter 的端到端过滤排序接线正确，
覆盖 collect_filter 纯函数单测之外的“应用层”逻辑。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.collect_tab import CollectWorker


SAMPLE = [
    {"title": "a", "price": 5.0, "wants": "50", "views": "10"},
    {"title": "b", "price": 9.9, "wants": "1.2万", "views": "3000"},
    {"title": "c", "price": 99.0, "wants": "200", "views": "8万"},
    {"title": "d", "price": 300.0, "wants": "5", "views": "2"},
]


def _worker(filters):
    return CollectWorker("xianyu", "keyword", "kw", 10, filters=filters)


class TestWorkerFilter(unittest.TestCase):
    def test_no_filter_passthrough(self):
        w = _worker({})
        self.assertEqual(len(w._apply_filters(list(SAMPLE))), 4)

    def test_price_range(self):
        w = _worker({"min_price": 5, "max_price": 100})
        titles = [it["title"] for it in w._apply_filters(list(SAMPLE))]
        self.assertEqual(set(titles), {"a", "b", "c"})

    def test_min_sales_uses_wants_fallback(self):
        w = _worker({"min_sales": 100})
        titles = {it["title"] for it in w._apply_filters(list(SAMPLE))}
        self.assertEqual(titles, {"b", "c"})

    def test_min_views_with_wan(self):
        w = _worker({"min_views": 5000})
        titles = {it["title"] for it in w._apply_filters(list(SAMPLE))}
        self.assertEqual(titles, {"c"})

    def test_sort_price_asc(self):
        w = _worker({"sort_by": "price", "order": "asc"})
        titles = [it["title"] for it in w._apply_filters(list(SAMPLE))]
        self.assertEqual(titles, ["a", "b", "c", "d"])

    def test_combined(self):
        w = _worker({"min_price": 1, "max_price": 100, "min_wants": 100,
                     "sort_by": "wants", "order": "desc"})
        titles = [it["title"] for it in w._apply_filters(list(SAMPLE))]
        self.assertEqual(titles, ["b", "c"])

    def test_bad_filter_returns_original(self):
        w = _worker({"sort_by": "price", "order": "asc"})
        # 故意塞入非 dict，过滤函数应跳过而不崩
        data = list(SAMPLE) + ["garbage"]
        out = w._apply_filters(data)
        self.assertTrue(all(isinstance(x, dict) for x in out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
