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

# 闲鱼官方发布页「规格类型」下拉为固定选项，采集数据的规格类型名不一定命中，
# 故按规格值关键词推断，未命中时回退到第一项「颜色」。
SPEC_TYPE_OPTIONS = ["颜色", "尺码", "容量", "份数", "大小", "高度", "总量"]
SPEC_TYPE_KEYWORDS = {
    "颜色": ("色", "颜色", "color", "配色", "色系"),
    "尺码": ("码", "尺码", "尺寸", "size", "cm", "厘米", "寸", "号"),
    "容量": ("容量", "ml", "升", "l", "毫升", "g", "克", "kg", "ml/"),
    "份数": ("份", "装", "个", "件", "盒", "套", "包", "支", "瓶", "对", "双"),
    "大小": ("大", "小", "大小", "规格"),
    "高度": ("高", "高度", "厚"),
    "总量": ("总量", "总", "量"),
}
SPEC_VALUE_MAXLEN = 30


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

    # ── 多规格(SKU) 支持 ──────────────────────────────────────
    @staticmethod
    def _norm_spec(value: str) -> str:
        """规格值归一：去首尾空白并截断到闲鱼可接受长度。"""
        return (str(value or "").strip())[:SPEC_VALUE_MAXLEN]

    def _collect_spec_axes(self, sku_list: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        """从 sku_list 提取两个规格轴（去重、保序）。

        返回 (spec1_values, spec2_values)。spec2 为空表示单规格轴。
        """
        v1: list[str] = []
        v2: list[str] = []
        for sku in sku_list:
            s1 = self._norm_spec(sku.get("spec1") or "")
            s2 = self._norm_spec(sku.get("spec2") or "")
            if s1 and s1 not in v1:
                v1.append(s1)
            if s2 and s2 not in v2:
                v2.append(s2)
        return v1, v2

    @staticmethod
    def _infer_spec_type(values: list[str]) -> str:
        """按规格值关键词推断闲鱼固定规格类型，未命中回退「颜色」。"""
        blob = " ".join(values).lower()
        best, best_hits = SPEC_TYPE_OPTIONS[0], 0
        for name in SPEC_TYPE_OPTIONS:
            hits = sum(1 for kw in SPEC_TYPE_KEYWORDS.get(name, ()) if kw.lower() in blob)
            if hits > best_hits:
                best, best_hits = name, hits
        return best

    def _add_spec_type_block(self) -> bool:
        """原生点击「添加规格类型」按钮（React/AntD 必须 by_js=False）。"""
        try:
            btn = self.tab.ele("css:button.addBtn--aeORl7oU", timeout=4)
        except Exception:
            btn = None
        if not btn:
            return False
        try:
            btn.click(by_js=False)
            time.sleep(1.2)
            return True
        except Exception as e:
            self.log(f"点击添加规格类型异常: {e}")
            return False

    def _select_spec_type(self, prop_idx: int, type_name: str) -> bool:
        """打开第 prop_idx 个规格类型下拉并选中 type_name（原生点击）。"""
        try:
            selector = self.tab.ele(
                f'xpath://input[@id="itemProperties_{prop_idx}_propertyName"]'
                f'/ancestor::div[contains(@class,"ant-select-selector")][1]',
                timeout=4,
            )
        except Exception:
            selector = None
        if not selector:
            self.log(f"未找到规格类型选择器 itemProperties_{prop_idx}_propertyName")
            return False
        try:
            selector.click(by_js=False)
        except Exception as e:
            self.log(f"打开规格类型下拉异常: {e}")
            return False
        time.sleep(1.0)
        try:
            opts = self.tab.eles(
                "css:.ant-select-dropdown:not(.ant-select-dropdown-hidden) "
                ".ant-select-item-option"
            )
        except Exception:
            opts = []
        target = None
        for o in opts:
            if (o.text or "").strip() == type_name:
                target = o
                break
        if not target and opts:
            target = opts[0]
        if not target:
            self.log("规格类型下拉无可选项")
            return False
        try:
            target.click(by_js=False)
            time.sleep(1.0)
            return True
        except Exception as e:
            self.log(f"选择规格类型异常: {e}")
            return False

    def _fill_spec_value(self, prop_idx: int, val_idx: int, value: str) -> bool:
        """填第 prop_idx 个规格类型的第 val_idx 个规格值（React 受控 input）。

        填入后闲鱼会自动新增下一个空值输入框。
        """
        js = r"""
        var id = arguments[0], value = arguments[1];
        var inp = document.getElementById(id);
        if(!inp) return false;
        var setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value').set;
        inp.focus();
        setter.call(inp, value);
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
        inp.blur();
        return true;
        """
        ele_id = f"itemProperties_{prop_idx}_propertyValues_{val_idx}_propertyValue"
        try:
            return bool(self.tab.run_js(js, ele_id, value))
        except Exception as e:
            self.log(f"填规格值[{prop_idx}/{val_idx}]异常: {e}")
            return False

    def _fill_spec_axis(self, prop_idx: int, type_name: str, values: list[str]) -> int:
        """完整填写一个规格轴：选类型 + 逐个填值。返回成功填入的值数量。"""
        if not self._select_spec_type(prop_idx, type_name):
            return 0
        filled = 0
        for i, v in enumerate(values):
            if self._fill_spec_value(prop_idx, i, v):
                filled += 1
            time.sleep(0.7)
        return filled

    def _wait_sku_table(self, timeout: int = 12) -> bool:
        """等待 SKU 表生成（出现 itemSkuList_0_propertyValue 隐藏字段）。"""
        js = "return !!document.getElementById('itemSkuList_0_propertyValue');"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.tab.run_js(js):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _read_sku_rows(self) -> list[dict[str, Any]]:
        """读取已生成的 SKU 行映射：[{n, v1, v2}]，v1/v2 为各行规格值。"""
        js = r"""
        var out = [], i = 0;
        while (true) {
          var pv = document.getElementById('itemSkuList_' + i + '_propertyValue');
          if (!pv) break;
          var sv = document.getElementById('itemSkuList_' + i + '_secondPropertyValue');
          out.push({n: i, v1: (pv.value || ''), v2: (sv ? (sv.value || '') : '')});
          i++;
        }
        return out;
        """
        try:
            return list(self.tab.run_js(js) or [])
        except Exception as e:
            self.log(f"读取 SKU 行异常: {e}")
            return []

    def _fill_sku_row(self, row_idx: int, price: str, stock: str) -> bool:
        """填第 row_idx 个 SKU 行的价格/库存（行内 0.00 / 0 输入框）。"""
        js = r"""
        var n = arguments[0], price = arguments[1], stock = arguments[2];
        var hidden = document.getElementById('itemSkuList_' + n + '_propertyValue');
        if (!hidden) return false;
        var tr = hidden.closest('tr');
        if (!tr) return false;
        var setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value').set;
        function setVal(inp, v) {
          if (!inp) return false;
          inp.focus();
          setter.call(inp, v);
          inp.dispatchEvent(new Event('input', {bubbles: true}));
          inp.dispatchEvent(new Event('change', {bubbles: true}));
          inp.blur();
          return true;
        }
        var ok = false;
        var priceInp = tr.querySelector('input[placeholder="0.00"]');
        var stockInp = tr.querySelector('input[placeholder="0"]');
        if (price) ok = setVal(priceInp, price) || ok;
        if (stock) setVal(stockInp, stock);
        return ok;
        """
        try:
            return bool(self.tab.run_js(js, row_idx, str(price), str(stock)))
        except Exception as e:
            self.log(f"填 SKU 行[{row_idx}]异常: {e}")
            return False

    def _fill_multi_sku(self, sku_list: list[dict[str, Any]]) -> dict[str, Any]:
        """多规格发布：建规格轴 -> 生成 SKU 表 -> 按规格值匹配逐行填价格/库存。

        返回 {ok, axes, rows_filled, total_rows, note}。
        """
        out = {"ok": False, "axes": 0, "rows_filled": 0, "total_rows": 0, "note": ""}
        v1, v2 = self._collect_spec_axes(sku_list)
        if not v1:
            out["note"] = "无有效规格值，已跳过多规格。"
            return out

        # 轴 1
        if not self._add_spec_type_block():
            out["note"] = "未能打开规格类型区。"
            return out
        t1 = self._infer_spec_type(v1)
        n1 = self._fill_spec_axis(0, t1, v1)
        self.log(f"规格类型1「{t1}」填入 {n1}/{len(v1)} 个值。")
        out["axes"] = 1

        # 轴 2（可选）
        if v2:
            if self._add_spec_type_block():
                t2 = self._infer_spec_type(v2)
                n2 = self._fill_spec_axis(1, t2, v2)
                self.log(f"规格类型2「{t2}」填入 {n2}/{len(v2)} 个值。")
                out["axes"] = 2

        if not self._wait_sku_table():
            out["note"] = "SKU 表未生成。"
            return out

        rows = self._read_sku_rows()
        out["total_rows"] = len(rows)
        # 按 (v1,v2) 建采集 SKU 索引（归一后匹配）。
        sku_index: dict[tuple[str, str], dict[str, Any]] = {}
        for sku in sku_list:
            key = (self._norm_spec(sku.get("spec1") or ""), self._norm_spec(sku.get("spec2") or ""))
            sku_index.setdefault(key, sku)

        filled = 0
        for row in rows:
            key = (self._norm_spec(row.get("v1") or ""), self._norm_spec(row.get("v2") or ""))
            sku = sku_index.get(key)
            if not sku:
                # 单轴时 v2 为空，二次尝试仅按 v1 匹配。
                sku = sku_index.get((key[0], ""))
            if not sku:
                continue
            price = sku.get("price") or 0
            stock = sku.get("stock") or ""
            price_str = f"{float(price):.2f}" if price else ""
            stock_str = str(int(stock)) if str(stock).strip() else ""
            if self._fill_sku_row(row["n"], price_str, stock_str):
                filled += 1
        out["rows_filled"] = filled
        out["ok"] = filled > 0
        return out

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

            # 3) 多/单规格分流
            sku_list = item.get("sku_list") or []
            result["sku_count"] = len(sku_list)
            multi = len(sku_list) > 1

            if multi:
                # 多规格：建规格轴 + 逐行填价格/库存
                ms = self._fill_multi_sku(sku_list)
                result["multi_sku"] = ms
                if ms["ok"]:
                    result["filled"].append(
                        f"多规格×{ms['rows_filled']}/{ms['total_rows']}"
                    )
                    self.log(
                        f"已填多规格：{ms['axes']} 个规格轴，"
                        f"{ms['rows_filled']}/{ms['total_rows']} 行价格/库存。"
                    )
                else:
                    # 多规格失败，回退单价兜底，避免整单填不进价格
                    self.log(f"多规格填写未成功（{ms['note']}），回退单一售价。")
                    if str(price).strip() and self._fill_price_by_index(0, f"{float(price):.2f}"):
                        result["filled"].append("价格(回退单价)")
                    else:
                        result["skipped"].append("价格")
            else:
                # 单规格：第 0 个 0.00 框 = 一口价
                if str(price).strip():
                    if self._fill_price_by_index(0, f"{float(price):.2f}"):
                        result["filled"].append("价格")
                    else:
                        result["skipped"].append("价格")
                else:
                    result["skipped"].append("价格")

                # 原价（第 1 个 0.00 框，可选）
                market = item.get("market_price") or ""
                if str(market).strip() and self._price_inputs() >= 2:
                    if self._fill_price_by_index(1, f"{float(market):.2f}"):
                        result["filled"].append("原价")

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
