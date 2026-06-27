"""淘宝/天猫商品采集器 - 登录态持久化 + DrissionPage 浏览器采集

采集流程:
1. 复用闲鱼采集器的淘宝登录态(同一个淘宝账号), profile 目录共用 ~/.xf_xianyu_collector_profile
2. 搜索采集: s.taobao.com 搜索关键词 → 滚动加载 → 提取商品链接 → 逐个采集详情页
3. 链接采集: 直接传入 item.taobao.com / detail.tmall.com 商品 URL 采集

技术要点:
- 淘宝详情页(ICE/React)把 SKU 内嵌在 skuBase + skuCore 两块 JSON 里, 比抓 DOM 稳定
- 主图来自 componentsVO.headImageVO.images
- 价格 priceMoney 单位是"分", 由 taobao_sku_parser 统一换算
- 图片用 requests 下载, 带 Referer 头, MD5 + dHash 去重 + 有效图校验
- 连续访问详情页可能触发滑块验证码, 检测到则暂停等待人工处理
"""
import time
import re
import json
import hashlib
import os
import requests
from urllib.parse import quote, urlparse, parse_qs
from DrissionPage import Chromium
from config import IMAGE_DIR
from utils.helpers import ensure_dir, sanitize_filename
from utils.browser_config import get_chromium_options, check_browser_available
from engine.taobao_sku_parser import parse_sku_from_html, extract_head_images
from utils.image_dedup import dhash, is_near_duplicate, is_valid_product_image


# ─── 持久化Profile目录(与闲鱼采集器共用淘宝登录态) ──────────────
TAOBAO_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".xf_xianyu_collector_profile")

# ─── URL 常量 ─────────────────────────────────────────────────
LOGIN_URL = "https://login.taobao.com/"
SEARCH_URL = "https://s.taobao.com/search?q={kw}"
HOME_URL = "https://www.taobao.com/"

# ─── UA ───────────────────────────────────────────────────────
PC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ─── Stealth JS ───────────────────────────────────────────────
STEALTH_JS = r"""
(function() {
    'use strict';
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
    } catch(e) {}
    try {
        const cdcKeys = Object.getOwnPropertyNames(window)
            .filter(k => k.startsWith('cdc_') || k.startsWith('__cdc_'));
        cdcKeys.forEach(k => { try { delete window[k]; } catch(e) {} });
    } catch(e) {}
    try {
        if (!window.chrome || typeof window.chrome !== 'object') {
            Object.defineProperty(window, 'chrome', { value: {}, writable: true, configurable: true });
        }
    } catch(e) {}
})();
"""


class TaobaoCollector:
    """淘宝/天猫商品采集器

    支持两种模式:
    1. 关键词搜索采集 (s.taobao.com/search)
    2. 商品链接直接采集 (item.taobao.com/item.htm?id=xxx, detail.tmall.com/item.htm?id=xxx)

    登录态与闲鱼采集器共用(同一个淘宝账号), 持久化保存到 ~/.xf_xianyu_collector_profile
    """

    def __init__(self, on_progress=None):
        self.chromium = None
        self.tab = None
        self.items = []
        self.seen_ids = set()
        self.seen_img_md5 = set()
        self.seen_img_dhash = []
        self.on_progress = on_progress

    # ═══════════════════════════════════════════════════════════
    #  内部工具
    # ═══════════════════════════════════════════════════════════

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)

    def _init_browser(self):
        """初始化浏览器：持久化Profile + 反检测参数"""
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")

        os.makedirs(TAOBAO_PROFILE_DIR, exist_ok=True)
        co, _port = get_chromium_options(user_data_dir=TAOBAO_PROFILE_DIR)

        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-sandbox")
        co.set_argument("--window-size=1440,900")
        co.set_argument("--disable-infobars")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument(f"--user-agent={PC_UA}")

        self.chromium = Chromium(co)
        self.tab = self.chromium.latest_tab

        try:
            self.tab.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=STEALTH_JS)
        except Exception:
            try:
                self.tab.run_cdp_loaded("Page.addScriptToEvaluateOnNewDocument", source=STEALTH_JS)
            except Exception:
                try:
                    self.tab.run_js(STEALTH_JS)
                except Exception:
                    pass

    def _close_browser(self):
        """关闭浏览器（保留Profile数据）"""
        if self.chromium:
            try:
                self.chromium.quit()
            except Exception:
                pass
            self.chromium = None
            self.tab = None

    def _safe_tab(self):
        """安全获取tab，失效时自动重新获取"""
        try:
            _ = self.tab.url
        except Exception:
            if self.chromium:
                self.tab = self.chromium.latest_tab
        return self.tab

    # ═══════════════════════════════════════════════════════════
    #  登录管理
    # ═══════════════════════════════════════════════════════════

    def _read_cookie_names(self) -> set:
        """读取当前标签的所有 Cookie 名称（含 httpOnly）。"""
        names = set()
        try:
            tab = self._safe_tab()
            cookies = tab.cookies()
            try:
                for c in cookies:
                    if isinstance(c, dict):
                        name = c.get("name") or c.get("Name") or ""
                        if name:
                            names.add(name)
            except Exception:
                pass
            if not names:
                try:
                    as_dict = cookies.as_dict()
                    if isinstance(as_dict, dict):
                        names.update(as_dict.keys())
                except Exception:
                    pass
        except Exception:
            pass
        return names

    def _is_logged_in(self) -> bool:
        """检查是否已登录淘宝 - 用 DrissionPage cookies() 读取(含 httpOnly)

        unb 是淘系核心用户ID, 存在即视为已登录; cookie17/_nk_/sg 等作为辅助佐证。
        """
        try:
            tab = self._safe_tab()
            current_url = tab.url or ""
            if "login.taobao.com" in current_url or "login.tmall.com" in current_url:
                return False

            names = self._read_cookie_names()
            if not names:
                return False
            if "unb" in names:
                return True
            aux_keys = {"cookie17", "_nk_", "sg", "_l_g_", "lid", "cancelledSubSites", "_tb_token_"}
            if aux_keys & names:
                return True
            return False
        except Exception:
            return False

    def _ensure_login(self, timeout: int = 300) -> bool:
        """确保已登录，未登录则等待用户扫码"""
        tab = self._safe_tab()

        self._log("正在检查淘宝登录状态...")
        tab.get(HOME_URL)
        time.sleep(3)

        if self._is_logged_in():
            self._log("✅ 已登录（使用保存的Cookie）")
            return True

        self._log("⚠️  未登录，正在打开淘宝登录页面...")
        self._log("=" * 50)
        self._log("请在弹出的浏览器中扫码登录淘宝")
        self._log("登录成功后采集将自动继续")
        self._log(f"登录状态会保存到 {TAOBAO_PROFILE_DIR}")
        self._log("下次无需重复登录")
        self._log("=" * 50)

        tab.get(LOGIN_URL)
        time.sleep(2)

        for i in range(timeout):
            time.sleep(1)
            try:
                current_url = self._safe_tab().url or ""
                if ("login.taobao.com" not in current_url and
                        "login.tmall.com" not in current_url):
                    if self._is_logged_in():
                        self._log("✅ 登录成功！开始采集...")
                        time.sleep(2)
                        return True
            except Exception:
                pass

            if i % 30 == 0 and i > 0:
                self._log(f"  ⏳ 等待登录... ({i}s/{timeout}s)")

        self._log("❌ 登录等待超时")
        return False

    def ensure_login(self, timeout: int = 300) -> bool:
        """独立登录入口（采集前调用）"""
        try:
            self._init_browser()
            return self._ensure_login(timeout)
        except Exception as e:
            self._log(f"登录初始化失败: {e}")
            return False
        finally:
            self._close_browser()

    def check_login_status(self) -> bool:
        """检查登录态（不阻塞）"""
        try:
            self._init_browser()
            tab = self._safe_tab()
            tab.get(HOME_URL)
            time.sleep(3)
            return self._is_logged_in()
        except Exception:
            return False
        finally:
            self._close_browser()

    def _bring_browser_to_front(self):
        """把采集浏览器窗口激活到前台。"""
        try:
            tab = self._safe_tab()
            try:
                tab.set.activate()
            except Exception:
                pass
        except Exception:
            pass
        try:
            import subprocess
            import sys
            if sys.platform == "darwin":
                for app in ("Chromium", "Google Chrome"):
                    try:
                        subprocess.run(
                            ["osascript", "-e", f'tell application "{app}" to activate'],
                            capture_output=True, timeout=3,
                        )
                    except Exception:
                        continue
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  风控验证码
    # ═══════════════════════════════════════════════════════════

    def _detect_captcha(self) -> bool:
        """检测当前页是否被风控验证码拦截(滑块/安全验证)。"""
        try:
            tab = self._safe_tab()
            title = ""
            try:
                title = tab.title or ""
            except Exception:
                pass
            if "验证码" in title or "安全验证" in title:
                return True
            url = (tab.url or "").lower()
            if "punish" in url or "_____tmd_____" in url or "captcha" in url:
                return True
            hit = tab.run_js('''
            try {
                if (document.querySelector('#baxia-dialog-content, .nc-container, #nc_1_wrapper, [id*="nocaptcha"]')) return true;
                var t = (document.body && document.body.innerText) ? document.body.innerText : '';
                if (t.indexOf('请拖动滑块') >= 0 || t.indexOf('安全验证') >= 0 || t.indexOf('验证码拦截') >= 0) return true;
                return false;
            } catch(e) { return false; }
            ''')
            return bool(hit)
        except Exception:
            return False

    def _try_auto_solve_slider(self) -> bool:
        """尝试程序化拖动 nc 滑块验证码(拟人化轨迹)。

        淘宝/天猫详情页风控常是 nc 滑块(#nc_1_n1z / .btn_slide)。用 DrissionPage
        的 actions 模拟"按住-分段移动-松开", 多数情况下能直接通过, 失败再回退人工。

        Returns:
            True 验证已通过; False 未找到滑块或拖动后仍被拦截。
        """
        import random
        tab = self._safe_tab()
        btn = None
        for sel in ('.nc_iconfont.btn_slide', '#nc_1_n1z', '.btn_slide',
                    '.nc-lang-cnt .btn_slide', 'span[class*="btn_slide"]'):
            try:
                e = tab.ele(sel, timeout=1)
                if e:
                    btn = e
                    break
            except Exception:
                continue
        if not btn:
            return False
        try:
            ac = tab.actions
            ac.move_to(btn)
            ac.hold(btn)
            moved = 0
            track_w = 300
            while moved < track_w:
                step = random.randint(8, 22)
                moved += step
                ac.move(step, random.randint(-2, 2), duration=0.02)
                time.sleep(random.uniform(0.005, 0.02))
            time.sleep(0.3)
            ac.release()
        except Exception:
            return False
        # 等待结果
        for _ in range(8):
            time.sleep(1)
            if not self._detect_captcha():
                return True
        return False

    def _wait_captcha_cleared(self, timeout: int = 180) -> bool:
        """检测到验证码时, 先自动尝试拖动滑块, 失败再提示人工, 轮询等待通过。"""
        if not self._detect_captcha():
            return True
        # 先尝试程序化自动拖动滑块(多数情况可直接通过), 最多试 3 次
        for attempt in range(3):
            self._log(f"⚙️  检测到滑块验证, 自动尝试拖动({attempt + 1}/3)...")
            if self._try_auto_solve_slider():
                self._log("✅ 自动验证通过，继续采集...")
                time.sleep(1)
                return True
            time.sleep(1)
            if not self._detect_captcha():
                self._log("✅ 验证已通过，继续采集...")
                return True
        # 自动失败, 回退人工
        self._bring_browser_to_front()
        self._log("=" * 50)
        self._log("⚠️  自动验证未通过，触发淘宝安全验证(滑块验证码)")
        self._log("请在弹出的浏览器窗口手动完成验证")
        self._log("完成后采集会自动继续...")
        self._log("=" * 50)
        for i in range(timeout):
            time.sleep(1)
            if not self._detect_captcha():
                self._log("✅ 验证已通过，继续采集...")
                return True
            if i % 30 == 0 and i > 0:
                self._log(f"  ⏳ 等待人工验证... ({i}s/{timeout}s)")
        self._log("❌ 验证等待超时，跳过该商品")
        return False

    # ═══════════════════════════════════════════════════════════
    #  图片下载
    # ═══════════════════════════════════════════════════════════

    def _md5(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _clean_image_url(self, url: str) -> str:
        """清理图片URL，去掉尺寸后缀获取大图"""
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        clean = re.sub(r'_\d+x\d+(xz)?(?=\.(jpg|jpeg|png|webp))', '', url, flags=re.I)
        clean = re.sub(r'\.(jpg|jpeg|png)_\.webp$', r'.\1', clean, flags=re.I)
        return clean

    def _download_and_dedup_image(self, url: str, save_dir: str, index: int,
                                  md5_pool: set | None = None,
                                  dhash_pool: list | None = None) -> dict | None:
        """下载图片并去重（有效图校验 + 字节级 MD5 + 感知 dHash）"""
        if md5_pool is None:
            md5_pool = self.seen_img_md5
        if dhash_pool is None:
            dhash_pool = self.seen_img_dhash
        try:
            if not url or len(url) < 10:
                return None

            clean_url = self._clean_image_url(url)
            headers = {
                "User-Agent": PC_UA,
                "Referer": "https://item.taobao.com/",
            }

            resp = requests.get(clean_url, timeout=15, headers=headers)
            if resp.status_code != 200 or len(resp.content) < 500:
                resp = requests.get(url, timeout=15, headers=headers)
                if resp.status_code != 200 or len(resp.content) < 500:
                    return None

            img_data = resp.content

            # 排除 SVG 图标/占位图/超小 UI 元素
            if not is_valid_product_image(img_data):
                return None

            md5 = self._md5(img_data)
            if md5 in md5_pool:
                return None

            img_hash = dhash(img_data)
            if is_near_duplicate(img_hash, dhash_pool):
                return None

            md5_pool.add(md5)
            if img_hash is not None:
                dhash_pool.append(img_hash)

            ext = ".jpg"
            if img_data[:8].startswith(b"\x89PNG"):
                ext = ".png"
            elif b"WEBP" in img_data[:12]:
                ext = ".webp"

            save_path = os.path.join(save_dir, f"img_{index:03d}{ext}")
            with open(save_path, "wb") as f:
                f.write(img_data)

            return {"path": save_path, "md5": md5, "url": url}
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════
    #  URL解析
    # ═══════════════════════════════════════════════════════════

    def _extract_item_id(self, url: str) -> str:
        """从URL中提取淘宝/天猫商品ID"""
        if not url:
            return ""
        if "id=" in url:
            try:
                vid = parse_qs(urlparse(url).query).get("id", [""])[0]
                if vid:
                    return vid
            except Exception:
                pass
        match = re.search(r'/item/(\d+)', url)
        if match:
            return match.group(1)
        match = re.search(r'(\d{8,})', url)
        if match:
            return match.group(1)
        return ""

    # ═══════════════════════════════════════════════════════════
    #  搜索页链接采集
    # ═══════════════════════════════════════════════════════════

    def _collect_search_links(self, keyword: str, count: int = 50) -> list[str]:
        """搜索关键词，提取商品详情页链接(淘宝搜索页是懒加载 SPA, 需滚动)。"""
        tab = self._safe_tab()
        encoded_kw = quote(keyword)
        url = SEARCH_URL.format(kw=encoded_kw)

        self._log(f"正在搜索: {keyword}")
        tab.get(url)
        time.sleep(5)

        current_url = tab.url or ""
        if "login.taobao.com" in current_url:
            self._log("⚠️  搜索页跳转到登录页，登录态已失效")
            return []

        links = []
        seen = set()
        last_height = 0
        scroll_attempts = 0
        max_scroll = 30

        while len(links) < count and scroll_attempts < max_scroll:
            item_links = tab.run_js('''
                try {
                    var a = document.querySelectorAll('a[href*="item.htm"], a[href*="item.taobao.com"], a[href*="detail.tmall.com"]');
                    var result = [];
                    for(var i=0; i<a.length; i++) {
                        var h = a[i].href || '';
                        if(h.indexOf('id=') >= 0 || h.indexOf('/item/') >= 0) {
                            result.push(h);
                        }
                    }
                    return JSON.stringify(result);
                } catch(e) { return '[]'; }
            ''') or '[]'
            item_links = json.loads(item_links) if isinstance(item_links, str) else (item_links or [])

            for link in item_links:
                item_id = self._extract_item_id(link)
                if not item_id:
                    continue
                if item_id in seen:
                    continue
                seen.add(item_id)
                clean_link = f"https://item.taobao.com/item.htm?id={item_id}"
                links.append(clean_link)
                self._log(f"发现商品 {len(links)}/{count}: {item_id}")

            if len(links) >= count:
                break

            tab.scroll.to_bottom()
            time.sleep(2)
            new_height = tab.run_js("return document.body.scrollHeight") or 0
            if new_height == last_height:
                scroll_attempts += 1
                time.sleep(1)
            else:
                scroll_attempts = 0
            last_height = new_height

        return links[:count]

    # ═══════════════════════════════════════════════════════════
    #  详情页采集
    # ═══════════════════════════════════════════════════════════

    def _scrape_detail_page(self, url: str) -> dict | None:
        """采集淘宝/天猫商品详情页

        采集内容: 标题、价格(SKU 最低价)、描述、属性、图片(主图)、SKU(多规格多价格)
        """
        tab = self._safe_tab()
        try:
            tab.get(url)
            time.sleep(4)
            for _ in range(5):
                try:
                    tab.scroll.down(800)
                except Exception:
                    pass
                time.sleep(0.6)

            current_url = tab.url or ""
            if "login.taobao.com" in current_url or "login.tmall.com" in current_url:
                self._log(f"  ⚠ 详情页无法访问（可能未登录）: {url[:60]}...")
                return None

            if self._detect_captcha():
                if not self._wait_captcha_cleared():
                    return None
                tab = self._safe_tab()

            item_id = self._extract_item_id(url)
            html = tab.html or ""

            # ── 标题: document.title 形如 "{标题}-tmall.com天猫" / "{标题}-淘宝网" ──
            title = tab.run_js('''
                try {
                    var t = (document.title || '')
                              .replace(/-\s*tmall\.com\s*天猫.*$/i, '')
                              .replace(/-\s*淘宝网.*$/i, '')
                              .replace(/【.*?】\s*$/, '')
                              .trim();
                    if (t && t.length >= 2) return t.substring(0, 200);
                    var h1 = document.querySelector('h1');
                    if (h1 && h1.textContent.trim()) return h1.textContent.trim().substring(0, 200);
                    return (document.title || '').trim().substring(0, 200);
                } catch(e) { return ''; }
            ''') or ""
            if not title:
                title = (tab.title or "").strip()

            # ── 属性: 商品参数表 ──
            attrs_raw = tab.run_js('''
                try {
                    var attrs = {};
                    var items = document.querySelectorAll('[class*="infoItem"], [class*="attributes"] li, [class*="Attributes"] li, [class*="param"] li');
                    items.forEach(function(el) {
                        var text = el.textContent.trim();
                        if(text.includes('：') || text.includes(':')) {
                            var parts = text.split(/[：:]/);
                            if(parts.length >= 2) {
                                var key = parts[0].trim();
                                var val = parts.slice(1).join(':').trim().substring(0, 100);
                                if(key && val && key.length < 20) attrs[key] = val;
                            }
                        }
                    });
                    return JSON.stringify(attrs);
                } catch(e) { return '{}'; }
            ''') or '{}'
            try:
                attrs_dict = json.loads(attrs_raw)
            except Exception:
                attrs_dict = {}

            # ── 卖家/店铺 ──
            seller = tab.run_js('''
                try {
                    var el = document.querySelector('[class*="shopName"], [class*="ShopName"], [class*="shop-name"], [class*="slardar"]');
                    if(el) return el.textContent.trim().substring(0, 50);
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # ── SKU 多规格多价格 (内嵌 skuBase + skuCore, 最稳定) ──
            sku_list = []
            try:
                sku_list = parse_sku_from_html(html)
            except Exception as e:
                self._log(f"  ⚠ SKU 解析异常: {e}")

            # ── 主图 (componentsVO.headImageVO.images) ──
            image_urls = []
            try:
                image_urls = extract_head_images(html, limit=30)
            except Exception:
                image_urls = []

            # 回退: DOM 抓主图
            if not image_urls:
                image_urls_raw = tab.run_js('''
                    try {
                        var imgs = [];
                        var seen = new Set();
                        var els = document.querySelectorAll('[class*="thumbnail"] img, [class*="mainPic"] img, [class*="PicGallery"] img, [class*="gallery"] img');
                        els.forEach(function(img) {
                            var src = img.src || img.dataset.src || '';
                            if(src && src.length > 40 && src.indexOf('alicdn') >= 0 && !seen.has(src)) {
                                seen.add(src);
                                imgs.push(src);
                            }
                        });
                        return JSON.stringify(imgs.slice(0, 30));
                    } catch(e) { return '[]'; }
                ''') or '[]'
                try:
                    image_urls = json.loads(image_urls_raw)
                except Exception:
                    image_urls = []

            # ── 下载主图 ──
            item_dir = os.path.join(IMAGE_DIR, "taobao_" + sanitize_filename(item_id or title[:20]))
            ensure_dir(item_dir)

            local_images = []
            for idx, img_url in enumerate(image_urls[:30]):
                result = self._download_and_dedup_image(img_url, item_dir, idx)
                if result:
                    local_images.append(result["path"])

            # ── 下载 SKU 规格图(独立去重池) ──
            if sku_list:
                sku_dir = os.path.join(item_dir, "sku")
                ensure_dir(sku_dir)
                sku_img_seen = {}
                sku_md5_pool = set()
                sku_dhash_pool = []
                for s_idx, sku in enumerate(sku_list):
                    img_url = sku.get("sku_image_url") or ""
                    if not img_url:
                        continue
                    if img_url in sku_img_seen:
                        sku["sku_image"] = sku_img_seen[img_url]
                        continue
                    res = self._download_and_dedup_image(
                        img_url, sku_dir, s_idx,
                        md5_pool=sku_md5_pool, dhash_pool=sku_dhash_pool)
                    if res:
                        sku["sku_image"] = res["path"]
                        sku_img_seen[img_url] = res["path"]

            # ── 价格: SKU 最低有效价 ──
            price_float = 0.0
            sku_prices = [
                float(s.get("price")) for s in (sku_list or [])
                if isinstance(s.get("price"), (int, float)) and float(s.get("price")) > 0
            ]
            if sku_prices:
                price_float = min(sku_prices)

            # 无 SKU 时从页面价格 DOM 兜底
            if not price_float:
                price_text = tab.run_js('''
                    try {
                        var el = document.querySelector('[class*="priceText"], [class*="Price--priceText"], [class*="price--"]');
                        if(el) {
                            var m = el.textContent.match(/[\\d,.]+/);
                            if(m) return m[0];
                        }
                        return '0';
                    } catch(e) { return '0'; }
                ''') or "0"
                try:
                    price_float = float(re.sub(r'[^\d.]', '', str(price_text)) or 0)
                except Exception:
                    price_float = 0.0

            item = {
                "item_id": f"taobao_{item_id}",
                "platform": "taobao",
                "title": title,
                "original_title": title,
                "description": "",
                "original_price": str(price_float) if price_float else "0",
                "price": price_float,
                "sku_list": sku_list,
                "image_urls": image_urls,
                "local_images": local_images,
                "image_dir": item_dir,
                "attributes": attrs_dict,
                "seller": seller,
                "seller_credit": "",
                "wants": "0",
                "views": "0",
                "collects": "0",
                "link": url,
                "source_url": url,
                "source_item_id": item_id,
            }

            self._log(
                f"  ✓ 标题: {title[:40]}  价格: ¥{price_float}  "
                f"图片: {len(local_images)}张  SKU: {len(sku_list)}个"
            )
            return item

        except Exception as e:
            self._log(f"采集失败 [{url}]: {e}")
            return None

    # ═══════════════════════════════════════════════════════════
    #  公开接口
    # ═══════════════════════════════════════════════════════════

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        """关键词搜索采集"""
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()
            self.seen_img_dhash = []

            if not self._ensure_login():
                raise Exception("登录超时，请重新运行并完成登录")

            links = self._collect_search_links(keyword, count)
            if not links:
                self._log("⚠️  未找到任何商品链接（可能登录态已失效或搜索结果为空）")
                return []

            self._log(f"找到 {len(links)} 个商品，开始逐个采集详情...")

            for i, link in enumerate(links):
                item_id = self._extract_item_id(link)
                if item_id in self.seen_ids:
                    continue
                self.seen_ids.add(item_id)

                self._log(f"采集 {i+1}/{len(links)}: {item_id}")
                item = self._scrape_detail_page(link)
                if item:
                    self.items.append(item)
                else:
                    self._log("  ✗ 采集失败，跳过")
                time.sleep(1)

            self._log(f"淘宝采集完成，共 {len(self.items)} 个商品")
            return self.items
        except Exception as e:
            raise Exception(f"淘宝采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个商品链接直采"""
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()
            self.seen_img_dhash = []

            if not self._ensure_login():
                raise Exception("登录超时，请重新运行并完成登录")

            self._log(f"采集淘宝商品: {url}")
            item = self._scrape_detail_page(url)
            if item:
                self.items.append(item)
            else:
                self._log("✗ 采集失败")
            return self.items
        except Exception as e:
            raise Exception(f"淘宝采集失败: {e}")
        finally:
            self._close_browser()

    def get_items(self) -> list:
        return self.items
