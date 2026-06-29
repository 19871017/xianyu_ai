"""闲管家(goofish.pro) 商品自动上架器（普通商品模式）。

发布页 ``/sale/product/add`` 是 Element UI 的 Vue SPA。经实测确认：
- **多规格/深库存是鱼小铺专属能力**，普通闲鱼店铺点击「添加多规格深库存」
  只会弹出「请先升级闲鱼号为鱼小铺」提示，弹窗不渲染。因此本上架器
  只走 **普通商品（单规格）模式**，不再尝试多规格弹窗。
- 表单字段以 ``el-form-item`` 组织：``el-form-item__label`` + 内部
  ``el-input__inner`` / ``textarea``。
- 部分字段（图片/标题/描述/价格等）在选择「商品分类」后才渐进渲染。
- 必须**选中店铺**（``.auth-list>li`` 里的真实店铺），否则部分字段不就绪。

设计要点：
- 登录态走 utils.login_manager（localStorage access_token），免登录。
- 默认 ``dry_run=True``：填完表单**停在提交前**，由人工确认后再放开提交，
  避免误上架真实商品。
- 字段定位优先用「label 文案 → 同一 form-item 内的输入框」的稳定方式，
  避免依赖易变的 hash class。
- DrissionPage 简写选择器不解析 ``>`` 子代组合器，凡用到的地方一律加
  ``css:`` 前缀；Vue 弹窗/列表项需用原生点击 ``click(by_js=False)``。
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


PUBLISH_URL = PLATFORM_URLS["goofishpro"]["publish"]

# 成色文案 → 闲管家单选项
CONDITION_MAP = {
    "全新": "全新", "准新": "准新",
    "99新": "99新", "95新": "95新", "9新": "9新",
    "8新": "8新", "7新": "7新", "6新": "6新", "5新及以下": "5新及以下",
}

IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")


class GoofishProLister:
    """闲管家商品上架器（普通商品模式，默认 dry-run，停在提交前）。"""

    def __init__(self, on_log: Callable[[str], None] | None = None,
                 mode: str = "normal"):
        """mode:
            "normal" — 普通商品模式（单规格，免费，已实测可用）。
            "shop"   — 鱼小铺多规格模式（需开通鱼小铺，付费能力）。
                       开通后才能打开「添加多规格深库存」弹窗，未开通会被平台拦截。
        """
        self.log = on_log or (lambda m: None)
        self.browser = None
        self.tab = None
        self.mode = mode if mode in ("normal", "shop") else "normal"

    # ── 浏览器/登录 ────────────────────────────────────────────
    def open(self, timeout: int = 600) -> bool:
        res = ensure_login("goofishpro", on_log=self.log, timeout=timeout)
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
        time.sleep(5)

    def _wait_form(self, timeout: int = 20) -> bool:
        """等待发布表单初步渲染（出现输入框）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            n = self.tab.run_js(
                "return document.querySelectorAll('input.el-input__inner').length;"
            ) or 0
            if int(n) >= 2:
                return True
            time.sleep(0.5)
        return False

    def _wait_full_form(self, timeout: int = 15) -> bool:
        """等待选类目后的完整表单渲染（出现商品标题输入框）。"""
        deadline = time.time() + timeout
        check = """
        var items = document.querySelectorAll('.el-form-item');
        for (var i=0;i<items.length;i++){
          var l=items[i].querySelector('.el-form-item__label');
          if(l&&(l.innerText||'').trim().indexOf('商品标题')===0) return true;
        }
        return false;
        """
        while time.time() < deadline:
            try:
                if self.tab.run_js(check):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _fill_by_label(self, label: str, value: str) -> bool:
        """按 form-item 的 label 文案定位其内部输入框/文本域并填值。"""
        js = """
        var label = arguments[0], value = arguments[1];
        var items = document.querySelectorAll('.el-form-item');
        for (var i = 0; i < items.length; i++) {
          var lab = items[i].querySelector('.el-form-item__label');
          if (!lab) continue;
          if ((lab.innerText || '').trim().indexOf(label) === 0) {
            var inp = items[i].querySelector('input.el-input__inner, textarea');
            if (!inp) continue;
            var setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value') ||
              Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
            if (inp.tagName === 'TEXTAREA') {
              setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
            }
            inp.focus();
            setter.set.call(inp, value);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            inp.blur();
            return true;
          }
        }
        return false;
        """
        try:
            return bool(self.tab.run_js(js, label, str(value)))
        except Exception as e:
            self.log(f"填写[{label}]异常: {e}")
            return False

    def _select_radio_by_text(self, text: str) -> bool:
        """点击文案匹配的 el-radio（商品类型等）。"""
        js = """
        var text = arguments[0];
        var radios = document.querySelectorAll('.el-radio, .el-radio-button');
        for (var i = 0; i < radios.length; i++) {
          var t = (radios[i].innerText || '').trim();
          if (t === text) { radios[i].click(); return true; }
        }
        return false;
        """
        try:
            return bool(self.tab.run_js(js, text))
        except Exception:
            return False

    def _select_product_type(self, ptype: str = "普通商品") -> bool:
        """选择商品类型为普通商品（优先 radio，其次按文案点击）。"""
        if self._select_radio_by_text(ptype):
            return True
        el = self.tab.ele(f"text:{ptype}", timeout=2)
        if el:
            try:
                el.click()
                return True
            except Exception:
                pass
        return False

    # ── 商品分类（级联搜索） ──────────────────────────────────
    def _select_category(self, keyword: str, prefer: str = "") -> dict:
        """商品分类：在输入框输关键词 → 选 suggestion-list-item。"""
        result = {"ok": False, "picked": ""}
        focus_js = """
        var items = document.querySelectorAll('.el-form-item');
        for (var i = 0; i < items.length; i++) {
          var lab = items[i].querySelector('.el-form-item__label');
          if (lab && (lab.innerText||'').trim().indexOf('商品分类') === 0) {
            var inp = items[i].querySelector('input.el-input__inner');
            if (inp) {
              inp.focus();
              var setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value');
              setter.set.call(inp, arguments[0]);
              inp.dispatchEvent(new Event('input', {bubbles:true}));
              return true;
            }
          }
        }
        return false;
        """
        try:
            if not self.tab.run_js(focus_js, keyword):
                return result
        except Exception:
            return result

        # 等建议项渲染，再用 DrissionPage 原生点击（JS .click() 不触发 Vue 选中）。
        import time as _t
        deadline = _t.time() + 6
        while _t.time() < deadline:
            try:
                sugs = self.tab.eles("css:.suggestion-list-item", timeout=1)
            except Exception:
                sugs = []
            if sugs:
                target = None
                if prefer:
                    for s in sugs:
                        if prefer in (s.text or ""):
                            target = s
                            break
                if target is None:
                    target = sugs[0]
                picked = (target.text or "").strip()
                try:
                    target.scroll.to_see()
                except Exception:
                    pass
                _t.sleep(0.2)
                try:
                    target.click(by_js=False)
                except Exception:
                    try:
                        target.click()
                    except Exception:
                        return result
                result["ok"] = True
                result["picked"] = picked
                return result
            _t.sleep(0.5)
        return result

    # ── 店铺选择 ──────────────────────────────────────────────
    def _select_shop(self, prefer: str = "") -> dict:
        """选中闲鱼店铺区(.auth-list>li)里的真实店铺。

        排除 ``创建闲鱼店铺``（class 含 sku-add-btn）。prefer 为偏好店铺名片段，
        留空则选第一个真实店铺。返回 {"ok":bool, "picked":str}。
        """
        result = {"ok": False, "picked": ""}
        # 店铺区在选完类目后异步渲染，轮询等待 .auth-list>li 出现。
        lis = []
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                lis = self.tab.eles("css:.auth-list>li", timeout=1)
            except Exception:
                lis = []
            real = 0
            for li in (lis or []):
                cls = (li.attr("class") or "")
                txt = (li.text or "").strip()
                if "sku-add-btn" not in cls and txt and "创建" not in txt:
                    real += 1
            if real:
                break
            time.sleep(0.5)
        target = None
        for li in lis:
            cls = (li.attr("class") or "")
            if "sku-add-btn" in cls:
                continue
            txt = (li.text or "").strip()
            if not txt or "创建" in txt:
                continue
            if prefer and prefer not in txt:
                continue
            target = li
            result["picked"] = txt
            break
        if target is None:
            # 无 prefer 命中时退而求其次：第一个非创建项
            for li in lis:
                if "sku-add-btn" in (li.attr("class") or ""):
                    continue
                txt = (li.text or "").strip()
                if txt and "创建" not in txt:
                    target = li
                    result["picked"] = txt
                    break
        if target is None:
            return result
        try:
            target.scroll.to_see()
        except Exception:
            pass
        time.sleep(0.3)
        try:
            target.click(by_js=False)
        except Exception:
            try:
                target.click()
            except Exception:
                return result
        time.sleep(0.8)
        try:
            sel = self.tab.run_js(
                "var s=document.querySelector('.auth-list>li.selected');return s?(s.innerText||'').trim():'';"
            )
            result["ok"] = bool(sel)
        except Exception:
            result["ok"] = True
        return result

    # ── 图片上传 ──────────────────────────────────────────────
    def _upload_images(self, paths: list[str]) -> int:
        """把本地主图上传到「商品图片」。返回成功提交的图片数。"""
        valid = [
            p for p in (paths or [])
            if p and os.path.isfile(p) and p.lower().endswith(IMG_EXTS)
        ]
        if not valid:
            return 0
        try:
            fi = self.tab.ele("css:.el-upload__input", timeout=4)
        except Exception:
            fi = None
        if not fi:
            try:
                fi = self.tab.ele('css:input[type=file]', timeout=2)
            except Exception:
                fi = None
        if not fi:
            return 0
        # DrissionPage: 多文件用换行分隔
        try:
            fi.input("\n".join(valid))
            time.sleep(min(2 + len(valid) * 0.6, 10))
            return len(valid)
        except Exception as e:
            self.log(f"图片上传异常: {e}")
            # 退化为逐张上传
            done = 0
            for p in valid:
                try:
                    self.tab.ele("css:.el-upload__input", timeout=2).input(p)
                    time.sleep(1.2)
                    done += 1
                except Exception:
                    break
            return done

    # ── 成色（底部「请选择」下拉） ────────────────────────────
    def _select_condition(self, level: str = "全新") -> bool:
        """成色为 el-select 下拉（placeholder「请选择」）：点开 → 选等级。"""
        level = CONDITION_MAP.get(level, level)
        open_js = """
        var items = document.querySelectorAll('.el-form-item');
        for (var i=0;i<items.length;i++){
          var lab=items[i].querySelector('.el-form-item__label');
          if(lab && (lab.innerText||'').trim()==='成色'){
            var inp=items[i].querySelector('input.el-input__inner');
            if(inp && (inp.getAttribute('placeholder')||'').indexOf('请选择')>=0){
              inp.click(); return true;
            }
          }
        }
        return false;
        """
        try:
            if not self.tab.run_js(open_js):
                return False
        except Exception:
            return False
        time.sleep(0.5)
        pick_js = """
        var level = arguments[0];
        var dds=document.querySelectorAll('.el-select-dropdown');
        for(var i=0;i<dds.length;i++){
          if(dds[i].style.display==='none') continue;
          var its=dds[i].querySelectorAll('.el-select-dropdown__item');
          for(var j=0;j<its.length;j++){
            if((its[j].innerText||'').trim()===level){ its[j].click(); return true; }
          }
        }
        return false;
        """
        try:
            return bool(self.tab.run_js(pick_js, level))
        except Exception:
            return False

    @staticmethod
    def _format_sku_summary(sku_list: list[dict[str, Any]], max_rows: int = 30) -> str:
        """把多规格清单整理成买家可读的描述文本（普通模式不支持多规格选购，
        故把规格/价格列进描述，便于买家留言选规格）。"""
        lines = []
        for s in sku_list or []:
            spec = " ".join(
                x for x in [str(s.get("spec1") or "").strip(),
                            str(s.get("spec2") or "").strip()] if x
            ).strip()
            if not spec:
                continue
            try:
                pr = float(str(s.get("price") or "").replace(",", "").strip())
            except Exception:
                pr = 0.0
            lines.append(f"· {spec}：¥{pr:.2f}" if pr > 0 else f"· {spec}")
            if len(lines) >= max_rows:
                break
        if not lines:
            return ""
        return "【可选规格】（拍下请留言所需规格）\n" + "\n".join(lines)

    # ── 上架主流程 ────────────────────────────────────────────
    def fill_product(self, item: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        """上架分流入口：按 self.mode 走普通模式或鱼小铺多规格模式。

        - mode="normal"：普通商品模式（单规格），多 SKU 取最低价 + 规格写入描述。
        - mode="shop"  ：鱼小铺多规格模式（需开通鱼小铺）。开通后逐 SKU 建规格/
          深库存；未开通则返回明确提示，不盲填无法验证的表单。
        """
        if getattr(self, "mode", "normal") == "shop":
            return self._fill_product_shop(item, dry_run=dry_run)
        return self._fill_product_normal(item, dry_run=dry_run)

    def _fill_product_normal(self, item: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        """把单个商品数据填进发布表单（普通商品模式）。

        item 字段（来自采集/打包层）：
          title, description, price, market_price, stock, condition,
          merchant_code, main_images/local_images, category_keyword
        多 SKU 时取价格区间最低价作为单一售价（普通模式不支持多规格）。
        dry_run=True 时填完即停（不提交）。
        """
        result = {"ok": False, "filled": [], "skipped": [], "dry_run": dry_run, "error": ""}
        if not self.tab:
            result["error"] = "浏览器未就绪，请先 open()"
            return result

        try:
            self._goto_publish()
            if not self._wait_form():
                result["error"] = "发布表单未渲染（可能登录失效）"
                return result

            # 售价：顶层 price 优先；缺失/非正时回退到 sku_list 最低有效价
            # （普通模式不支持多规格，多 SKU 以价格区间最低价作单一售价）。
            def _f(v):
                try:
                    return float(str(v).replace(",", "").strip())
                except Exception:
                    return 0.0
            price = item.get("price") or item.get("original_price") or ""
            if _f(price) <= 0:
                _sp = [
                    _f(s.get("price"))
                    for s in (item.get("sku_list") or [])
                    if _f(s.get("price")) > 0
                ]
                if _sp:
                    price = min(_sp)
            title = item.get("title") or item.get("original_title") or ""

            # 1) 商品类型（普通商品）
            ptype = item.get("product_type") or "普通商品"
            if self._select_product_type(ptype):
                result["filled"].append(f"商品类型={ptype}")
            else:
                result["skipped"].append("商品类型")
            time.sleep(1.0)

            # 2) 商品分类（必填，闲管家据此渲染图片/标题/价格等字段）
            cat_kw = item.get("category_keyword") or item.get("category") or (title[:6] if title else "")
            cat_prefer = item.get("category_prefer") or ""
            if cat_kw:
                cat = self._select_category(cat_kw, cat_prefer)
                if cat["ok"]:
                    result["filled"].append(f"商品分类={cat['picked']}")
                    self.log(f"已选分类: {cat['picked']}")
                else:
                    result["skipped"].append("商品分类")
            else:
                result["skipped"].append("商品分类")

            # 3) 选中店铺（图片/标题/成色等字段依赖店铺选中后才渲染）
            time.sleep(0.8)
            shop = self._select_shop(item.get("shop_prefer") or "")
            if shop["ok"]:
                result["filled"].append(f"店铺={shop['picked']}")
                self.log(f"已选店铺: {shop['picked']}")
            else:
                result["skipped"].append("店铺")

            # 选店铺后等完整表单渲染（出现商品标题输入框）
            self._wait_full_form()
            time.sleep(0.8)

            # 4) 图片上传
            imgs = item.get("main_images") or item.get("local_images") or []
            up = self._upload_images(imgs)
            if up:
                result["filled"].append(f"图片×{up}")
                self.log(f"已上传图片 {up} 张")
            else:
                result["skipped"].append("商品图片")

            # 5) 标题/描述
            if title:
                if self._fill_by_label("商品标题", title[:30]):
                    result["filled"].append("商品标题")
                else:
                    result["skipped"].append("商品标题")
            desc = item.get("description") or item.get("desc") or title
            # 多规格降级为单品时，把规格/价格清单追加进描述，买家可见可选规格。
            _skus_for_desc = item.get("sku_list") or []
            if len(_skus_for_desc) > 1:
                _summary = self._format_sku_summary(_skus_for_desc)
                if _summary:
                    desc = f"{desc}\n\n{_summary}" if desc else _summary
            if desc and self._fill_by_label("商品描述", desc[:5000]):
                result["filled"].append("商品描述")

            # 6) 价格/库存/编码
            fields = [
                ("售价", f"{float(price):.2f}" if str(price).strip() else ""),
                ("原价", item.get("market_price") or (f"{float(price):.2f}" if str(price).strip() else "")),
                ("库存", str(item.get("stock") or 1)),
                ("商家编码", item.get("merchant_code") or item.get("item_id") or ""),
            ]
            for label, value in fields:
                if not value:
                    result["skipped"].append(label)
                    continue
                if self._fill_by_label(label, value):
                    result["filled"].append(label)
                else:
                    result["skipped"].append(label)

            # 7) 成色（底部下拉）
            condition = item.get("condition") or "全新"
            if self._select_condition(condition):
                result["filled"].append(f"成色={condition}")
            else:
                result["skipped"].append("成色")

            # 多 SKU 仅提示：普通模式不支持多规格，已取单一售价
            sku_list = item.get("sku_list") or []
            if len(sku_list) > 1:
                result["sku_count"] = len(sku_list)
                self.log(
                    f"检测到 {len(sku_list)} 个 SKU，但普通商品模式不支持多规格，"
                    f"已按单一售价填写（多规格需升级鱼小铺）。"
                )

            result["ok"] = True
            if dry_run:
                self.log("✅ 已填写表单（dry-run，未提交）。请在浏览器中核对，确认无误再放开提交。")
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    # ── 鱼小铺多规格能力检测 ──────────────────────────────────
    def _detect_shop_capability(self) -> dict:
        """检测当前账号是否具备鱼小铺多规格能力。

        普通闲鱼号点「添加多规格深库存」会弹「请先升级闲鱼号为鱼小铺」，
        弹窗不渲染规格表。返回 {"enabled": bool, "reason": str}。

        实现：在发布页查找「多规格」「深库存」相关入口按钮是否存在且可点开
        规格编辑区（出现规格名/规格值输入或 SKU 价格表）。
        """
        find_js = r"""
        function vis(el){ if(!el) return false; var r=el.getBoundingClientRect();
          return r.width>0 && r.height>0; }
        var btns = document.querySelectorAll('button, a, span, div');
        var entry = null;
        for (var i=0;i<btns.length;i++){
          var t = (btns[i].innerText||'').trim();
          if (/多规格|深库存|添加规格/.test(t) && vis(btns[i])) { entry = t; break; }
        }
        return JSON.stringify({ entry: entry });
        """
        try:
            import json as _json
            info = _json.loads(self.tab.run_js(find_js) or "{}")
        except Exception as e:
            return {"enabled": False, "reason": f"探测异常: {e}"}

        entry = info.get("entry")
        if not entry:
            return {"enabled": False,
                    "reason": "未找到「多规格/深库存」入口（普通商品模式或账号未开通鱼小铺）。"}

        # 找到入口后尝试点击，看是否弹出升级提示（未开通）或渲染规格区（已开通）。
        click_js = r"""
        var btns = document.querySelectorAll('button, a, span, div');
        for (var i=0;i<btns.length;i++){
          var t = (btns[i].innerText||'').trim();
          if (/多规格|深库存|添加规格/.test(t)) { btns[i].click(); return true; }
        }
        return false;
        """
        try:
            self.tab.run_js(click_js)
            time.sleep(1.5)
        except Exception:
            pass

        check_js = r"""
        var body = (document.body.innerText||'');
        var needUpgrade = /升级.*鱼小铺|开通鱼小铺|请先升级/.test(body);
        var hasSpecEditor = !!document.querySelector(
          'input[placeholder*="规格名"], input[placeholder*="规格值"]');
        return JSON.stringify({ needUpgrade: needUpgrade, hasSpecEditor: hasSpecEditor });
        """
        try:
            import json as _json
            st = _json.loads(self.tab.run_js(check_js) or "{}")
        except Exception as e:
            return {"enabled": False, "reason": f"状态判定异常: {e}"}

        if st.get("needUpgrade"):
            return {"enabled": False, "reason": "账号未开通鱼小铺（平台提示需升级）。"}
        if st.get("hasSpecEditor"):
            return {"enabled": True, "reason": "鱼小铺多规格编辑区已渲染。"}
        return {"enabled": False, "reason": "未能确认多规格编辑区（DOM 未渲染）。"}

    # ── 鱼小铺多规格上架（需开通后用真实页面补全 DOM 流程） ──────
    def _fill_product_shop(self, item: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        """鱼小铺多规格模式上架。

        当前账号未开通鱼小铺时，平台会拦截「添加多规格深库存」弹窗，无法实测
        其真实 DOM。为遵循「只交付可落地、可验证的功能」，本方法先做能力检测：
          - 未开通：返回明确提示，不盲填无法验证的表单（避免形同虚设）。
          - 已开通：进入多规格填充流程（开通后用真实页面把 DOM 选择器补全）。

        闲鱼官方多规格已完整可用（XianyuLister），鱼小铺开通前建议用闲鱼官方
        发布多规格，或用闲管家普通模式（单规格 + 规格写入描述）。
        """
        result = {"ok": False, "filled": [], "skipped": [], "dry_run": dry_run,
                  "error": "", "mode": "shop"}
        if not self.tab:
            result["error"] = "浏览器未就绪，请先 open()"
            return result

        try:
            self._goto_publish()
            if not self._wait_form():
                result["error"] = "发布表单未渲染（可能登录失效）"
                return result

            cap = self._detect_shop_capability()
            result["shop_capability"] = cap
            if not cap.get("enabled"):
                msg = (
                    "鱼小铺多规格模式不可用：" + cap.get("reason", "未知原因") + "\n"
                    "建议：① 多规格请用「闲鱼官方」渠道发布（免费、已支持多规格）；"
                    "② 或用「闲管家·普通模式」按单一售价发布并把规格写入描述。"
                    "开通鱼小铺后此模式将启用逐 SKU 规格/深库存填充。"
                )
                self.log(msg)
                result["error"] = msg
                return result

            # —— 已开通鱼小铺：进入多规格填充 —— #
            # 注：以下流程需在真实开通账号的页面上把规格名/规格值/SKU 价格表/
            #     深库存/配图的选择器补全并实测。开通后在此实现，避免盲写。
            sku_list = item.get("sku_list") or []
            result["sku_count"] = len(sku_list)
            self.log(
                f"检测到鱼小铺多规格能力可用，待开通账号实测后补全 DOM 填充"
                f"（{len(sku_list)} 个 SKU）。"
            )
            result["error"] = "鱼小铺多规格填充流程待真实开通账号实测补全。"
            return result
        except Exception as e:
            result["error"] = str(e)
            return result


if __name__ == "__main__":
    lister = GoofishProLister(on_log=lambda m: print(m, flush=True))
    if lister.open():
        demo = {"title": "测试商品-请勿提交", "price": 9.9, "stock": 5,
                "merchant_code": "TEST001", "condition": "全新",
                "category_keyword": "连衣裙"}
        print(lister.fill_product(demo, dry_run=True))
