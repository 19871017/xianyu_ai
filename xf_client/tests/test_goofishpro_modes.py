"""闲管家普通/鱼小铺双模式分流 回归测试。

背景：
    闲管家普通模式(免费)不支持多规格；鱼小铺多规格(付费)需开通后才能打开
    「添加多规格深库存」弹窗。系统据此分流为两个上架渠道：
      - GoofishProLister(mode="normal")：普通单规格（已实测可用）。
      - GoofishProLister(mode="shop")  ：鱼小铺多规格（开通后实测补全 DOM）。

    本测试在无浏览器环境下用桩对象验证分流契约：
      - mode 解析（非法值回退 normal）。
      - 未就绪时安全返回。
      - shop 模式能力检测不通过时返回明确提示，不盲填表单。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.goofishpro_lister import GoofishProLister


class TestModeParsing(unittest.TestCase):
    def test_default_normal(self):
        self.assertEqual(GoofishProLister().mode, "normal")

    def test_shop_mode(self):
        self.assertEqual(GoofishProLister(mode="shop").mode, "shop")

    def test_invalid_mode_falls_back(self):
        self.assertEqual(GoofishProLister(mode="xxx").mode, "normal")


class TestFillDispatch(unittest.TestCase):
    def test_no_tab_returns_safe(self):
        # 浏览器未就绪时两模式都应安全返回错误，不抛异常。
        n = GoofishProLister(mode="normal").fill_product({"title": "x"})
        self.assertFalse(n["ok"])
        self.assertIn("浏览器未就绪", n["error"])

        s = GoofishProLister(mode="shop").fill_product({"title": "x"})
        self.assertFalse(s["ok"])
        self.assertEqual(s.get("mode"), "shop")
        self.assertIn("浏览器未就绪", s["error"])


class _FakeTab:
    """最小桩：_goto_publish/_wait_form 走通，能力检测返回未开通。"""
    def __init__(self, body_text="请先升级闲鱼号为鱼小铺"):
        self.body_text = body_text

    def get(self, *a, **k):
        pass

    def run_js(self, js, *args):
        # _wait_form: 统计输入框数量 → 给足。
        if "el-input__inner" in js:
            return 5
        # _detect_shop_capability find_js → 找到入口
        if "entry" in js:
            return '{"entry": "添加多规格深库存"}'
        # click_js
        if "btns[i].click()" in js:
            return True
        # check_js → 未开通（needUpgrade=true）
        if "needUpgrade" in js:
            return '{"needUpgrade": true, "hasSpecEditor": false}'
        return None


class TestShopCapabilityGate(unittest.TestCase):
    def test_shop_blocked_when_not_opened(self):
        lister = GoofishProLister(mode="shop")
        lister.tab = _FakeTab()
        res = lister.fill_product({"title": "多规格商品",
                                   "sku_list": [{"spec1": "红", "price": 5},
                                                {"spec1": "蓝", "price": 6}]})
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "shop")
        self.assertFalse(res["shop_capability"]["enabled"])
        self.assertIn("鱼小铺", res["error"])
        # 关键：未开通时给出改用闲鱼官方的建议，而非盲填。
        self.assertIn("闲鱼官方", res["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
