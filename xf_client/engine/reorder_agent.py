"""1688 半自动代采执行器（停在下单确认页，绝不自动支付）。

设计原则（安全第一）：
- 本模块只做到「选规格 + 填数量 + 填收货地址 + 走到下单确认页 / 加入购物车」，
  **绝不点击「提交订单」「立即支付」等不可逆按钮**。最终支付由人工确认。
- 任何疑似支付/扣款按钮一律不点；找不到加购/确认入口时停下并回传原因。

职责分两层：
1) 纯逻辑（可单测，无浏览器依赖）：
   - build_offer_url(plan)            由代采计划构造规范的 1688 offer 链接。
   - validate_reorder_plan(plan)      代采前安全校验（源平台/链接/数量/规格匹配度）。
   - pick_sku_spec_tokens(plan)       从计划里取出要在详情页点选的规格名 token。
2) 浏览器动作（ReorderAgent）：
   - 走 utils.login_manager 统一登录态（1688 profile，免登录）。
   - open_offer → select_spec → set_quantity → add_to_cart / goto_confirm。
   - 全流程带「禁止支付」护栏：只点加购/去下单，停在确认页。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


# 1688 详情页 offer id 提取（与 alibaba_collector._extract_item_id 同源规则）。
_OFFER_ID_RE = (
    re.compile(r"offer/(\d+)"),
    re.compile(r"offerId=(\d+)"),
    re.compile(r"(\d{10,})"),
)

# 绝不点击的危险按钮文本（不可逆/扣款）。
FORBIDDEN_BUTTON_TEXTS = (
    "提交订单", "立即支付", "确认支付", "去支付", "立即付款",
    "确认付款", "马上支付", "支付订单",
)

# 只允许点击的安全入口文本（加购物车 / 走到确认页）。
SAFE_CART_TEXTS = ("加入进货车", "加入采购车", "加入购物车", "放入进货车")
SAFE_ORDER_TEXTS = ("立即订购", "立即下单", "去下单", "结算")


def extract_offer_id(url: str) -> str:
    """从 1688 链接中提取 offer id，提取不到返回空串。"""
    if not url:
        return ""
    for rx in _OFFER_ID_RE:
        m = rx.search(str(url))
        if m:
            return m.group(1)
    return ""


def build_offer_url(plan: dict[str, Any]) -> str:
    """由代采计划构造规范的 1688 offer 链接。

    优先用计划里的 source_url；若只有 offer id 则拼标准详情页链接。
    非 1688 源平台返回空串（本执行器仅支持 1688）。
    """
    if not isinstance(plan, dict):
        return ""
    platform = (plan.get("source_platform") or "").lower()
    url = (plan.get("source_url") or "").strip()
    if platform and platform != "1688":
        return ""
    offer_id = extract_offer_id(url)
    if not offer_id:
        return ""
    if "1688.com" not in url.lower():
        # 源链接不是 1688 域名，但能抽到 id，仍按 1688 详情页构造。
        return PLATFORM_URLS["1688"]["item"].format(offer_id=offer_id)
    return url


def validate_reorder_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """代采前安全校验。返回 {ok, reasons:[...], offer_url, quantity}。

    不通过时 ok=False 并给出每条原因，调用方据此阻止下单或提示人工。
    """
    reasons: list[str] = []
    plan = plan or {}
    platform = (plan.get("source_platform") or "").lower()
    if platform and platform != "1688":
        reasons.append(f"暂仅支持 1688 代采，当前源平台：{platform or '未知'}")

    offer_url = build_offer_url(plan)
    if not offer_url:
        reasons.append("无法得到有效的 1688 源商品链接")

    raw_qty = plan.get("quantity", 1)
    try:
        qty = int(raw_qty)
    except Exception:
        qty = 0
    if qty < 1:
        reasons.append("下单数量无效")

    ship = plan.get("ship_to") or {}
    if not (ship.get("address") or "").strip():
        reasons.append("缺少买家收货地址")
    if not (ship.get("name") or "").strip():
        reasons.append("缺少收货人姓名")

    score = plan.get("spec_score")
    if score is not None and score < 0.99:
        reasons.append("规格非精确匹配，下单前必须人工核对规格")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "offer_url": offer_url,
        "quantity": qty,
    }


def pick_sku_spec_tokens(plan: dict[str, Any]) -> list[str]:
    """从计划里取出要在详情页点选的规格名 token（spec1/spec2）。"""
    sku = (plan or {}).get("sku") or {}
    tokens = []
    for key in ("spec1", "spec2"):
        v = (sku.get(key) or "").strip()
        if v and v != "默认":
            tokens.append(v)
    return tokens


def is_forbidden_button(text: str) -> bool:
    """判断某按钮文本是否属于禁止点击的支付/提交类。"""
    t = (text or "").strip()
    return any(bad in t for bad in FORBIDDEN_BUTTON_TEXTS)


# ─────────────────────── 浏览器动作（半自动） ───────────────────────

class ReorderAgent:
    """1688 半自动代采执行器（停在确认页，绝不支付）。"""

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.log = on_log or (lambda m: None)
        self.browser = None
        self.tab = None

    def open(self, timeout: int = 600) -> bool:
        res = ensure_login("1688", on_log=self.log, timeout=timeout)
        if not res["ok"]:
            self.log(f"登录失败: {res.get('error')}")
            return False
        self.browser = res["browser"]
        self.tab = res["tab"]
        return True

    def close(self):
        if self.browser:
            try:
                self.browser.quit()
            except Exception:
                pass
            self.browser = None
            self.tab = None

    def prepare_reorder(self, plan: dict[str, Any]) -> dict[str, Any]:
        """执行半自动代采：打开 offer → 选规格 → 填数量 → 停在加购/确认页。

        返回 {ok, stage, offer_url, selected_specs, error, paid:False}
        无论如何 paid 恒为 False —— 本执行器从不支付。
        """
        out = {
            "ok": False, "stage": "init", "offer_url": "",
            "selected_specs": [], "error": "", "paid": False,
        }
        check = validate_reorder_plan(plan)
        if not check["ok"]:
            out["error"] = "；".join(check["reasons"])
            out["stage"] = "validate"
            return out
        out["offer_url"] = check["offer_url"]

        if not self.tab:
            out["error"] = "浏览器未就绪，请先 open()"
            return out

        try:
            self.tab.get(check["offer_url"])
            time.sleep(6)
            out["stage"] = "opened"

            tokens = pick_sku_spec_tokens(plan)
            selected = self._select_specs(tokens)
            out["selected_specs"] = selected
            out["stage"] = "spec_selected"

            qty = check["quantity"]
            self._set_quantity(qty)
            out["stage"] = "qty_set"

            # 只走到加购/确认入口，绝不支付。
            entered = self._click_safe_entry()
            out["stage"] = "confirm_page" if entered else "stopped_before_entry"
            out["ok"] = True
            if entered:
                self.log("✅ 已停在下单确认页/进货车，请人工核对规格、数量、地址后手动支付。")
            else:
                self.log("⚠️ 未找到安全的加购/下单入口，已停下，请人工在浏览器中操作。")
            return out
        except Exception as e:
            out["error"] = str(e)
            return out

    def _select_specs(self, tokens: list[str]) -> list[str]:
        """在详情页按规格名点选规格（原生点击触发框架事件）。"""
        selected: list[str] = []
        for tok in tokens:
            ok = False
            try:
                for el in self.tab.eles("css:a,span,div,button,li"):
                    try:
                        txt = (el.text or "").strip()
                    except Exception:
                        continue
                    if txt and txt == tok:
                        try:
                            el.click(by_js=False)
                            ok = True
                            time.sleep(0.8)
                            break
                        except Exception:
                            continue
            except Exception:
                pass
            if ok:
                selected.append(tok)
                self.log(f"已选规格：{tok}")
            else:
                self.log(f"未能自动选中规格：{tok}（请人工确认）")
        return selected

    def _set_quantity(self, qty: int):
        """设置采购数量（找数量输入框，原生填值）。"""
        if qty < 1:
            return
        try:
            ok = self.tab.run_js(
                r"""
                var set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                var inputs = document.querySelectorAll('input');
                for (var i=0;i<inputs.length;i++){
                  var el = inputs[i];
                  var cls = (el.className||'') + ' ' + (el.name||'') + ' ' + (el.id||'');
                  if(/(amount|quantity|count|num|buy)/i.test(cls) || el.type==='number'){
                    set.call(el, String(arguments[0]));
                    el.dispatchEvent(new Event('input',{bubbles:true}));
                    el.dispatchEvent(new Event('change',{bubbles:true}));
                    return true;
                  }
                }
                return false;
                """,
                qty,
            )
            if ok:
                self.log(f"已设置采购数量：{qty}")
        except Exception:
            pass

    def _click_safe_entry(self) -> bool:
        """只点加购/去下单按钮，绝不点支付/提交订单类按钮。"""
        safe_texts = SAFE_CART_TEXTS + SAFE_ORDER_TEXTS
        try:
            for el in self.tab.eles("css:a,button,span,div"):
                try:
                    txt = (el.text or "").strip()
                except Exception:
                    continue
                if not txt:
                    continue
                # 安全护栏：禁止点击的支付/提交类按钮直接跳过。
                if is_forbidden_button(txt):
                    continue
                if any(s in txt for s in safe_texts):
                    try:
                        el.click(by_js=False)
                        time.sleep(2)
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False
