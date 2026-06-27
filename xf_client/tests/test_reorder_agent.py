"""1688 半自动代采纯逻辑单测（无浏览器依赖）。

覆盖：offer id 提取、offer url 构造、代采计划安全校验、规格 token 提取、
以及关键安全护栏——禁止支付/提交类按钮。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.reorder_agent import (
    extract_offer_id,
    build_offer_url,
    validate_reorder_plan,
    pick_sku_spec_tokens,
    is_forbidden_button,
    FORBIDDEN_BUTTON_TEXTS,
)


def _good_plan(**over):
    plan = {
        "ok": True,
        "source_platform": "1688",
        "source_url": "https://detail.1688.com/offer/723383755110.html",
        "source_sku_id": "5196460270576",
        "spec_score": 1.0,
        "quantity": 2,
        "ship_to": {"name": "王宇", "phone": "13800000000", "address": "上海市浦东新区xx路1号"},
        "sku": {"spec1": "红色", "spec2": "均码", "source_sku_id": "5196460270576"},
    }
    plan.update(over)
    return plan


class TestExtractOfferId(unittest.TestCase):
    def test_offer_path(self):
        self.assertEqual(
            extract_offer_id("https://detail.1688.com/offer/723383755110.html"),
            "723383755110",
        )

    def test_offer_id_param(self):
        self.assertEqual(
            extract_offer_id("https://detail.m.1688.com/page/index.html?offerId=123456789"),
            "123456789",
        )

    def test_empty(self):
        self.assertEqual(extract_offer_id(""), "")
        self.assertEqual(extract_offer_id("https://example.com/foo"), "")


class TestBuildOfferUrl(unittest.TestCase):
    def test_keep_1688_url(self):
        url = build_offer_url(_good_plan())
        self.assertIn("723383755110", url)
        self.assertIn("1688.com", url)

    def test_reject_other_platform(self):
        self.assertEqual(build_offer_url(_good_plan(source_platform="taobao")), "")

    def test_build_from_id_when_not_1688_domain(self):
        plan = _good_plan(source_platform="", source_url="https://x.com/offer/999999999999.html")
        url = build_offer_url(plan)
        self.assertIn("999999999999", url)
        self.assertIn("detail.1688.com", url)


class TestValidatePlan(unittest.TestCase):
    def test_good_plan_passes(self):
        res = validate_reorder_plan(_good_plan())
        self.assertTrue(res["ok"], res["reasons"])
        self.assertEqual(res["quantity"], 2)

    def test_missing_address_fails(self):
        plan = _good_plan(ship_to={"name": "王宇", "address": ""})
        res = validate_reorder_plan(plan)
        self.assertFalse(res["ok"])
        self.assertTrue(any("地址" in r for r in res["reasons"]))

    def test_fuzzy_spec_blocks(self):
        res = validate_reorder_plan(_good_plan(spec_score=0.7))
        self.assertFalse(res["ok"])
        self.assertTrue(any("规格" in r for r in res["reasons"]))

    def test_other_platform_blocks(self):
        res = validate_reorder_plan(_good_plan(source_platform="pdd"))
        self.assertFalse(res["ok"])

    def test_bad_quantity(self):
        res = validate_reorder_plan(_good_plan(quantity=0))
        self.assertFalse(res["ok"])


class TestPickTokens(unittest.TestCase):
    def test_two_specs(self):
        self.assertEqual(pick_sku_spec_tokens(_good_plan()), ["红色", "均码"])

    def test_skip_default(self):
        plan = _good_plan(sku={"spec1": "默认", "spec2": ""})
        self.assertEqual(pick_sku_spec_tokens(plan), [])


class TestSafetyGuard(unittest.TestCase):
    def test_all_forbidden_texts_blocked(self):
        for t in FORBIDDEN_BUTTON_TEXTS:
            self.assertTrue(is_forbidden_button(t), t)

    def test_pay_button_blocked_with_surrounding_text(self):
        self.assertTrue(is_forbidden_button("立即支付 ¥31.20"))
        self.assertTrue(is_forbidden_button("提交订单"))

    def test_cart_button_not_forbidden(self):
        self.assertFalse(is_forbidden_button("加入进货车"))
        self.assertFalse(is_forbidden_button("立即订购"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
