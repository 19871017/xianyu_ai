"""拼多多(PDD)商品采集器 - 反爬增强版 v2.0

核心反爬绕过策略:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. CDP stealth注入 (Page.addScriptToEvaluateOnNewDocument)
   - 在页面任何JS执行前注入，隐藏 navigator.webdriver
   - 清除CDP特有的 window.cdc_* 标记
   - 伪造 chrome.runtime / chrome.app 对象
   - 伪造 navigator.plugins / languages / platform
   - 修复 permissions API，避免 Notification 检测

2. 网络监听器拦截 PDD 自身API响应
   - 无需逆向 anti-content 加密！
   - 浏览器内 PDD JS 用真实 token 发请求 → 我们只拦截响应
   - 支持 yangkeduo.com / api.pinduoduo.com / apiv2.pinduoduo.com

3. 持久化 Chrome Profile (~/.pdd_collector_profile)
   - 一次扫码登录，Cookie 永久保存
   - 后续采集全程免登录

4. 移动端优先 (mobile.yangkeduo.com)
   - 移动端反爬规则相对宽松
   - API 响应结构更简单直接

5. 全局状态变量提取备用
   - window.__INITIAL_STATE__ / window.__pdd_state__
   - 部分 SSR 渲染下直接包含商品数据
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import re
import json
import time
import os
import hashlib
import random
import requests as _requests
from DrissionPage import Chromium
from config import IMAGE_DIR
from utils.helpers import ensure_dir, sanitize_filename
from utils.browser_config import get_chromium_options, check_browser_available
from engine.product_package import download_product_image_groups
from engine.pdd_full_package import enrich_pdd_product
from utils.login_manager import (
    save_cookies as _save_cookies,
    load_cookies as _load_cookies,
    _read_tab_cookies,
    _inject_cookies,
)


# ─── 持久化Profile目录 ───────────────────────────────────────
PDD_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".pdd_collector_profile")

# ─── Mobile UA（反检测效果更好）────────────────────────────────
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)
PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ─── URL 常量 ─────────────────────────────────────────────────
PDD_MOBILE_HOME   = "https://mobile.yangkeduo.com/"
PDD_MOBILE_SEARCH = "https://mobile.yangkeduo.com/search_result.html?search_key={kw}&page={page}"
PDD_PC_SEARCH     = "https://www.pinduoduo.com/goods.html?q={kw}"

# ─── 网络监听域名关键词 ───────────────────────────────────────
LISTEN_KEYWORDS = ["yangkeduo.com", "pinduoduo.com/api"]

# ═══════════════════════════════════════════════════════════════
# ★★★ Stealth JS - 在页面任何脚本执行前注入 ★★★
# ═══════════════════════════════════════════════════════════════
STEALTH_JS = r"""
(function() {
    'use strict';

    /* ── 1. 隐藏 webdriver 标记（最关键，PDD必查）── */
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined, configurable: true
        });
    } catch(e) {}

    /* ── 2. 清除 CDP 特有的 window 标记 ── */
    try {
        const cdcKeys = Object.getOwnPropertyNames(window)
            .filter(k => k.startsWith('cdc_') || k.startsWith('__cdc_') ||
                         k === '$chrome_asyncScriptInfo' || k === '$cdc_asdjflasutopfhvcZLmcfl_');
        cdcKeys.forEach(k => { try { delete window[k]; } catch(e) {} });
    } catch(e) {}

    /* ── 3. iOS Safari 指纹对齐（UA 是 iPhone，指纹也必须是 iPhone）──
       关键修复：之前注入的是桌面指纹（Win32 / window.chrome / PDF插件），
       与 iPhone UA 严重矛盾，是 PDD 软风控（假售罄+猜你喜欢）的主因。
       真 iOS Safari：platform='iPhone'，maxTouchPoints=5，无 window.chrome，
       vendor='Apple Computer, Inc.'，无桌面 PDF 插件。 */
    try {
        Object.defineProperty(navigator, 'platform',
            { get: () => 'iPhone', configurable: true });
        Object.defineProperty(navigator, 'maxTouchPoints',
            { get: () => 5, configurable: true });
        Object.defineProperty(navigator, 'vendor',
            { get: () => 'Apple Computer, Inc.', configurable: true });
        Object.defineProperty(navigator, 'hardwareConcurrency',
            { get: () => 4, configurable: true });
    } catch(e) {}

    /* ── 4. 语言设置 ── */
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh-Hans', 'zh'], configurable: true
        });
        Object.defineProperty(navigator, 'language', {
            get: () => 'zh-CN', configurable: true
        });
    } catch(e) {}

    /* ── 5. 移除桌面 Chrome 痕迹：iOS Safari 没有 window.chrome ── */
    try {
        if (window.chrome) { try { delete window.chrome; } catch(e) { window.chrome = undefined; } }
    } catch(e) {}

    /* ── 6. 修复 permissions.query（避免 Notification 检测）── */
    try {
        const origQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (origQuery) {
            window.navigator.permissions.query = function(parameters) {
                if (parameters && parameters.name === 'notifications') {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
                return origQuery.call(window.navigator.permissions, parameters);
            };
        }
    } catch(e) {}

    /* ── 7. 触摸事件支持探测（移动端应存在 ontouchstart）── */
    try {
        if (!('ontouchstart' in window)) {
            window.ontouchstart = null;
        }
    } catch(e) {}

})();
"""


class PddCollector:
    """拼多多商品采集器（反爬增强版 v2.0）

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    登录说明：
    首次使用时浏览器会打开拼多多，请手动扫码登录。
    登录状态会保存在 ~/.pdd_collector_profile，后续免登录。
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """

    def __init__(self, on_progress=None):
        self.on_progress = on_progress
        self.chromium = None
        self.tab = None
        self.items: list = []
        self.seen_ids: set = set()
        self.seen_img_md5: set = set()

    # ──────────────────────── 日志 & 浏览器 ────────────────────────

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)
        print(msg)  # 同时输出到控制台方便调试

    def _init_browser(self):
        """初始化浏览器：持久化Profile + 反检测参数"""
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")

        os.makedirs(PDD_PROFILE_DIR, exist_ok=True)
        co, _port = get_chromium_options(user_data_dir=PDD_PROFILE_DIR)

        # 核心反检测参数
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--exclude-switches=enable-automation")
        co.set_argument("--disable-automation")
        co.set_argument("--no-sandbox")
        co.set_argument("--window-size=1440,900")
        co.set_argument("--disable-infobars")
        co.set_argument("--disable-dev-shm-usage")
        # 设置 UA（Mobile UA 绕过效果更好）
        co.set_argument(f"--user-agent={MOBILE_UA}")

        self.chromium = Chromium(co)
        self.tab = self.chromium.latest_tab

        # CDP 注入 stealth JS（在每个新页面JS执行前注入）
        self._inject_stealth_js()

    def _inject_stealth_js(self):
        """通过 CDP Page.addScriptToEvaluateOnNewDocument 注入 stealth"""
        try:
            self.tab.run_cdp(
                "Page.addScriptToEvaluateOnNewDocument",
                source=STEALTH_JS
            )
            self._log("  ✓ Stealth JS 注入成功（CDP）")
        except Exception as e:
            # 有些版本 DrissionPage 方法名不同，兜底方案
            try:
                self.tab.run_cdp_loaded(
                    "Page.addScriptToEvaluateOnNewDocument",
                    source=STEALTH_JS
                )
                self._log("  ✓ Stealth JS 注入成功（CDP loaded）")
            except Exception:
                self._log(f"  ⚠ Stealth注入降级（直接JS执行）: {e}")
                # 降级：直接在当前页面执行
                try:
                    self.tab.run_js(STEALTH_JS)
                except Exception:
                    pass

    def _close_browser(self):
        if self.chromium:
            try:
                self.chromium.quit()
            except Exception:
                pass
            self.chromium = None
            self.tab = None

    def _safe_tab(self):
        try:
            _ = self.tab.url
        except Exception:
            if self.chromium:
                self.tab = self.chromium.latest_tab
        return self.tab

    # ──────────────────────── 登录检测 ────────────────────────

    def _is_logged_in(self) -> bool:
        """检查登录态 - Cookie(CDP) + DOM双重验证。

        PDDAccessToken 是 httpOnly cookie，``document.cookie`` 读不到，
        必须用 DrissionPage 的 ``tab.cookies()``（走 CDP）才能读到。
        """
        try:
            url = self._safe_tab().url or ""
            if "login" in url.lower() or "passport" in url.lower():
                return False

            # 第一步：Cookie 严格检查（CDP 读取，含 httpOnly）
            cookie_ok = False
            try:
                names_vals = {}
                for ck in (self.tab.cookies() or []):
                    name = ck.get("name") if isinstance(ck, dict) else getattr(ck, "name", "")
                    val = ck.get("value") if isinstance(ck, dict) else getattr(ck, "value", "")
                    if name:
                        names_vals[name] = val or ""
                if len(names_vals.get("PDDAccessToken", "")) >= 10:
                    cookie_ok = True
                elif len(names_vals.get("multi_sid", "")) >= 5:
                    cookie_ok = True
            except Exception:
                cookie_ok = False
            # 有有效 PDDAccessToken/multi_sid 即视为已登录。
            # 注意：不再做 DOM 文本二次校验——商品详情页 body 常为零宽占位符，
            # 匹配不到“退出登录”会误判未登录（这是之前反复掉登录的根因之一）。
            return bool(cookie_ok)
        except Exception:
            return False

    def _wait_for_login(self, timeout: int = 300) -> bool:
        """等待用户完成扫码登录（最多 timeout 秒）"""
        self._log("=" * 50)
        self._log("⚠️  请在弹出的浏览器中扫码登录拼多多")
        self._log("   登录成功后采集将自动继续")
        self._log("   登录状态会保存，下次无需重复登录")
        self._log("=" * 50)
        for i in range(timeout):
            time.sleep(1)
            try:
                if self._is_logged_in():
                    self._log("✅ 登录成功！开始采集...")
                    self._persist_cookies()
                    time.sleep(2)
                    return True
            except Exception:
                pass
            if i % 30 == 0 and i > 0:
                self._log(f"  ⏳ 等待登录... ({i}s/{timeout}s)")
        self._log("❌ 登录等待超时")
        return False

    # ──────────────────────── Cookie 持久化 ────────────────────────
    def _restore_cookies(self) -> int:
        """注入已保存的 Cookie（含 httpOnly 的 PDDAccessToken）。"""
        try:
            cookies = _load_cookies("pdd")
        except Exception:
            cookies = []
        if not cookies:
            return 0
        try:
            return _inject_cookies(self.tab, cookies)
        except Exception:
            return 0

    def _persist_cookies(self) -> bool:
        """登录成功后导出 Cookie 到 JSON。

        PDDAccessToken 是 session cookie（仅存内存），强杀浏览器会丢失，
        必须主动导出再注入，否则无法跨进程复用登录态。
        """
        try:
            cookies = _read_tab_cookies(self.tab)
        except Exception:
            cookies = []
        has_token = False
        for c in cookies or []:
            name = c.get("name") if isinstance(c, dict) else ""
            val = str((c.get("value") if isinstance(c, dict) else "") or "")
            if name == "PDDAccessToken" and len(val) >= 10:
                has_token = True
                break
            if name == "multi_sid" and len(val) >= 5:
                has_token = True
                break
        if not has_token:
            return False
        try:
            _save_cookies("pdd", cookies, extra={"profile": PDD_PROFILE_DIR})
            return True
        except Exception:
            return False

    def _open_and_check_login(self) -> bool:
        """打开首页 + 注入已存 Cookie + 校验登录态；登录则刷新持久化。"""
        self._safe_tab().get(PDD_MOBILE_HOME)
        time.sleep(2)
        n = self._restore_cookies()
        if n:
            self._log(f"  ↻ 注入已存 {n} 条 Cookie，刷新校验…")
            self._safe_tab().get(PDD_MOBILE_HOME)
            time.sleep(2)
        ok = self._is_logged_in()
        if ok:
            self._persist_cookies()
        return ok

    # ──────────────────────── 图片下载 ────────────────────────

    def _md5(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _download_image(self, url: str, save_dir: str, index: int) -> str | None:
        try:
            if not url or len(url) < 10:
                return None
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith("http"):
                return None

            # 去掉缩略图参数，拿原图
            clean_url = url.split("?")[0]

            headers = {
                "User-Agent": MOBILE_UA,
                "Referer": "https://mobile.yangkeduo.com/",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            resp = _requests.get(clean_url, timeout=15, headers=headers, allow_redirects=True)
            if resp.status_code != 200 or len(resp.content) < 500:
                return None

            img_data = resp.content
            md5 = self._md5(img_data)
            if md5 in self.seen_img_md5:
                return None
            self.seen_img_md5.add(md5)

            ext = ".jpg"
            if img_data[:8].startswith(b"\x89PNG"):
                ext = ".png"
            elif b"WEBP" in img_data[:12]:
                ext = ".webp"

            path = os.path.join(save_dir, f"img_{index:03d}{ext}")
            with open(path, "wb") as f:
                f.write(img_data)
            return path
        except Exception:
            return None

    # ──────────────────────── JSON 解析 ────────────────────────

    def _safe_price(self, price_val) -> float:
        """安全转换价格（PDD API 价格单位是「分」，需÷100）"""
        try:
            p = float(str(price_val).replace(",", "").replace("¥", "").strip())
            # PDD API 返回的通常是「分」：如 5990 → 59.90元
            if p > 500:
                return round(p / 100, 2)
            return round(p, 2)
        except Exception:
            return 0.0

    # 商品的强特征字段：必须命中其一才认定为「真商品」，
    # 避免把搜索热词等字符串列表 / 弱特征列表误判为商品列表
    GOODS_STRONG_KEYS = frozenset([
        "goods_id", "goodsId", "item_id", "itemId",
        "min_group_price", "minGroupPrice",
        "group_price", "groupPrice",
    ])

    def _is_goods_list(self, value) -> bool:
        """判断一个值是否为真正的商品列表。

        要求：非空 list、首元素是 dict、且 dict 含商品强特征字段。
        这样可排除拼多多搜索热词接口返回的字符串列表
        （如 ['耳机', '丝袜', ...]）以及其它弱特征列表。
        """
        if not isinstance(value, list) or not value:
            return False
        first = value[0]
        if not isinstance(first, dict):
            return False
        return bool(self.GOODS_STRONG_KEYS & set(first.keys()))

    def _find_goods_list(self, data, depth: int = 0) -> list:
        """递归从嵌套 JSON 中找商品列表"""
        if depth > 5:
            return []
        if self._is_goods_list(data):
            return data
        if isinstance(data, dict):
            # 优先路径
            for key_path in [
                ["result", "goods_list"], ["result", "items"], ["result", "list"],
                ["data", "goods_list"], ["data", "items"], ["data", "list"],
                ["goods_list"], ["items"], ["list"], ["goodsList"],
                ["result", "searchResult", "list"],
                ["result", "searchResult", "goods_list"],
            ]:
                cur = data
                try:
                    for k in key_path:
                        cur = cur[k]
                    if self._is_goods_list(cur):
                        return cur
                except (KeyError, TypeError):
                    pass

            # 递归搜索
            for v in data.values():
                found = self._find_goods_list(v, depth + 1)
                if found:
                    return found
        return []

    def _parse_item(self, raw: dict, source_url: str = "") -> dict | None:
        """将 PDD API 返回的单个商品数据标准化"""
        try:
            goods_id = str(
                raw.get("goods_id") or raw.get("goodsId") or
                raw.get("item_id") or raw.get("itemId") or
                raw.get("id") or ""
            ).strip()

            if not goods_id or goods_id in self.seen_ids:
                return None
            self.seen_ids.add(goods_id)

            # 标题
            title = str(
                raw.get("goods_name") or raw.get("goodsName") or
                raw.get("name") or raw.get("title") or
                raw.get("goods_desc") or raw.get("goodsDesc") or ""
            ).strip()[:200]

            if not title:
                return None

            # 价格（分 → 元）
            price_raw = (
                raw.get("min_group_price") or raw.get("minGroupPrice") or
                raw.get("group_price") or raw.get("groupPrice") or
                raw.get("normal_price") or raw.get("normalPrice") or
                raw.get("price") or raw.get("min_price") or 0
            )
            price_float = self._safe_price(price_raw)

            orig_price_raw = (
                raw.get("normal_price") or raw.get("normalPrice") or
                raw.get("market_price") or raw.get("marketPrice") or
                price_raw
            )
            orig_price_float = self._safe_price(orig_price_raw)

            # 销量
            sales = str(
                raw.get("sales_tip") or raw.get("salesTip") or
                raw.get("sold_count") or raw.get("soldCount") or
                raw.get("sell_num") or raw.get("sellNum") or "0"
            ).strip()

            # 描述
            desc = str(
                raw.get("goods_desc") or raw.get("goodsDesc") or
                raw.get("description") or raw.get("desc") or ""
            ).strip()[:2000]

            # 图片URLs
            img_urls = []
            for field in ["image_url", "imageUrl", "thumb_url", "thumbUrl",
                          "cover", "img", "pic"]:
                v = raw.get(field)
                if v and isinstance(v, str) and v not in img_urls:
                    img_urls.append(v)

            for field in ["goods_imgs", "goodsImgs", "images", "imgs", "gallery"]:
                imgs = raw.get(field) or []
                if isinstance(imgs, list):
                    for img in imgs:
                        if isinstance(img, str) and img not in img_urls:
                            img_urls.append(img)
                        elif isinstance(img, dict):
                            for k in ["url", "image_url", "src", "thumb"]:
                                if img.get(k) and img[k] not in img_urls:
                                    img_urls.append(img[k])
                                    break

            # 补全scheme
            img_urls = [
                ("https:" + u) if u.startswith("//") else u
                for u in img_urls if u and isinstance(u, str)
            ][:20]

            # 店铺
            store = str(
                raw.get("store_name") or raw.get("storeName") or
                raw.get("mall_name") or raw.get("mallName") or
                raw.get("shop_name") or raw.get("shopName") or ""
            ).strip()

            # 属性
            attrs: dict = {}
            for field in ["goods_property", "goodsProperty", "attrs", "properties"]:
                prop = raw.get(field)
                if isinstance(prop, list):
                    for p in prop:
                        if isinstance(p, dict):
                            k = p.get("key") or p.get("name") or p.get("k", "")
                            v = p.get("value") or p.get("v") or p.get("values", "")
                            if k:
                                attrs[str(k)] = str(v)
                    break

            item_link = (
                source_url or
                f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"
            )

            return {
                "item_id": f"pdd_{goods_id}",
                "platform": "pdd",
                "title": title,
                "original_title": title,
                "description": desc,
                "original_price": str(orig_price_float),
                "price": price_float,
                "image_urls": img_urls,
                "local_images": [],
                "image_dir": "",
                "attributes": attrs,
                "seller": store,
                "seller_credit": "",
                "wants": sales,
                "views": "0",
                "collects": "0",
                "link": item_link,
                "source_url": item_link,
                "source_item_id": goods_id,
            }
        except Exception:
            return None

    # ──────────────────────── 采集核心 ────────────────────────

    def _parse_ssr_item(self, g: dict, source_url: str = "") -> dict | None:
        """解析搜索页 SSR(ssrListData.list)的单个商品项。

        拼多多移动端搜索页是服务端渲染，商品列表直接挂在
        window.rawData.stores.store.data.ssrListData.list，
        无需逆向 anti_content。priceInfo 是用户可见显示价(元)，
        price 是分；优先用 priceInfo。
        """
        try:
            goods_id = str(g.get("goodsID") or g.get("goodsId") or "").strip()
            if not goods_id or goods_id in self.seen_ids:
                return None
            title = str(g.get("goodsName") or "").strip()[:200]
            if not title:
                return None
            self.seen_ids.add(goods_id)

            price = 0.0
            price_info = g.get("priceInfo")
            if price_info not in (None, ""):
                try:
                    price = round(float(str(price_info)), 2)
                except Exception:
                    price = 0.0
            if price <= 0:
                p = g.get("price")
                if isinstance(p, (int, float)) and p > 0:
                    price = round(float(p) / 100, 2)

            img = str(g.get("imgUrl") or g.get("longImgUrl") or "").strip()
            if img.startswith("//"):
                img = "https:" + img
            img_urls = [img] if img.startswith(("http://", "https://")) else []

            sales = str(g.get("salesTip") or "").strip()
            spec = str(g.get("specName") or "").strip()
            link = f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"

            return {
                "item_id": f"pdd_{goods_id}",
                "platform": "pdd",
                "title": title,
                "original_title": title,
                "description": "",
                "original_price": str(price),
                "price": price,
                "image_urls": img_urls,
                "local_images": [],
                "image_dir": "",
                "attributes": {"规格": spec} if spec else {},
                "seller": "",
                "seller_credit": "",
                "wants": sales,
                "views": "0",
                "collects": "0",
                "link": link,
                "source_url": link,
                "source_item_id": goods_id,
            }
        except Exception:
            return None

    def _extract_ssr_flip(self) -> str:
        """提取搜索页 flip 翻页令牌 + lastPage 标志(window.rawData)。"""
        js = r"""
        try {
          var d = window.rawData.stores.store.data;
          var sd = d.ssrListData || {};
          return JSON.stringify({flip: sd.flip || "", lastPage: !!sd.lastPage});
        } catch(e){ return "{}"; }
        """
        try:
            data = json.loads(self.tab.run_js(js) or "{}")
            self._ssr_last_page = bool(data.get("lastPage"))
            return str(data.get("flip") or "")
        except Exception:
            return ""

    def _extract_ssr_list(self) -> list:
        """从当前搜索页提取 SSR 商品列表(window.rawData)。"""
        js = r"""
        try {
          var rd = window.rawData;
          if (!rd || !rd.stores || !rd.stores.store || !rd.stores.store.data) return "[]";
          var d = rd.stores.store.data;
          var candidates = [];
          if (d.ssrListData && Array.isArray(d.ssrListData.list)) candidates.push(d.ssrListData.list);
          if (d.dataMap) { for (var k in d.dataMap) { if (d.dataMap[k] && Array.isArray(d.dataMap[k].list)) candidates.push(d.dataMap[k].list); } }
          for (var i=0;i<candidates.length;i++){ if (candidates[i].length) return JSON.stringify(candidates[i]); }
          return "[]";
        } catch(e){ return "[]"; }
        """
        try:
            raw_json = self.tab.run_js(js) or "[]"
            return json.loads(raw_json) or []
        except Exception as e:
            self._log(f"  \u2717 SSR 提取异常: {e}")
            return []

    def _collect_via_ssr(self, keyword: str, count: int, page: int = 1, flip: str = "") -> list:
        """\u2605 主方法：搜索页 SSR 提取(无需逆向 anti_content)。"""
        items = []
        if page > 1 and flip:
            from urllib.parse import quote
            search_url = PDD_MOBILE_SEARCH.format(kw=keyword, page=page) + "&flip=" + quote(flip)
        else:
            search_url = PDD_MOBILE_SEARCH.format(kw=keyword, page=page)
        self._log(f"  \U0001f310 [SSR模式] 搜索: {keyword} 第{page}页")
        self._safe_tab().get(search_url)
        time.sleep(random.uniform(2.0, 3.0))
        for _ in range(2):
            try:
                self.tab.run_js("window.scrollBy(0, window.innerHeight * 0.8);")
            except Exception:
                pass
            time.sleep(random.uniform(0.6, 1.0))

        goods = self._extract_ssr_list()
        self._ssr_hit = bool(goods)
        self._ssr_flip = self._extract_ssr_flip() or flip
        if not goods:
            self._log("  \u26a0 SSR 未取到商品列表")
            return []
        self._log(f"  \U0001f4e6 SSR 命中 {len(goods)} 个商品")

        for g in goods:
            if not isinstance(g, dict):
                continue
            item = self._parse_ssr_item(g, search_url)
            if item:
                items.append(item)
            if len(items) >= count:
                break
        return items

    def _collect_via_listener(
        self, keyword: str, count: int, page: int = 1
    ) -> list:
        """★ 主要方法：网络监听器直接拦截 PDD API 响应

        PDD 浏览器 JS 会用真实 anti-content token 发请求，
        我们只需监听响应，无需破解加密。
        """
        items = []
        search_url = PDD_MOBILE_SEARCH.format(kw=keyword, page=page)
        self._log(f"  🌐 [监听模式] 搜索: {keyword} 第{page}页")

        # 启动网络监听
        listen_started = False
        for target in LISTEN_KEYWORDS:
            try:
                self.tab.listen.start(target)
                listen_started = True
                self._log(f"  ✓ 监听启动: {target}")
                break
            except Exception as e:
                self._log(f"  ⚠ 监听 {target} 失败: {e}")

        if not listen_started:
            self._log("  ✗ 监听器启动失败，切换DOM模式")
            return []

        # 访问搜索页，滚动触发API请求
        self._safe_tab().get(search_url)
        time.sleep(3)

        for _ in range(4):
            self.tab.run_js("window.scrollBy(0, window.innerHeight * 0.7);")
            time.sleep(random.uniform(0.8, 1.5))

        # 收集网络数据包
        collected_raw: list = []
        consecutive_empty = 0
        max_packets = 80

        for _ in range(max_packets):
            try:
                # 等待最多3秒一个包
                packet = self.tab.listen.wait(count=1, timeout=3)
                if packet is None:
                    consecutive_empty += 1
                    if consecutive_empty >= 4:
                        break
                    continue
                consecutive_empty = 0

                packets = packet if isinstance(packet, list) else [packet]
                for p in packets:
                    try:
                        body = p.response.body
                        if not body:
                            continue

                        # 解析 JSON
                        if isinstance(body, dict):
                            data = body
                        elif isinstance(body, (str, bytes)):
                            body_str = body.decode() if isinstance(body, bytes) else body
                            if not body_str.strip().startswith("{"):
                                continue
                            try:
                                data = json.loads(body_str)
                            except Exception:
                                continue
                        else:
                            continue

                        # 提取商品列表
                        goods = self._find_goods_list(data)
                        if goods and len(goods) > 0:
                            self._log(f"  📦 捕获API响应！{len(goods)} 个商品")
                            collected_raw.extend(goods)
                            # 已经足够了就提前退出
                            if len(collected_raw) >= count:
                                break
                    except Exception:
                        continue

                if len(collected_raw) >= count:
                    break

            except Exception as e:
                consecutive_empty += 1
                if consecutive_empty >= 4:
                    break

        try:
            self.tab.listen.stop()
        except Exception:
            pass

        # 标准化解析
        for raw in collected_raw:
            item = self._parse_item(raw, search_url)
            item = self._ensure_full_product_item(item, raw)
            if item:
                items.append(item)
            if len(items) >= count:
                break

        self._log(f"  ✓ 监听模式: {len(items)} 个商品")
        return items

    def _collect_via_dom(self, keyword: str) -> list:
        """备用方法：DOM解析（stealth注入后成功率有所提升）"""
        self._log("  🔄 [DOM模式] 直接解析页面结构...")
        items = []
        search_url = PDD_MOBILE_SEARCH.format(kw=keyword, page=1)

        self._safe_tab().get(search_url)
        time.sleep(4)

        # 重新注入 stealth（防止页面覆盖）
        try:
            self.tab.run_js(STEALTH_JS)
        except Exception:
            pass

        # 滚动触发懒加载
        for _ in range(5):
            self.tab.run_js("window.scrollBy(0, 600);")
            time.sleep(random.uniform(0.6, 1.0))

        # 方法1：从全局状态变量提取（SSR数据）
        state_js = """
        try {
            var s = window.__INITIAL_STATE__ || window.__pdd_state__ ||
                    window.__PRELOADED_STATE__ || window.pddState || null;
            return s ? JSON.stringify(s) : null;
        } catch(e) { return null; }
        """
        try:
            state_raw = self.tab.run_js(state_js)
            if state_raw:
                state = json.loads(state_raw) if isinstance(state_raw, str) else state_raw
                goods = self._find_goods_list(state)
                if goods:
                    self._log(f"  ✓ 从全局状态变量提取 {len(goods)} 条数据")
                    for raw in goods:
                        item = self._parse_item(raw, search_url)
                        if item:
                            items.append(item)
        except Exception:
            pass

        if items:
            return items

        # 方法2：DOM选择器解析商品卡片
        dom_js = r"""
        try {
            var results = [];
            var seen = new Set();
            var cards = document.querySelectorAll(
                '[class*="search-item"], [class*="goods-item"], [class*="product-item"],' +
                '[class*="SearchResult"], [class*="GoodsCard"], [class*="item-wrapper"],' +
                '[class*="goods-card"], [class*="item-card"]'
            );

            if (cards.length === 0) {
                cards = document.querySelectorAll('a[href*="goods_id"]');
            }

            cards.forEach(function(card) {
                try {
                    var link = '';
                    var a = card.tagName === 'A' ? card : card.querySelector('a[href*="goods_id"]');
                    if (a) link = a.href || '';
                    if (!link) return;

                    var m = link.match(/goods_id=(\d+)/);
                    if (!m) return;
                    var gid = m[1];
                    if (seen.has(gid)) return;
                    seen.add(gid);

                    var titleEl = card.querySelector(
                        '[class*="title"], [class*="name"], [class*="desc"], h3, h4, p'
                    );
                    var title = titleEl ? titleEl.textContent.trim() : '';
                    if (!title || title.length < 3) return;

                    var priceEl = card.querySelector('[class*="price"], [class*="Price"]');
                    var priceText = priceEl ? priceEl.textContent.replace(/[^\d.]/g,'') : '0';
                    var price = parseFloat(priceText) || 0;

                    var imgEl = card.querySelector('img[src], img[data-src]');
                    var imgUrl = imgEl ? (imgEl.src || imgEl.dataset.src || '') : '';

                    var salesEl = card.querySelector('[class*="sales"], [class*="sold"], [class*="已拼"]');
                    var sales = salesEl ? salesEl.textContent.trim() : '';

                    results.push({
                        goods_id: gid,
                        goods_name: title,
                        min_group_price: price,
                        image_url: imgUrl,
                        sales_tip: sales,
                        source_link: link
                    });
                } catch(e) {}
            });

            return JSON.stringify(results);
        } catch(e) { return '[]'; }
        """
        try:
            raw_json = self.tab.run_js(dom_js) or "[]"
            raw_items = json.loads(raw_json)
            for raw in raw_items:
                item = self._parse_item(raw, search_url)
                item = self._ensure_full_product_item(item, raw)
                if item:
                    items.append(item)
            self._log(f"  ✓ DOM解析: {len(items)} 个商品")
        except Exception as e:
            self._log(f"  ✗ DOM解析失败: {e}")

        return items


    def _ensure_full_product_item(self, item: dict | None, raw: dict | None = None, use_dom: bool = False):
        """补齐拼多多完整商品包字段：SKU、主图、详情图、SKU图、售后等。"""
        try:
            return enrich_pdd_product(item, raw=raw, tab=self.tab if use_dom else None, logger=self._log)
        except Exception as e:
            self._log(f"  ⚠ 完整商品包增强失败: {e}")
            return item

    def _batch_download_images(self, items: list) -> list:
        """批量下载图片，并按主图/详情图/SKU图分组保存。"""
        results = []
        for item in items:
            goods_id = item.get("source_item_id", "unknown")
            item_dir = os.path.join(IMAGE_DIR, f"pdd_{sanitize_filename(str(goods_id))}")
            ensure_dir(item_dir)

            try:
                item = download_product_image_groups(item, self._download_image, item_dir)
            except Exception as e:
                self._log(f"  ⚠ 分组下载失败，回退旧图片下载: {e}")
                local_images = []
                for idx, img_url in enumerate(item.get("image_urls", [])[:8]):
                    saved = self._download_image(img_url, item_dir, idx)
                    if saved:
                        local_images.append(saved)
                item["local_images"] = local_images
                item["main_images"] = local_images

            item["image_dir"] = item_dir
            results.append(item)

        return results

    # ──────────────────────── 公开接口 ────────────────────────

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        """关键词搜索采集

        Args:
            keyword: 搜索关键词
            count: 目标采集数量（最多200）

        Returns:
            标准化商品列表
        """
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            # 先访问主页，确认/完成登录（自动注入已存 Cookie）
            self._log("正在打开拼多多...")
            if not self._open_and_check_login():
                logged_in = self._wait_for_login(300)
                if not logged_in:
                    raise Exception("登录超时，请重新运行并完成登录")
            else:
                self._log("✅ 已登录（使用保存的Cookie）")

            # 分页采集(flip token 翻页，降低重复率)
            self._ssr_flip = ""
            self._ssr_last_page = False
            self._ssr_hit = False
            page = 1
            max_pages = min(20, (count // 15) + 3)
            empty_streak = 0

            while len(self.items) < count and page <= max_pages:
                need = count - len(self.items)
                self._log(f"\n📄 第 {page} 页（还需 {need} 个）...")

                # 主方法：搜索页 SSR 提取（flip 翻页）
                page_items = self._collect_via_ssr(keyword, need, page, self._ssr_flip)

                # 仅当 SSR 完全失效(连原始列表都没有)才走兜底
                if not page_items and not self._ssr_hit:
                    self._log("  SSR 未获取数据，切换监听模式...")
                    page_items = self._collect_via_listener(keyword, need, page)
                    if not page_items:
                        self._log("  监听未获取数据，切换DOM模式...")
                        page_items = self._collect_via_dom(keyword)
                    if not page_items:
                        self._log("  ⚠ 三种模式均无数据，停止")
                        break

                if page_items:
                    self._log(f"  📥 下载 {len(page_items)} 个商品的图片...")
                    page_items = self._batch_download_images(page_items)
                    self.items.extend(page_items)
                    self._log(f"  第{page}页完成，累计 {len(self.items)} 个")
                    empty_streak = 0
                else:
                    empty_streak += 1
                    self._log(f"  第{page}页无新增(连续{empty_streak}次)")
                    if empty_streak >= 3:
                        self._log("  连续多页无新增，停止")
                        break

                page += 1
                if self._ssr_last_page:
                    self._log("  已到最后一页")
                    break
                if len(self.items) < count:
                    time.sleep(random.uniform(1.5, 3.0))

            result = self.items[:count]
            self._log(f"\n✅ 拼多多采集完成：{len(result)} 个商品")
            return result

        except Exception as e:
            raise Exception(f"拼多多采集失败: {e}")
        finally:
            self._close_browser()

    def ensure_login(self, timeout: int = 300) -> bool:
        """独立登录入口（采集前调用）"""
        try:
            self._init_browser()
            self._log("正在打开拼多多...")
            if self._open_and_check_login():
                self._log("✅ 已登录（使用保存的Cookie）")
                return True
            return self._wait_for_login(timeout)
        except Exception as e:
            self._log(f"登录初始化失败: {e}")
            return False
        finally:
            self._close_browser()

    def check_login_status(self) -> bool:
        """检查登录态（不阻塞）"""
        try:
            self._init_browser()
            return self._open_and_check_login()
        except Exception:
            return False
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个商品链接直采

        支持:
          https://mobile.yangkeduo.com/goods.html?goods_id=xxx
          https://yangkeduo.com/goods.html?goods_id=xxx
          https://www.pinduoduo.com/goods.html?goods_id=xxx
        """
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            # 检查登录态（自动注入已存 Cookie）
            self._log("检查登录状态...")
            if not self._open_and_check_login():
                logged_in = self._wait_for_login(300)
                if not logged_in:
                    raise Exception("登录超时，请先点击'登录账号'按钮完成登录")
            else:
                self._log("✅ 已登录")

            m = re.search(r"goods_id=(\d+)", url)
            goods_id = m.group(1) if m else ""
            self._log(f"采集拼多多商品: goods_id={goods_id or url[:50]}")

            # 转为移动端URL
            if goods_id and "yangkeduo.com" not in url:
                url = f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"

            # 启动监听
            try:
                self.tab.listen.start("yangkeduo.com")
            except Exception:
                pass

            self._safe_tab().get(url)
            time.sleep(5)

            # 滚动触发懒加载
            for _ in range(3):
                self.tab.run_js("window.scrollBy(0, 400);")
                time.sleep(0.8)

            # 等待并解析API响应
            found = False
            consecutive_empty = 0
            for _ in range(40):
                try:
                    packet = self.tab.listen.wait(count=1, timeout=2)
                    if packet is None:
                        consecutive_empty += 1
                        if consecutive_empty >= 5:
                            break
                        continue
                    consecutive_empty = 0
                    packets = packet if isinstance(packet, list) else [packet]
                    for p in packets:
                        try:
                            body = p.response.body
                            if not body:
                                continue
                            data = body if isinstance(body, dict) else json.loads(
                                body.decode() if isinstance(body, bytes) else body
                            )
                            goods = self._find_goods_list(data)
                            if goods:
                                item = self._parse_item(goods[0], url)
                                item = self._ensure_full_product_item(item, goods[0], use_dom=True)
                                if item:
                                    self.items.append(item)
                                    found = True
                        except Exception:
                            continue
                    if found:
                        break
                except Exception:
                    consecutive_empty += 1
                    if consecutive_empty >= 5:
                        break
            try:
                self.tab.listen.stop()
            except Exception:
                pass

            if not found:
                # DOM备用
                raw = self.tab.run_js(r"""
                try {
                    var d = {};
                    d.goods_name = document.querySelector('h1,[class*="title"]')?.textContent?.trim()
                                   || document.title.replace(/[-_|].*/,'').trim();
                    var priceEl = document.querySelector('[class*="price"],[class*="Price"]');
                    d.min_group_price = priceEl ? parseFloat(priceEl.textContent.replace(/[^\d.]/g,''))||0 : 0;
                    d.image_url = document.querySelector('img[src*="yangkeduo"],img[src*="pddpic"]')?.src||'';
                    d.goods_id = location.href.match(/goods_id=(\d+)/)?.[1]||'';
                    d.sales_tip = document.querySelector('[class*="sales"],[class*="sold"]')?.textContent?.trim()||'';
                    return JSON.stringify(d);
                } catch(e) { return '{}'; }
                """) or "{}"
                try:
                    raw_data = json.loads(raw)
                    item = self._parse_item(raw_data, url)
                    item = self._ensure_full_product_item(item, raw_data, use_dom=True)
                    if item:
                        self.items.append(item)
                except Exception:
                    pass

            if self.items:
                self.items = self._batch_download_images(self.items)
                self._log(f"✅ 采集成功: {self.items[0].get('title','')[:40]}")
            else:
                self._log("⚠ 未能提取商品数据，请检查链接或登录状态")

            return self.items

        except Exception as e:
            raise Exception(f"拼多多商品采集失败: {e}")
        finally:
            self._close_browser()
