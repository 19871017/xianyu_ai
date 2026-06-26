"""阿里巴巴(1688)商品采集器 - 登录态持久化 + DrissionPage浏览器采集

采集流程:
1. 首次使用: 打开浏览器 → 导航到1688登录页 → 用户扫码登录 → Cookie持久化保存
2. 后续使用: 自动加载Cookie，检查登录状态，未登录则提示重新扫码
3. 搜索采集: s.1688.com 搜索关键词 → 提取商品链接 → 逐个采集详情页
4. 链接采集: 直接传入 detail.1688.com 商品URL采集

技术要点:
- 1688搜索页和详情页都需要登录才能访问
- 未登录时搜索页会跳转到 login.taobao.com
- 详情页未登录会显示404
- 使用持久化用户数据目录 ~/.xf_1688_profile 保存登录态
- 使用 DrissionPage Chromium(co).get_tab() 模式
- 图片用 requests 下载，带 Referer 头，MD5 去重
"""
import time
import re
import json
import hashlib
import os
import requests
from urllib.parse import quote
from DrissionPage import Chromium
from config import IMAGE_DIR
from utils.helpers import ensure_dir, sanitize_filename
from utils.browser_config import get_chromium_options, check_browser_available


# ─── 持久化Profile目录 ───────────────────────────────────────
ALIBABA_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".xf_1688_profile")

# ─── URL 常量 ─────────────────────────────────────────────────
LOGIN_URL = "https://login.1688.com/member/marketSigninJump.htm"
SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords={kw}"
HOME_URL = "https://www.1688.com/"

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


class AlibabaCollector:
    """阿里巴巴(1688)商品采集器

    支持两种模式:
    1. 关键词搜索采集 (s.1688.com/selloffer/offer_search.htm)
    2. 商品链接直接采集 (detail.1688.com/offer/xxx.html)

    首次使用需扫码登录，登录态持久化保存到 ~/.xf_1688_profile
    """

    def __init__(self, on_progress=None):
        self.chromium = None
        self.tab = None
        self.items = []
        self.seen_ids = set()
        self.seen_img_md5 = set()
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

        os.makedirs(ALIBABA_PROFILE_DIR, exist_ok=True)
        co, _port = get_chromium_options(user_data_dir=ALIBABA_PROFILE_DIR)

        # 反检测参数
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-sandbox")
        co.set_argument("--window-size=1440,900")
        co.set_argument("--disable-infobars")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument(f"--user-agent={PC_UA}")

        self.chromium = Chromium(co)
        self.tab = self.chromium.latest_tab

        # 注入 stealth JS
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

    def _is_logged_in(self) -> bool:
        """检查是否已登录1688

        判断逻辑:
        1. 当前URL是否被重定向到登录页
        2. Cookie中是否包含1688登录标记
        """
        try:
            tab = self._safe_tab()
            current_url = tab.url or ""

            # 被重定向到登录页 → 未登录
            if "login.taobao.com" in current_url or "login.1688.com" in current_url:
                return False

            # 检查Cookie中的登录标记
            result = tab.run_js("""
            var c = document.cookie || '';
            return c.includes('cna') && (
                c.includes('unb') ||
                c.includes('lid') ||
                c.includes('cookie17') ||
                c.includes('login_') ||
                c.includes('_m_h5_tk') ||
                c.includes('xlly_s')
            );
            """)
            return bool(result)
        except Exception:
            return False

    def _ensure_login(self, timeout: int = 300) -> bool:
        """确保已登录，未登录则等待用户扫码

        Args:
            timeout: 等待登录超时时间（秒）

        Returns:
            True 已登录，False 超时
        """
        tab = self._safe_tab()

        # 先访问1688首页，检查登录状态
        self._log("正在检查1688登录状态...")
        tab.get(HOME_URL)
        time.sleep(3)

        if self._is_logged_in():
            self._log("✅ 已登录（使用保存的Cookie）")
            return True

        # 未登录，导航到登录页
        self._log("⚠️  未登录，正在打开1688登录页面...")
        self._log("=" * 50)
        self._log("请在弹出的浏览器中扫码登录1688")
        self._log("登录成功后采集将自动继续")
        self._log(f"登录状态会保存到 {ALIBABA_PROFILE_DIR}")
        self._log("下次无需重复登录")
        self._log("=" * 50)

        tab.get(LOGIN_URL)
        time.sleep(2)

        # 等待用户完成登录
        for i in range(timeout):
            time.sleep(1)
            try:
                current_url = self._safe_tab().url or ""

                # 登录成功后会跳转离开登录页
                if ("login.taobao.com" not in current_url and
                    "login.1688.com" not in current_url and
                    "marketSigninJump" not in current_url):

                    # 二次确认Cookie
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

    # ═══════════════════════════════════════════════════════════
    #  图片下载
    # ═══════════════════════════════════════════════════════════

    def _md5(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _clean_image_url(self, url: str) -> str:
        """清理图片URL，去掉尺寸后缀获取大图

        1688图片URL常见格式:
        - https://cbu01.alicdn.com/img/ibank/xxx/xxx_800x800.jpg
        - https://cbu01.alicdn.com/img/ibank/xxx/xxx_400x400.jpg
        - //cbu01.alicdn.com/...
        """
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        # 去掉 _数字x数字 后缀（如 _800x800.jpg → .jpg）
        clean = re.sub(r'_\d+x\d+', '', url)
        return clean

    def _download_and_dedup_image(self, url: str, save_dir: str, index: int) -> dict | None:
        """下载图片并MD5去重

        Returns:
            {"path": local_path, "md5": md5, "url": original_url} 或 None
        """
        try:
            if not url or len(url) < 10:
                return None

            clean_url = self._clean_image_url(url)

            headers = {
                "User-Agent": PC_UA,
                "Referer": "https://detail.1688.com/"
            }

            # 优先用清理后的URL获取大图
            resp = requests.get(clean_url, timeout=15, headers=headers)
            if resp.status_code != 200 or len(resp.content) < 500:
                # 回退到原始URL
                resp = requests.get(url, timeout=15, headers=headers)
                if resp.status_code != 200 or len(resp.content) < 500:
                    return None

            img_data = resp.content
            md5 = self._md5(img_data)
            if md5 in self.seen_img_md5:
                return None
            self.seen_img_md5.add(md5)

            # 判断图片格式
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
        """从URL中提取1688商品ID"""
        if not url:
            return ""
        # detail.1688.com/offer/123456789.html
        match = re.search(r'offer/(\d+)', url)
        if match:
            return match.group(1)
        # 备用: 提取10位以上数字
        match = re.search(r'(\d{10,})', url)
        if match:
            return match.group(1)
        return ""

    # ═══════════════════════════════════════════════════════════
    #  搜索页链接采集
    # ═══════════════════════════════════════════════════════════

    def _collect_search_links(self, keyword: str, count: int = 50) -> list[str]:
        """搜索关键词，提取商品详情页链接

        Args:
            keyword: 搜索关键词
            count: 目标链接数量

        Returns:
            商品详情页URL列表
        """
        tab = self._safe_tab()
        encoded_kw = quote(keyword)
        url = SEARCH_URL.format(kw=encoded_kw)

        self._log(f"正在搜索: {keyword}")
        tab.get(url)
        time.sleep(4)

        # 检查是否被重定向到登录页
        current_url = tab.url or ""
        if "login.taobao.com" in current_url or "login.1688.com" in current_url:
            self._log("⚠️  搜索页跳转到登录页，登录态已失效")
            return []

        links = []
        seen = set()
        last_height = 0
        scroll_attempts = 0
        max_scroll = 30

        while len(links) < count and scroll_attempts < max_scroll:
            # 提取页面中的商品链接
            item_links = tab.run_js('''
                try {
                    var links = document.querySelectorAll('a[href*="offer"], a[href*="detail.1688"]');
                    var result = [];
                    var seen = new Set();
                    for(var i=0; i<links.length; i++) {
                        var h = links[i].href;
                        if(h && h.match(/\\d{10,}/) && !seen.has(h)) {
                            seen.add(h);
                            result.push(h);
                        }
                    }
                    return JSON.stringify(result);
                } catch(e) { return '[]'; }
            ''') or '[]'

            item_links = json.loads(item_links) if isinstance(item_links, str) else (item_links or [])

            for link in item_links:
                # 规范化链接：去掉查询参数
                clean_link = re.sub(r'\?.*$', '', link)
                if clean_link not in seen:
                    seen.add(clean_link)
                    links.append(clean_link)
                    self._log(f"发现商品 {len(links)}/{count}: {clean_link[:70]}...")

            if len(links) >= count:
                break

            # 滚动加载更多
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
        """采集1688商品详情页

        采集内容: 标题、价格(区间价格取最低)、描述、属性、图片(主图+详情图)、卖家、销量
        图片下载到本地，MD5去重

        Args:
            url: 商品详情页URL

        Returns:
            标准化商品dict 或 None
        """
        tab = self._safe_tab()
        try:
            tab.get(url)
            time.sleep(4)

            # 检查是否未登录被拦截
            current_url = tab.url or ""
            page_text = tab.html or ""
            if ("login.taobao.com" in current_url or
                "login.1688.com" in current_url or
                "404" in page_text[:500] and len(page_text) < 2000):
                self._log(f"  ⚠ 详情页无法访问（可能未登录）: {url[:60]}...")
                return None

            item_id = self._extract_item_id(url)

            # ── 标题 ──
            title = tab.run_js('''
                try {
                    var el = document.querySelector('[class*="title"], .d-title, .offer-title, .obj-title');
                    if(el) return el.textContent.trim().substring(0, 200);
                    var el2 = document.querySelector('h1');
                    if(el2) return el2.textContent.trim().substring(0, 200);
                    return document.title.replace(/-阿里巴巴.*$/, '').replace(/-1688.*$/, '').trim();
                } catch(e) { return ''; }
            ''') or ""

            if not title:
                title = tab.run_js("return document.title || '';") or ""

            # ── 价格 ──
            price_text = tab.run_js('''
                try {
                    var els = document.querySelectorAll('[class*="price"], .price, .d-price, .obj-price');
                    for(var i=0; i<els.length; i++) {
                        var text = els[i].textContent.trim();
                        if(text.match(/\\d/)) {
                            var match = text.match(/[\\d,.]+/);
                            if(match) return match[0];
                        }
                    }
                    return '0';
                } catch(e) { return '0'; }
            ''') or "0"

            # ── 描述 ──
            description = tab.run_js('''
                try {
                    var el = document.querySelector('[class*="desc"], [class*="detail-content"], .content-detail, .obj-description');
                    if(el) return el.textContent.trim().substring(0, 2000);
                    var attr = document.querySelector('[class*="attributes"], .obj-attrs');
                    if(attr) return attr.textContent.trim().substring(0, 2000);
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # ── 属性 ──
            attributes_raw = tab.run_js('''
                try {
                    var attrs = {};
                    var attrEls = document.querySelectorAll('[class*="attr-item"], [class*="prop-item"], .obj-attrs td, .attributes td, [class*="attrs"] .attr');
                    attrEls.forEach(function(el) {
                        var text = el.textContent.trim();
                        if(text.includes('：') || text.includes(':')) {
                            var parts = text.split(/[：:]/);
                            if(parts.length >= 2) {
                                var key = parts[0].trim();
                                var val = parts.slice(1).join(':').trim().substring(0, 100);
                                if(key && val && key.length < 15) {
                                    attrs[key] = val;
                                }
                            }
                        }
                    });
                    if(Object.keys(attrs).length === 0) {
                        var allEls = document.querySelectorAll('div, span, li');
                        allEls.forEach(function(el) {
                            var text = el.textContent.trim();
                            if(text.length > 3 && text.length < 60 && (text.includes('：') || text.includes(':'))) {
                                var parts = text.split(/[：:]/);
                                if(parts.length === 2 && parts[0].length < 15) {
                                    var key = parts[0].trim();
                                    var val = parts[1].trim().substring(0, 100);
                                    if(key && val && !attrs[key]) {
                                        attrs[key] = val;
                                    }
                                }
                            }
                        });
                    }
                    return JSON.stringify(attrs);
                } catch(e) { return '{}'; }
            ''')
            try:
                attrs_dict = json.loads(attributes_raw) if isinstance(attributes_raw, str) else {}
            except Exception:
                attrs_dict = {}

            # ── 销量 ──
            sales = tab.run_js('''
                try {
                    var text = document.body.innerText;
                    var match = text.match(/成交(\\d+[万+]*)/);
                    if(match) return match[1];
                    match = text.match(/销量(\\d+)/);
                    if(match) return match[1];
                    match = text.match(/月销(\\d+)/);
                    if(match) return match[1];
                    match = text.match(/(\\d+)人付款/);
                    if(match) return match[1];
                    return '0';
                } catch(e) { return '0'; }
            ''') or "0"

            # ── 卖家 ──
            seller = tab.run_js('''
                try {
                    var el = document.querySelector('[class*="company"], [class*="seller"], [class*="shop-name"], [class*="supplier"]');
                    if(el) return el.textContent.trim().substring(0, 50);
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # ── 图片 (主图 + 详情图) ──
            image_urls_raw = tab.run_js('''
                try {
                    var imgs = [];
                    var seen = new Set();
                    // 主图: 轮播/画廊区域
                    var mainSelectors = [
                        '.tab-attr img', '[class*="carousel"] img',
                        '[class*="gallery"] img', '[class*="swiper"] img',
                        '.detail-gallery img', '#dt-tab img',
                        '[class*="main-pic"] img', '[class*="offer-img"] img'
                    ];
                    for (var s = 0; s < mainSelectors.length; s++) {
                        var els = document.querySelectorAll(mainSelectors[s]);
                        els.forEach(function(img) {
                            var src = img.src || img.dataset.src || img.getAttribute('data-lazy-src') || '';
                            if(src && src.length > 30 && !seen.has(src)) {
                                seen.add(src);
                                imgs.push(src);
                            }
                        });
                    }
                    // 详情图: 详情描述区域
                    var detailEls = document.querySelectorAll('[class*="detail-content"] img, [class*="desc"] img, .content-detail img');
                    detailEls.forEach(function(img) {
                        var src = img.src || img.dataset.src || img.getAttribute('data-lazy-src') || '';
                        if(src && src.length > 30 && !seen.has(src)) {
                            seen.add(src);
                            imgs.push(src);
                        }
                    });
                    return JSON.stringify(imgs.slice(0, 30));
                } catch(e) { return '[]'; }
            ''') or '[]'
            image_urls = json.loads(image_urls_raw) if isinstance(image_urls_raw, str) else []

            # ── 下载图片 ──
            item_dir = os.path.join(IMAGE_DIR, "1688_" + sanitize_filename(item_id or title[:20]))
            ensure_dir(item_dir)

            local_images = []
            for idx, img_url in enumerate(image_urls[:30]):
                result = self._download_and_dedup_image(img_url, item_dir, idx)
                if result:
                    local_images.append(result["path"])

            # ── 价格处理: 区间价格取最低 ──
            price_float = 0.0
            try:
                price_clean = re.sub(r'[¥￥,，\s]', '', str(price_text)).strip()
                # 区间价格 如 "5.00-8.00" 取最低
                if '-' in price_clean:
                    price_parts = price_clean.split('-')
                    price_float = float(re.search(r'[\d.]+', price_parts[0]).group())
                else:
                    price_float = float(re.search(r'[\d.]+', price_clean).group())
            except Exception:
                pass

            item = {
                "item_id": f"1688_{item_id}",
                "platform": "1688",
                "title": title,
                "original_title": title,
                "description": description,
                "original_price": str(price_float) if price_float else price_text,
                "price": price_float,
                "image_urls": image_urls,
                "local_images": local_images,
                "image_dir": item_dir,
                "attributes": attrs_dict,
                "seller": seller,
                "seller_credit": "",
                "wants": sales,
                "views": "0",
                "collects": "0",
                "link": url,
                "source_url": url,
                "source_item_id": item_id,
            }

            self._log(
                f"  ✓ 标题: {title[:40]}  价格: ¥{price_float}  "
                f"销量: {sales}  图片: {len(local_images)}张"
            )
            return item

        except Exception as e:
            self._log(f"采集失败 [{url}]: {e}")
            return None

    # ═══════════════════════════════════════════════════════════
    #  公开接口
    # ═══════════════════════════════════════════════════════════

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        """关键词搜索采集

        流程: 登录 → 搜索关键词 → 提取商品链接 → 逐个采集详情页

        Args:
            keyword: 搜索关键词
            count: 目标采集数量（默认50）

        Returns:
            标准化商品列表
        """
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            # 确保登录
            if not self._ensure_login():
                raise Exception("登录超时，请重新运行并完成登录")

            # 搜索采集
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
                    self._log(f"  ✗ 采集失败，跳过")

                # 请求间隔，避免触发反爬
                time.sleep(1)

            self._log(f"1688采集完成，共 {len(self.items)} 个商品")
            return self.items

        except Exception as e:
            raise Exception(f"1688采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个商品链接直采

        支持: https://detail.1688.com/offer/123456789.html

        Args:
            url: 1688商品详情页URL

        Returns:
            包含单个商品的列表（或空列表）
        """
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            # 确保登录
            if not self._ensure_login():
                raise Exception("登录超时，请重新运行并完成登录")

            self._log(f"采集1688商品: {url}")
            item = self._scrape_detail_page(url)
            if item:
                self.items.append(item)
            else:
                self._log("✗ 采集失败")

            return self.items

        except Exception as e:
            raise Exception(f"1688采集失败: {e}")
        finally:
            self._close_browser()
