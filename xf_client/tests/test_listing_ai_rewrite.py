"""上架前 AI 文案改写开关 回归测试（ListingWorker._apply_ai_rewrite）。

依赖 PyQt6（ListingWorker 是 QThread）；环境无 PyQt6 时自动跳过，
不破坏测试套件「不依赖 GUI」的约定。用假 writer 注入，不碰网络/浏览器。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication
    from ui.listing_tab import ListingWorker
    _HAS_QT = True
except Exception:
    _HAS_QT = False


class _FakeOK:
    def _is_configured(self):
        return True

    def rewrite(self, title, desc, price):
        return {
            "success": True,
            "title": "韩系磁吸iPhone壳",
            "description": "全新现货，多机型可选",
            "tags": ["手机壳", "磁吸"],
        }


class _FakeNotConfigured:
    def _is_configured(self):
        return False

    def rewrite(self, *a):
        raise AssertionError("未配置时不应调用 rewrite")


class _FakeFail:
    def _is_configured(self):
        return True

    def rewrite(self, *a):
        return {"success": False, "error": "boom"}


@unittest.skipUnless(_HAS_QT, "PyQt6 不可用，跳过 GUI 相关测试")
class TestListingAIRewrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def _worker(self, writer):
        w = ListingWorker([], "fixed", 0.0, ai_rewrite=True)
        w._ai_writer = writer
        return w

    def test_success_writes_back_fields(self):
        w = self._worker(_FakeOK())
        item = {"title": "原始堆砌标题xxx", "description": "老描述", "new_price": "21.9"}
        w._apply_ai_rewrite(item, lambda m: None)
        self.assertEqual(item["title"], "韩系磁吸iPhone壳")
        self.assertEqual(item["description"], "全新现货，多机型可选")
        self.assertEqual(item["tags"], ["手机壳", "磁吸"])
        # 原描述保留以便追溯
        self.assertEqual(item["original_description"], "老描述")
        self.assertEqual(item["ai_title"], "韩系磁吸iPhone壳")

    def test_not_configured_keeps_original(self):
        w = self._worker(_FakeNotConfigured())
        item = {"title": "保持不变", "description": "d"}
        w._apply_ai_rewrite(item, lambda m: None)
        self.assertEqual(item["title"], "保持不变")
        self.assertEqual(item["description"], "d")

    def test_rewrite_fail_keeps_original(self):
        w = self._worker(_FakeFail())
        item = {"title": "失败也不变", "description": "d"}
        w._apply_ai_rewrite(item, lambda m: None)
        self.assertEqual(item["title"], "失败也不变")
        self.assertEqual(item["description"], "d")


if __name__ == "__main__":
    unittest.main(verbosity=2)
