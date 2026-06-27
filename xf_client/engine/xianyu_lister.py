"""闲鱼官方(goofish.com) 商品自动上架器。

发布页 ``https://www.goofish.com/publish`` 是 React 自定义组件页面（非标准表单）：
- **描述**：``contentEditable`` DIV（placeholder「描述一下宝贝的品牌型号...」，上限 1500），
  闲鱼无独立标题字段，描述即正文（首行通常被当作标题展示）。
- **图片**：``input[type=file]``（accept png/jpg/jpeg/heic/webp，multiple，标签「添加首图」）。
- **价格**：占位为 ``0.00`` 的文本框，DOM 顺序前两个为 价格(一口价)/原价，第三个为运费。
- **服务/发货**：包邮、无需邮寄等 radio。
- **发布**：底部「发布」按钮。

设计要点：
- 登录态走 utils.login_manager（xianyu Cookie + profile），免登录。
- 默认 ``dry_run=True``：填完表单**停在「发布」前**，由人工确认后再点发布，
  避免误上架真实商品。
- React 受控组件：input 用原生 value setter + input 事件；contentEditable 用
  ``execCommand('insertText')`` 触发 React onChange。
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


PUBLISH_URL = PLATFORM_URLS["xianyu"]["publish"]

IMG_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp")


class XianyuLister:
    """闲鱼官方商品上架器（默认 dry-run，停在「发布」前）。"""

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.log = on_log or (lambda m: None)
        self.browser = None
        self.tab = None

    # ── 浏览器/登录 ────────────────────────────────────────────
    def open(self, timeout: int = 600) -> bool:
        res = ensure_login("xianyu", on_log=self.log, timeout=timeout)
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

    # ── DOM 工具 ──────────────────────────────────────────────
    def _goto_publish(self):
        self.tab.get(PUBLISH_URL)
        time.sleep(6)

    def _wait_form(self, timeout: int = 20) -> bool:
        """等待发布页渲染（出现 contentEditable 描述框或价格框）。"""
        check = r"""
        var ed = document.querySelectorAll('div[contenteditable="true"]').length;
        var pr = 0;
        document.querySelectorAll('input').forEach(function(i){
          if((i.getAttribute('placeholder')||'')==='0.00') pr++;
        });
        return (ed > 0 || pr > 0);
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.tab.run_js(check):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _fill_description(self, text: str) -> bool:
        """填写描述（contentEditable，React）。用 execCommand 触发 onChange。"""
        js = r"""
        var text = arguments[0];
        var divs = document.querySelectorAll('div[contenteditable="true"]');
        if (!divs.length) return false;
        var el = divs[0];
        el.focus();
        try {
          document.execCommand('selectAll', false, null);
          document.execCommand('insertText', false, text);
          return true;
        } catch (e) {
          el.textContent = text;
          el.dispatchEvent(new InputEvent('input', {bubbles: true, data: text}));
          return true;
        }
        """
        try:
            return bool(self.tab.run_js(js, text))
        except Exception as e:
            self.log(f"填写描述异常: {e}")
            return False

    def _price_inputs(self) -> int:
        """返回占位为 0.00 的价格输入框数量。"""
        js = r"""
        var n = 0;
        document.querySelectorAll('input').forEach(function(i){
          if((i.getAttribute('placeholder')||'')==='0.00') n++;
        });
        return n;
        """
        try:
            return int(self.tab.run_js(js) or 0)
        except Exception:
            return 0

    def _fill_price_by_index(self, idx: int, value: str) -> bool:
        """按 DOM 顺序填写第 idx 个 0.00 价格框（0=一口价, 1=原价）。"""
        js = r"""
        var idx = arguments[0], value = arguments[1];
        var prices = [];
        document.querySelectorAll('input').forEach(function(i){
          if((i.getAttribute('placeholder')||'')==='0.00') prices.push(i);
        });
        if (idx >= prices.length) return false;
        var inp = prices[idx];
        var setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value').set;
        inp.focus();
        setter.call(inp, value);
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
        inp.blur();
        return true;
        """
        try:
            return bool(self.tab.run_js(js, idx, str(value)))
        except Exception as e:
            self.log(f"填写价格[{idx}]异常: {e}")
            return False

    def _upload_images(self, paths: list[str]) -> int:
        """上传本地主图到「添加首图」file input。返回提交的图片数。"""
        valid = [
            p for p in (paths or [])
            if p and os.path.isfile(p) and p.lower().endswith(IMG_EXTS)
        ]
        if not valid:
            return 0
        try:
            fi = self.tab.ele('css:input[type=file]', timeout=4)
        except Exception:
            fi = None
        if not fi:
            return 0
        try:
            fi.input("\n".join(valid))
            time.sleep(min(2 + len(valid) * 0.8, 12))
            return len(valid)
        except Exception as e:
            self.log(f"图片上传异常: {e}")
            return 0

    # ── 上架主流程 ────────────────────────────────────────────
    def fill_product(self, item: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        """把单个商品数据填进闲鱼发布页。

        item 字段（来自采集/打包层）：
          title, description, price, market_price, main_images/local_images
        闲鱼无独立标题，描述 = 标题 + 换行 + 描述正文。
        多 SKU 时取单一售价（闲鱼官方普通发布为单价）。
        dry_run=True 时填完即停（不点「发布」）。
        """
        result = {"ok": False, "filled": [], "skipped": [], "dry_run": dry_run, "error": ""}
        if not self.tab:
            result["error"] = "浏览器未就绪，请先 open()"
            return result

        try:
            self._goto_publish()
            if not self._wait_form():
                result["error"] = "闲鱼发布页未渲染（可能登录失效或页面改版）"
                return result

            title = item.get("title") or item.get("original_title") or ""
            desc = item.get("description") or item.get("desc") or ""
            price = item.get("price") or item.get("original_price") or ""

            # 1) 图片（闲鱼建议先传图）
            imgs = item.get("main_images") or item.get("local_images") or []
            up = self._upload_images(imgs)
            if up:
                result["filled"].append(f"图片×{up}")
                self.log(f"已上传图片 {up} 张")
            else:
                result["skipped"].append("商品图片")

            # 2) 描述（标题 + 正文，闲鱼无独立标题）
            full_desc = title
            if desc and desc != title:
                full_desc = f"{title}\n{desc}" if title else desc
            full_desc = (full_desc or "").strip()[:1500]
            if full_desc and self._fill_description(full_desc):
                result["filled"].append("描述")
            else:
                result["skipped"].append("描述")

            # 3) 价格（第 0 个 0.00 框 = 一口价）
            if str(price).strip():
                if self._fill_price_by_index(0, f"{float(price):.2f}"):
                    result["filled"].append("价格")
                else:
                    result["skipped"].append("价格")
            else:
                result["skipped"].append("价格")

            # 4) 原价（第 1 个 0.00 框，可选）
            market = item.get("market_price") or ""
            if str(market).strip() and self._price_inputs() >= 2:
                if self._fill_price_by_index(1, f"{float(market):.2f}"):
                    result["filled"].append("原价")

            # 多 SKU 仅提示：闲鱼官方普通发布为单价
            sku_list = item.get("sku_list") or []
            if len(sku_list) > 1:
                result["sku_count"] = len(sku_list)
                self.log(
                    f"检测到 {len(sku_list)} 个 SKU，闲鱼官方普通发布按单一售价填写。"
                )

            result["ok"] = True
            if dry_run:
                self.log("✅ 已填写闲鱼发布表单（dry-run，未点「发布」）。请在浏览器中核对后手动发布。")
            return result
        except Exception as e:
            result["error"] = str(e)
            return result


if __name__ == "__main__":
    lister = XianyuLister(on_log=lambda m: print(m, flush=True))
    if lister.open():
        demo = {"title": "测试商品-请勿发布", "description": "仅用于调试，请勿点击发布。",
                "price": 9.9}
        print(lister.fill_product(demo, dry_run=True))
