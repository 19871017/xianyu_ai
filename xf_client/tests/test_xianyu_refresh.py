"""闲鱼擦亮器 安全护栏 回归测试。

仅测纯函数 is_safe_button / is_forbidden_button：
确保危险按钮(下架/删除/编辑/降价)绝不被判为可点击，
擦亮类按钮被正确识别。不依赖浏览器，离线可重复。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.xianyu_refresh import is_safe_button, is_forbidden_button


class TestForbidden(unittest.TestCase):
    def test_danger_texts_forbidden(self):
        for t in ["删除", "下架", "编辑", "降价", "修改", "删除商品",
                  "下架商品", "立即降价", "一口价", "出售", "拍卖"]:
            self.assertTrue(is_forbidden_button(t), t)

    def test_refresh_not_forbidden(self):
        for t in ["擦亮", "一键擦亮", "重新擦亮"]:
            self.assertFalse(is_forbidden_button(t), t)

    def test_empty_not_forbidden(self):
        self.assertFalse(is_forbidden_button(""))
        self.assertFalse(is_forbidden_button(None))


class TestSafe(unittest.TestCase):
    def test_refresh_texts_safe(self):
        for t in ["擦亮", "一键擦亮", "重新擦亮", " 擦亮 "]:
            self.assertTrue(is_safe_button(t), t)

    def test_danger_never_safe(self):
        for t in ["删除", "下架", "编辑", "降价", "出售", "一口价"]:
            self.assertFalse(is_safe_button(t), t)

    def test_mixed_text_with_danger_word_not_safe(self):
        # 含危险词的混合文本一律不安全（护栏优先）。
        self.assertFalse(is_safe_button("擦亮并降价"))
        self.assertFalse(is_safe_button("编辑擦亮"))

    def test_irrelevant_not_safe(self):
        for t in ["", None, "查看", "分享", "更多"]:
            self.assertFalse(is_safe_button(t))


if __name__ == "__main__":
    unittest.main(verbosity=2)
