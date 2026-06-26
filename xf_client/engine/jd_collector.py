"""京东(JD)商品采集器 - 搜索页+详情页双阶段采集

支持两种模式:
1. 关键词搜索 (search.jd.com)
2. 商品链接直采 (item.jd.com/xxxxx.html)
"""
import time
import re
import json
import hashlib
import os
import requests
from DrissionPage import Chromium
from config import IMAGE_DIR
from utils.helpers import ensure_dir, sanitize_filename
from utils.browser_config import get_chromium_options, check_browser_available

JD_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".xf_jd_collector_profile")


class JDCollector:
    """京东商品采集器

    采集流程:
    1. 搜索页提取商品ID列表
    2. 逐个访问 item.jd.com 详情页
    3. 提取标题/价格/描述/属性/SKU/店铺/评价数
    4. 下载商品图片并 MD5 去重
    """

    SEARCH_URL = "https://search.jd.com/Search?keyword={kw}&enc=utf-8&page={page}"
    DETAIL_URL = "https://item.jd.com/{sku_id}.html"

    def __init__(self, on_progress=None):
        self.chromium = None
        self.tab = None
        self.items = []
        self.seen_ids = set()
        self.seen_img_md5 = set()
        self.on_progress = on_progress

    # ──────────────────────── 内部工具 ────────────────────────

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)

    def _init_browser(self):
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")
        os.makedirs(JD_PROFILE_DIR, exist_ok=True)
        co, _port = get_chromium_options(user_data_dir=JD_PROFILE_DIR)
        # 反检测参数
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--no-sandbox")
        co.set_argument("--window-size=1440,900")
        self.chromium = Chromium(co)
        self.tab = self.chromium.latest_tab
        self.tab.run_js(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});"
        )

    def _is_logged_in(self) -> bool:
        """检查京东登录态"""
        try:
            url = self.tab.url or ""
            if "login" in url.lower() or "passport" in url.lower():
                return False
            result = self.tab.run_js("""
            var c = document.cookie || '';
            return c.includes('thor') || c.includes('pin') ||
                   c.includes('pinId') || c.includes('pt_key');
            """)
            return bool(result)
        except Exception:
            return False

    def _ensure_login(self, timeout: int = 300) -> bool:
        """确保已登录，未登录则等待用户扫码"""
        self._log("检查登录状态...")
        self.tab.get("https://www.jd.com/")
        time.sleep(3)
        if self._is_logged_in():
            self._log("✓ 京东已登录")
            return True

        self._log("=" * 50)
        self._log("⚠️  请在浏览器中登录京东账号")
        self._log("   登录成功后采集将自动继续")
        self._log("=" * 50)

        self.tab.get("https://passport.jd.com/new/login.aspx")
        for i in range(timeout):
            time.sleep(1)
            try:
                url = self.tab.url or ""
                if "login" not in url.lower() and "passport" not in url.lower():
                    time.sleep(2)
                    if self._is_logged_in():
                        self._log("✓ 登录成功！登录态已保存")
                        return True
            except Exception:
                pass
            if i % 30 == 0 and i > 0:
                self._log(f"等待登录... ({i}s)")
        self._log("❌ 登录超时")
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
            self.tab.get("https://www.jd.com/")
            time.sleep(3)
            return self._is_logged_in()
        except Exception:
            return False
        finally:
            self._close_browser()

    def _close_browser(self):
        if self.chromium:
            try:
                self.chromium.quit()
            except Exception:
                pass
            self.chromium = None
            self.tab = None

    def _md5(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _download_image(self, url: str, save_dir: str, index: int) -> str | None:
        """下载图片并 MD5 去重，返回本地路径"""
        try:
            if url.startswith("//"):
                url = "https:" + url
            # 去缩略图后缀，拿高清原图
            clean_url = re.sub(r"!.*$", "", url)
            clean_url = re.sub(r"_\d+x\d+.*$", "", clean_url)

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://item.jd.com/",
            }
            resp = requests.get(clean_url, timeout=15, headers=headers)
            if resp.status_code != 200 or len(resp.content) < 500:
                # 回退原始 URL
                resp = requests.get(url, timeout=15, headers=headers)
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

    def _extract_sku_id(self, url: str) -> str:
        m = re.search(r"item\.jd\.com/(\d+)\.html", url)
        if m:
            return m.group(1)
        m = re.search(r"[?&]sku=(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{8,15})\b", url)
        if m:
            return m.group(1)
        return ""

    def _safe_tab(self):
        """安全获取 tab，防止 PageDisconnected"""
        try:
            _ = self.tab.url
        except Exception:
            self.tab = self.chromium.latest_tab
        return self.tab

    # ──────────────────────── 搜索页采集 ────────────────────────

    def _collect_search_links(self, keyword: str, count: int) -> list[str]:
        """从搜索结果页收集商品链接"""
        links = []
        seen = set()
        page_num = 1

        while len(links) < count:
            url = self.SEARCH_URL.format(kw=keyword, page=(page_num * 2 - 1))
            self._log(f"搜索第 {page_num} 页: {url[:70]}...")
            self._safe_tab().get(url)
            time.sleep(3)

            # 向下滚动触发懒加载
            for _ in range(3):
                self.tab.run_js("window.scrollBy(0, 800);")
                time.sleep(1)

            raw = self.tab.run_js("""
            try {
                var links = [];
                var seen = new Set();
                var els = document.querySelectorAll(
                    'li[class*="gl-item"] a[href*="item.jd.com"],'
                    'a[href*="item.jd.com"][class*="img"],'
                    '.p-img a, .goods-list a[href*="item.jd.com"]'
                );
                els.forEach(function(a) {
                    var h = a.href || '';
                    if (!h) return;
                    h = h.split('?')[0];
                    if (/item\\.jd\\.com\\/\\d+/.test(h) && !seen.has(h)) {
                        seen.add(h);
                        links.push(h);
                    }
                });
                return JSON.stringify(links);
            } catch(e) { return '[]'; }
            """) or '[]'
            raw = json.loads(raw) if isinstance(raw, str) else (raw or [])

            new_found = 0
            for link in raw:
                if link not in seen and len(links) < count:
                    seen.add(link)
                    links.append(link)
                    new_found += 1

            self._log(f"  第 {page_num} 页找到 {new_found} 个链接，累计 {len(links)}")
            if new_found == 0:
                break
            page_num += 1
            time.sleep(1)

        return links[:count]

    # ──────────────────────── 详情页采集 ────────────────────────

    def _collect_detail(self, url: str) -> dict | None:
        """采集京东商品详情页"""
        try:
            sku_id = self._extract_sku_id(url)
            if not sku_id:
                self._log(f"  ⚠ 无法提取SKU ID: {url}")
                return None

            self._safe_tab().get(url)
            time.sleep(4)

            # 滚动触发懒加载
            for _ in range(4):
                self.tab.run_js("window.scrollBy(0, 600);")
                time.sleep(0.8)
            self.tab.run_js("window.scrollTo(0,0);")
            time.sleep(0.5)

            raw = self.tab.run_js("""
            try {
                var res = {};
                var body = document.body;

                // ── 标题 ──
                var t = document.querySelector(
                    '.sku-name, #name h1, .itemInfo-wrap h1, [class*="mainInfo"] h1, .product-intro h1'
                );
                res.title = t ? t.textContent.trim().substring(0, 200) : document.title.replace(/-?京东.*$/, '').trim();

                // ── 价格 ──
                var p = document.querySelector(
                    '[class*="J-p-"], .price.J-p, .p-price strong i, #jd-price, .priceDetail-item .item-price'
                );
                res.price = p ? p.textContent.trim() : '';
                if (!res.price) {
                    var pMatch = body.innerText.match(/￥\\s*([\\d,.]+)/);
                    res.price = pMatch ? pMatch[1] : '0';
                }

                // ── 评价数 ──
                var comm = document.querySelector('#comment-count a, .count, [id*="comment"] .comment-num');
                var commText = comm ? comm.textContent.trim() : '';
                var commMatch = (body.innerText).match(/([\\d,.万+]+)\\s*条评价/);
                res.reviews = commText || (commMatch ? commMatch[1] : '0');

                // ── 店铺 ──
                var shop = document.querySelector(
                    '#popbox .name, .J-hrefstore .name, .shopLink span, [class*="shop-name"], .seller-name'
                );
                res.shop = shop ? shop.textContent.trim() : '';

                // ── 品牌/参数 ──
                var attrs = {};
                document.querySelectorAll(
                    '.Ptable-item, .parameter2 li, [class*="param-list"] li, .product-params tr'
                ).forEach(function(el) {
                    var text = el.textContent.trim();
                    var sep = text.indexOf('：') > -1 ? '：' : ':';
                    var idx = text.indexOf(sep);
                    if (idx > 0 && idx < 30) {
                        attrs[text.slice(0, idx).trim()] = text.slice(idx + 1).trim().substring(0, 100);
                    }
                });
                res.attrs = JSON.stringify(attrs);

                // ── 描述 ──
                var descEl = document.querySelector(
                    '#product-detail-2, #detail, .product-detail, [class*="product-desc"]'
                );
                res.description = descEl ? descEl.innerText.trim().substring(0, 2000) : '';

                // ── 图片 ──
                var imgs = [];
                var seen = new Set();

                // 主图轮播
                document.querySelectorAll(
                    '#spec-list img, #preview ul img, .lh-m-wrap img, [class*="spec-n"] img'
                ).forEach(function(img) {
                    var src = img.src || img.dataset.src || img.getAttribute('data-origin') || '';
                    if (!src || src.length < 20) return;
                    if (src.startsWith('//')) src = 'https:' + src;
                    src = src.split('!')[0];
                    if (!seen.has(src)) { seen.add(src); imgs.push(src); }
                });

                // 通用备用
                if (imgs.length < 3) {
                    document.querySelectorAll('img').forEach(function(img) {
                        var src = img.src || img.dataset.src || '';
                        if (!src || seen.has(src)) return;
                        if (src.startsWith('//')) src = 'https:' + src;
                        if (src.includes('jd.com') || src.includes('jdcloud') || src.includes('360buyimg')) {
                            src = src.split('!')[0];
                            var w = img.naturalWidth || img.width || 0;
                            if (w < 50 && w > 0) return;
                            seen.add(src);
                            imgs.push(src);
                        }
                    });
                }
                res.images = imgs.slice(0, 20);

                return JSON.stringify(res);
            } catch(e) { return JSON.stringify({error: e.toString()}); }
            """)

            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}

            if "error" in data:
                self._log(f"  ⚠ JS 解析错误: {data['error'][:80]}")

            title = data.get("title", "")
            price_str = data.get("price", "0")
            try:
                price_float = float(re.sub(r"[^\d.]", "", price_str) or "0")
            except Exception:
                price_float = 0.0

            try:
                attrs_dict = json.loads(data.get("attrs", "{}"))
            except Exception:
                attrs_dict = {}

            # 下载图片
            item_dir = os.path.join(IMAGE_DIR, f"jd_{sanitize_filename(sku_id)}")
            ensure_dir(item_dir)
            local_images = []
            for idx, img_url in enumerate(data.get("images", [])[:15]):
                saved = self._download_image(img_url, item_dir, idx)
                if saved:
                    local_images.append(saved)

            item = {
                "item_id": f"jd_{sku_id}",
                "platform": "jd",
                "title": title,
                "original_title": title,
                "description": data.get("description", ""),
                "original_price": str(price_float),
                "price": price_float,
                "image_urls": data.get("images", []),
                "local_images": local_images,
                "image_dir": item_dir,
                "attributes": attrs_dict,
                "seller": data.get("shop", ""),
                "seller_credit": "",
                "wants": data.get("reviews", "0"),
                "views": "0",
                "collects": "0",
                "link": url,
                "source_url": url,
                "source_item_id": sku_id,
            }

            self._log(
                f"  ✓ {title[:40]}  ¥{price_float}"
                f"  评价:{item['wants']}  图片:{len(local_images)}张"
            )
            return item

        except Exception as e:
            self._log(f"  ✗ 详情页采集失败 [{url}]: {e}")
            return None

    # ──────────────────────── 公开接口 ────────────────────────

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        """关键词搜索采集"""
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            if not self._ensure_login():
                raise Exception("登录超时，请先点击'登录账号'按钮完成登录")

            self._log(f"正在搜索京东: {keyword}")
            links = self._collect_search_links(keyword, count)
            self._log(f"找到 {len(links)} 个商品，开始逐个采集详情...")

            for i, link in enumerate(links):
                sku_id = self._extract_sku_id(link)
                if sku_id in self.seen_ids:
                    continue
                self.seen_ids.add(sku_id)

                self._log(f"采集 {i + 1}/{len(links)}: {sku_id}")
                item = self._collect_detail(link)
                if item:
                    self.items.append(item)
                time.sleep(1.5)

            self._log(f"京东采集完成，共 {len(self.items)} 个商品")
            return self.items

        except Exception as e:
            raise Exception(f"京东采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个链接直采"""
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            if not self._ensure_login():
                raise Exception("登录超时，请先点击'登录账号'按钮完成登录")

            self._log(f"采集京东商品: {url}")
            item = self._collect_detail(url)
            if item:
                self.items.append(item)

            return self.items

        except Exception as e:
            raise Exception(f"京东采集失败: {e}")
        finally:
            self._close_browser()
