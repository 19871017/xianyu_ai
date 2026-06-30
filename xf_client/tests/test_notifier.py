"""新订单提醒核心逻辑 回归测试。

只覆盖纯逻辑：新订单识别 detect_new_orders 与偏好读写，
朗读/系统通知（依赖系统命令）不在单测内触发。
全部把落盘路径重定向到临时目录，绝不污染真实用户文件。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import notifier


class TestNewOrderDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._seen = notifier._SEEN_PATH
        self._pref = notifier._PREF_PATH
        notifier._SEEN_PATH = os.path.join(self.tmp.name, "seen.json")
        notifier._PREF_PATH = os.path.join(self.tmp.name, "pref.json")

    def tearDown(self):
        notifier._SEEN_PATH = self._seen
        notifier._PREF_PATH = self._pref
        self.tmp.cleanup()

    def _order(self, **kw):
        base = {"xianyu_item_id": "1", "buyer_spec": "黑色", "order_amount": "9.9",
                "buyer_name": "张三", "title": "耳机"}
        base.update(kw)
        return base

    def test_first_time_is_baseline_no_new(self):
        orders = [self._order(xianyu_item_id="a"), self._order(xianyu_item_id="b")]
        self.assertEqual(notifier.detect_new_orders(orders), 0)

    def test_second_time_detects_only_new(self):
        notifier.detect_new_orders([self._order(xianyu_item_id="a")])  # 基线
        n = notifier.detect_new_orders([
            self._order(xianyu_item_id="a"),
            self._order(xianyu_item_id="b"),
            self._order(xianyu_item_id="c"),
        ])
        self.assertEqual(n, 2)

    def test_repeated_same_orders_no_new(self):
        notifier.detect_new_orders([self._order(xianyu_item_id="a")])
        n = notifier.detect_new_orders([self._order(xianyu_item_id="a")])
        self.assertEqual(n, 0)

    def test_prefers_platform_order_id(self):
        k1 = notifier._order_key({"platform_order_id": "X1"})
        k2 = notifier._order_key({"platform_order_id": "X1", "buyer_name": "改了名"})
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("id:"))

    def test_hash_key_changes_with_fields(self):
        k1 = notifier._order_key(self._order(buyer_spec="黑色"))
        k2 = notifier._order_key(self._order(buyer_spec="白色"))
        self.assertNotEqual(k1, k2)

    def test_seen_file_trimmed(self):
        big = [self._order(xianyu_item_id=str(i)) for i in range(notifier._SEEN_MAX + 50)]
        notifier.detect_new_orders(big)
        with open(notifier._SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertLessEqual(len(data), notifier._SEEN_MAX)

    def test_ignores_non_dict(self):
        self.assertEqual(notifier.detect_new_orders([None, "x", 123]), 0)


class TestVoicePref(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._pref = notifier._PREF_PATH
        notifier._PREF_PATH = os.path.join(self.tmp.name, "pref.json")

    def tearDown(self):
        notifier._PREF_PATH = self._pref
        self.tmp.cleanup()

    def test_default_enabled(self):
        self.assertTrue(notifier.is_voice_enabled())

    def test_toggle_persists(self):
        notifier.set_voice_enabled(False)
        self.assertFalse(notifier.is_voice_enabled())
        notifier.set_voice_enabled(True)
        self.assertTrue(notifier.is_voice_enabled())

    def test_alert_zero_is_noop(self):
        # count<=0 不应抛异常
        notifier.alert_new_orders(0)


if __name__ == "__main__":
    unittest.main()
