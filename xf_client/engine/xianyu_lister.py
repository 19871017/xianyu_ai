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
import re
import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


PUBLISH_URL = PLATFORM_URLS["xianyu"]["publish"]

IMG_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp")

# 闲鱼官方发布页「规格类型」下拉为固定选项，采集数据的规格类型名不一定命中，
# 故按规格值关键词推断，未命中时回退到第一项「颜色」。
SPEC_TYPE_OPTIONS = ["颜色", "尺码", "容量", "份数", "大小", "高度", "总量"]
# 关键词反推（兜底用）：按「命中该关键词的规格值个数」打分，而非关键词累计次数，
# 避免「黑色套装」里 套+装 双命中份数压过颜色这类误判。
SPEC_TYPE_KEYWORDS = {
    "颜色": ("色", "颜色", "color", "配色", "色系"),
    "尺码": ("码", "尺码", "尺寸", "size", "cm", "厘米", "寸", "号", "s", "m", "l", "xl"),
    "容量": ("容量", "ml", "毫升", "升", "g", "克", "kg", "斤", "公斤"),
    "份数": ("份", "装", "个", "件", "盒", "套", "包", "支", "瓶", "对", "双", "条"),
    "大小": ("大", "小", "大小"),
    "高度": ("高", "高度", "厚", "长", "短"),
    "总量": ("总量", "总数", "总"),
}
# 原始规格类型名（1688/淘宝 sku_attrs 的 key，最权威）→ 闲鱼固定 7 项 的映射。
# 闲鱼规格类型为只读下拉、不可自定义，故无对应项时映射到语义最接近且最不会
# 误导买家的项；颜色为最终兜底。
SPEC_NAME_MAP = {
    "颜色": "颜色", "颜色分类": "颜色", "配色": "颜色", "色彩": "颜色",
    "机型": "颜色", "型号": "颜色", "款式": "颜色", "样式": "颜色",
    "类型": "颜色", "花色": "颜色", "图案": "颜色", "版本": "颜色",
    "尺码": "尺码", "尺寸": "尺码", "鞋码": "尺码", "码数": "尺码", "size": "尺码",
    "容量": "容量", "重量": "容量", "净含量": "容量", "毫升": "容量", "克重": "容量",
    "套餐": "份数", "套装": "份数", "份数": "份数", "数量": "份数",
    "件数": "份数", "包装": "份数", "规格": "份数", "套餐类型": "份数",
    "大小": "大小",
    "高度": "高度", "厚度": "高度", "长度": "高度",
    "总量": "总量",
}
SPEC_VALUE_MAXLEN = 12  # 闲鱼发布页「规格值最大长度为12个字」


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

    def _set_condition(self, name: str = "全新") -> bool:
        """选择闲鱼「成色」下拉值（ant-select）。默认全新——本工具发的都是新品。"""
        js_open = r"""
        var sels = document.querySelectorAll('.ant-select-selector');
        for (var i=0;i<sels.length;i++){
          var box = sels[i].closest('.ant-form-item');
          if (box && /成色/.test(box.innerText||'')){
            sels[i].dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            sels[i].click();
            return true;
          }
        }
        return false;
        """
        try:
            if not self.tab.run_js(js_open):
                return False
        except Exception:
            return False
        time.sleep(0.8)
        js_pick = r"""
        var name = arguments[0];
        var opts = document.querySelectorAll('.ant-select-item-option');
        for (var i=0;i<opts.length;i++){
          var t = (opts[i].innerText||'').trim();
          var r = opts[i].getBoundingClientRect();
          if (t === name && r.width>0 && r.height>0){
            opts[i].dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            opts[i].click();
            return true;
          }
        }
        return false;
        """
        try:
            self.tab.run_js(js_pick, name)
        except Exception as e:
            self.log(f"选择成色异常: {e}")
            return False
        time.sleep(0.5)
        js_read = r"""
        var sels = document.querySelectorAll('.ant-select-selector');
        for (var i=0;i<sels.length;i++){
          var box = sels[i].closest('.ant-form-item');
          if (box && /成色/.test(box.innerText||'')){
            var item = sels[i].querySelector('.ant-select-selection-item');
            return item ? (item.innerText||item.title||'').trim() : '';
          }
        }
        return '';
        """
        try:
            return (self.tab.run_js(js_read) or "").strip() == name
        except Exception:
            return False

    def _upload_spec_images(self, sku_list: list[dict[str, Any]]) -> int:
        """按规格值上传 SKU 配图（闲鱼按规格值配图，非按 SKU 行）。

        每个规格值的 file input 在其 ``propertyValueInputContainer`` 容器内；
        用容器关联定位（比固定索引更稳），把该规格值首个有效 sku_image 传上去。
        返回成功上传的规格图数。
        """
        # 规格值(spec1) -> 首张有效本地图
        by_spec: dict[str, str] = {}
        for s in sku_list or []:
            sp = self._norm_spec(s.get("spec1") or "")
            img = s.get("sku_image") or ""
            if sp and sp not in by_spec and img and os.path.isfile(img) \
                    and img.lower().endswith(IMG_EXTS):
                by_spec[sp] = img
        if not by_spec:
            return 0

        # 读规格轴1的值顺序
        js_vals = r"""
        var out=[]; var i=0;
        while(true){ var pv=document.getElementById('itemProperties_0_propertyValues_'+i+'_propertyValue'); if(!pv) break; out.push(pv.value||''); i++; }
        return JSON.stringify(out);
        """
        try:
            import json as _json
            vals = _json.loads(self.tab.run_js(js_vals) or "[]")
        except Exception:
            return 0

        try:
            files = self.tab.eles('css:input[type=file]')
        except Exception:
            files = []
        if not files:
            return 0

        done = 0
        for idx, v in enumerate(vals):
            sp = self._norm_spec(v or "")
            img = by_spec.get(sp)
            if not img:
                continue
            # 规格值 val_idx=idx 对应 file input idx+1（主图占 0），实测映射已确认
            file_idx = idx + 1
            if file_idx >= len(files):
                continue
            try:
                files[file_idx].input(img)
                time.sleep(1.2)
                done += 1
            except Exception as e:
                self.log(f"规格图上传失败[{sp}]: {e}")
        if done:
            self.log(f"已按规格值上传配图 {done} 张。")
        return done

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
    def _spec_axis_names(sku_list: list[dict[str, Any]]) -> tuple[str, str]:
        """从 sku_attrs 取两个规格轴的原始类型名（最权威，用于映射闲鱼类型）。

        sku_attrs 为保序 dict（解析器按 spec1、spec2 顺序写入），故第 1 个 key
        对应 spec1 轴、第 2 个 key 对应 spec2 轴。取首个含 sku_attrs 的 SKU 即可。
        无 sku_attrs 时返回空串，交给关键词反推兜底。
        """
        for sku in sku_list or []:
            attrs = sku.get("sku_attrs")
            if isinstance(attrs, dict) and attrs:
                keys = list(attrs.keys())
                n1 = keys[0] if len(keys) >= 1 else ""
                n2 = keys[1] if len(keys) >= 2 else ""
                return n1, n2
        return "", ""

    @staticmethod
    def _map_spec_name(raw_name: str) -> str:
        """把原始规格类型名（1688/淘宝 sku_attrs 的 key）映射到闲鱼固定 7 项。

        命中返回映射后的固定项；未命中返回空串（交给关键词反推兜底）。
        """
        name = (str(raw_name or "")).strip().lower()
        if not name:
            return ""
        for k, v in SPEC_NAME_MAP.items():
            if k.lower() == name:
                return v
        # 包含匹配：原始名里含某个已知词（如「颜色分类」含「颜色」）。
        for k, v in SPEC_NAME_MAP.items():
            if k.lower() in name:
                return v
        return ""

    @staticmethod
    def _infer_spec_type(values: list[str], raw_name: str = "",
                         exclude: tuple[str, ...] | None = None) -> str:
        """推断闲鱼固定规格类型。

        优先级：原始类型名映射（最权威）> 关键词反推 > 回退首个可用项。
        关键词反推按「命中该关键词的规格值个数」打分（而非关键词累计次数），
        让「每个值都含色」的颜色轴稳胜「个别值偶含套/装」的误判。
        exclude：已被其它规格轴占用的类型名，避免两个轴重名（闲鱼同一商品
        两个规格轴类型不能相同）；命中映射但已被占用时顺延到下一个可用项。
        """
        excluded = set(exclude or ())

        def _first_available() -> str:
            for name in SPEC_TYPE_OPTIONS:
                if name not in excluded:
                    return name
            return SPEC_TYPE_OPTIONS[0]

        mapped = XianyuLister._map_spec_name(raw_name)
        if mapped and mapped not in excluded:
            return mapped

        vals = [str(v or "").lower() for v in values if str(v or "").strip()]
        if not vals:
            return _first_available()
        # 每个规格值归给「命中的最长关键词」所属类型：更长的关键词更具体，
        # 让 100ml 命中 容量「ml」(2字) 而非 尺码「m」(1字)；值内平局按选项
        # 顺序（颜色优先），使「黑色套装」这类色+装并存的值稳归颜色。
        counts = {name: 0 for name in SPEC_TYPE_OPTIONS}
        for v in vals:
            best_name, best_len = "", 0
            for name in SPEC_TYPE_OPTIONS:
                if name in excluded:
                    continue
                for kw in SPEC_TYPE_KEYWORDS.get(name, ()):
                    kw = kw.lower()
                    if kw in v and len(kw) > best_len:
                        best_name, best_len = name, len(kw)
            if best_name:
                counts[best_name] += 1
        best, best_score = _first_available(), 0
        for name in SPEC_TYPE_OPTIONS:
            if name in excluded:
                continue
            if counts[name] > best_score:
                best, best_score = name, counts[name]
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
        # 原始类型名（最权威）：优先用它映射闲鱼固定类型，反推仅作兜底。
        name1, name2 = self._spec_axis_names(sku_list)

        # 轴 1
        if not self._add_spec_type_block():
            out["note"] = "未能打开规格类型区。"
            return out
        t1 = self._infer_spec_type(v1, name1)
        n1 = self._fill_spec_axis(0, t1, v1)
        self.log(f"规格类型1「{t1}」填入 {n1}/{len(v1)} 个值。")
        out["axes"] = 1

        # 轴 2（可选）
        if v2:
            if self._add_spec_type_block():
                # 排除第一轴已用类型，避免两个规格轴重名（如双「颜色」）。
                t2 = self._infer_spec_type(v2, name2, exclude=(t1,))
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

        # 闲鱼多规格按笛卡尔积（轴1×轴2）生成所有行且不可删行，每行都强制要求
        # 价格+库存。采集源往往只覆盖部分组合（如某色不含某机型），缺失组合若
        # 留空会导致整单发布被「请输入价格/库存」拦截。
        # 策略：缺失组合也填——价格用同轴1值(同色)的真实价兜底，库存填 0（无货），
        # 使买家无法下单到源头不存在的规格，既过校验又防止超卖/无法追溯采购。
        axis1_price: dict[str, float] = {}
        all_prices: list[float] = []
        for sku in sku_list:
            pv = float(sku.get("price") or 0)
            if pv > 0:
                k1 = self._norm_spec(sku.get("spec1") or "")
                axis1_price.setdefault(k1, pv)
                all_prices.append(pv)
        fallback_price = min(all_prices) if all_prices else 0.0

        filled = 0
        real_filled = 0
        zero_stock = 0
        for row in rows:
            key = (self._norm_spec(row.get("v1") or ""), self._norm_spec(row.get("v2") or ""))
            sku = sku_index.get(key)
            if not sku:
                # 单轴时 v2 为空，二次尝试仅按 v1 匹配。
                sku = sku_index.get((key[0], ""))
            if sku:
                price = float(sku.get("price") or 0)
                stock = sku.get("stock") or ""
                price_str = f"{price:.2f}" if price else ""
                stock_str = str(int(stock)) if str(stock).strip() else ""
                if not price_str:
                    # 真实组合但缺价：用同色兜底价，避免留空被拦。
                    bp = axis1_price.get(key[0], fallback_price)
                    price_str = f"{bp:.2f}" if bp else ""
                if self._fill_sku_row(row["n"], price_str, stock_str):
                    filled += 1
                    real_filled += 1
            else:
                # 笛卡尔积空缺组合：兜底价 + 库存 0（无货，不可下单）。
                bp = axis1_price.get(key[0], fallback_price)
                if bp <= 0:
                    continue
                if self._fill_sku_row(row["n"], f"{bp:.2f}", "0"):
                    filled += 1
                    zero_stock += 1

        out["rows_filled"] = filled
        out["real_skus"] = real_filled
        out["zero_stock_rows"] = zero_stock
        out["ok"] = filled >= len(rows) and len(rows) > 0
        if zero_stock:
            self.log(
                f"已为 {zero_stock} 个源端不存在的规格组合填库存 0（无货占位），"
                f"真实可售 {real_filled} 个。"
            )
        return out

    # ── 发布 ──────────────────────────────────────────────────
    def _click_publish(self, timeout: int = 20) -> dict[str, Any]:
        """点击发布页底部「发布」按钮并校验提交结果。

        闲鱼发布页底部按钮文本为「发布」（class 后缀为 hash，故按文本匹配）。
        点击后页面会跳转/弹层或出现校验提示。返回 {ok, error}。
        """
        out = {"ok": False, "error": ""}
        # 找文本恰为「发布」的可见按钮（排除「添加规格类型」等）。
        find_js = r"""
        var btns = document.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) {
          var b = btns[i];
          var t = (b.innerText || '').trim();
          var r = b.getBoundingClientRect();
          if (t === '发布' && r.width > 0 && r.height > 0) return true;
        }
        return false;
        """
        try:
            if not self.tab.run_js(find_js):
                out["error"] = "未找到「发布」按钮"
                return out
        except Exception as e:
            out["error"] = f"查找发布按钮异常: {e}"
            return out

        btn = None
        try:
            for b in self.tab.eles("css:button"):
                if (b.text or "").strip() == "发布":
                    btn = b
                    break
        except Exception as e:
            out["error"] = f"定位发布按钮异常: {e}"
            return out
        if not btn:
            out["error"] = "未定位到发布按钮元素"
            return out

        try:
            btn.click(by_js=False)
        except Exception as e:
            out["error"] = f"点击发布按钮异常: {e}"
            return out

        # 校验提交结果：等待 URL 离开 /publish，或出现成功/失败提示。
        deadline = time.time() + timeout
        check_js = r"""
        var msg = '';
        document.querySelectorAll('.ant-message-notice-content, .ant-form-item-explain-error, [class*=toast], [class*=Toast]').forEach(function(el){
          var t = (el.innerText || '').trim();
          if (t) msg += t + ' | ';
        });
        return msg;
        """
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                url = self.tab.url or ""
            except Exception:
                url = ""
            if url and "/publish" not in url:
                out["ok"] = True
                return out
            try:
                msg = (self.tab.run_js(check_js) or "").strip()
            except Exception:
                msg = ""
            if msg:
                # 含「成功」视为成功，否则视为校验未通过。
                if "成功" in msg or "已发布" in msg:
                    out["ok"] = True
                    return out
                out["error"] = msg[:200]
                return out
        out["error"] = "点击发布后未检测到成功跳转或提示（可能仍有必填项未完成）"
        return out

    def _capture_published_id(self, timeout: int = 8) -> str:
        """发布成功后尝试抓取闲鱼新商品 id（用于订单回溯）。

        闲鱼发布成功通常跳转到 item/详情或个人在售页，商品 id 多出现在
        URL（item?id=xxx / item/xxx）或页面链接中。抓不到返回空串。
        """
        id_re = re.compile(r"item[/?](?:id=)?(\d{8,})")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                url = self.tab.url or ""
            except Exception:
                url = ""
            m = id_re.search(url)
            if m:
                return m.group(1)
            # 退而求其次：从页面里第一个商品详情链接抓 id。
            try:
                href = self.tab.run_js(r"""
                var a = document.querySelector('a[href*="item?id="], a[href*="item/"]');
                return a ? a.href : '';
                """) or ""
            except Exception:
                href = ""
            m = id_re.search(href)
            if m:
                return m.group(1)
            time.sleep(1.0)
        return ""

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
                    spec_imgs = self._upload_spec_images(sku_list)
                    if spec_imgs:
                        result["filled"].append(f"规格图×{spec_imgs}")
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

            # 4) 成色：本工具发布的都是新品，统一选「全新」
            if self._set_condition("全新"):
                result["filled"].append("成色(全新)")
            else:
                result["skipped"].append("成色")

            if dry_run:
                result["ok"] = True
                self.log("✅ 已填写闲鱼发布表单（dry-run，未点「发布」）。请在浏览器中核对后手动发布。")
                return result

            # 非 dry-run：真正点「发布」按钮并校验提交结果。
            self.log("正在提交发布…")
            pub = self._click_publish()
            result["publish"] = pub
            if pub["ok"]:
                result["ok"] = True
                result["published"] = True
                xy_id = self._capture_published_id()
                if xy_id:
                    result["xianyu_item_id"] = xy_id
                    self.log(f"✅ 已提交发布，闲鱼商品 id：{xy_id}")
                else:
                    self.log("✅ 已提交发布，闲鱼后台稍后可见该商品。")
            else:
                result["ok"] = False
                result["published"] = False
                result["error"] = f"发布未成功: {pub.get('error')}"
                self.log(f"❌ {result['error']}")
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
