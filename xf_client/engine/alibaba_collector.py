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


class AlibabaCollector:
    """阿里巴巴(1688)商品采集器 - 采集1688详情页商品信息
    
    支持两种模式:
    1. 关键词搜索采集 (s.1688.com/selloffer/offer_search.htm)
    2. 商品链接直接采集 (detail.1688.com/offer/xxx.html)
    """

    def __init__(self, on_progress=None):
        self.page = None
        self.items = []
        self.seen_ids = set()
        self.seen_img_md5 = set()
        self.on_progress = on_progress

    def _log(self, msg):
        if self.on_progress:
            self.on_progress(msg)

    def _init_browser(self):
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")
        
        co, _port = get_chromium_options()
        chromium = Chromium(co)
        self.page = chromium.latest_tab
        self.page.run_js("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

    def _close_browser(self):
        if self.page:
            try:
                self.page.browser.quit()
            except Exception:
                pass
            self.page = None

    def _md5(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _download_and_dedup_image(self, url: str, save_dir: str, index: int) -> dict | None:
        try:
            # 1688图片URL处理
            if url.startswith('//'):
                url = 'https:' + url
            # 去掉尺寸后缀获取大图
            clean_url = re.sub(r'_\d+x\d+.*', '', url)

            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://detail.1688.com/"
            }
            resp = requests.get(clean_url, timeout=15, headers=headers)
            if resp.status_code != 200 or len(resp.content) < 100:
                resp = requests.get(url, timeout=15, headers=headers)
                if resp.status_code != 200 or len(resp.content) < 100:
                    return None

            img_data = resp.content
            md5 = self._md5(img_data)
            if md5 in self.seen_img_md5:
                return None
            self.seen_img_md5.add(md5)

            ext = ".jpg"
            if b"PNG" in img_data[:8]:
                ext = ".png"
            elif b"WEBP" in img_data[:12]:
                ext = ".webp"

            save_path = os.path.join(save_dir, f"img_{index:03d}{ext}")
            with open(save_path, "wb") as f:
                f.write(img_data)
            return {"path": save_path, "md5": md5, "url": url}
        except Exception:
            return None

    def _extract_item_id(self, url: str) -> str:
        if not url:
            return ""
        match = re.search(r'offer/(\d+)', url)
        if match:
            return match.group(1)
        match = re.search(r'(\d{10,})', url)
        if match:
            return match.group(1)
        return ""

    def _collect_search_links(self, keyword: str, count: int = 50) -> list[str]:
        """搜索关键词采集"""
        url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={keyword}"
        self.page.get(url)
        time.sleep(4)

        links = []
        seen = set()
        last_height = 0
        scroll_attempts = 0

        while len(links) < count and scroll_attempts < 30:
            item_links = self.page.run_js('''
                try {
                    var links = document.querySelectorAll('a[href*="offer"], a[href*="detail.1688"]');
                    var result = [];
                    for(var i=0; i<links.length; i++) {
                        var h = links[i].href;
                        if(h && h.includes('offer') && h.match(/\\d{10,}/) && !result.includes(h)) {
                            result.push(h);
                        }
                    }
                    return result;
                } catch(e) { return []; }
            ''')

            for link in item_links:
                if link not in seen:
                    seen.add(link)
                    links.append(link)
                    self._log(f"发现商品 {len(links)}/{count}: {link[:60]}...")

            if len(links) >= count:
                break

            self.page.scroll.to_bottom()
            time.sleep(2)

            new_height = self.page.run_js("return document.body.scrollHeight")
            if new_height == last_height:
                scroll_attempts += 1
                time.sleep(1)
            else:
                scroll_attempts = 0
            last_height = new_height

        return links[:count]

    def _scrape_detail_page(self, url: str) -> dict | None:
        """采集1688商品详情页"""
        try:
            self.page.get(url)
            time.sleep(4)

            item_id = self._extract_item_id(url)

            # 标题
            title = self.page.run_js('''
                try {
                    var el = document.querySelector('[class*="title"], .d-title, .offer-title');
                    if(el) return el.textContent.trim().substring(0, 200);
                    var el2 = document.querySelector('h1');
                    if(el2) return el2.textContent.trim().substring(0, 200);
                    return document.title.replace(/-阿里巴巴.*$/, '').replace(/-1688.*$/, '').trim();
                } catch(e) { return ''; }
            ''') or ""

            # 价格
            price_text = self.page.run_js('''
                try {
                    var el = document.querySelector('[class*="price"], .price, .d-price');
                    if(el) {
                        var text = el.textContent.trim();
                        var match = text.match(/[\\d,.]+/);
                        return match ? match[0] : text;
                    }
                    return '0';
                } catch(e) { return '0'; }
            ''') or "0"

            # 描述
            description = self.page.run_js('''
                try {
                    // 商品详情区域
                    var el = document.querySelector('[class*="desc"], [class*="detail-content"], .content-detail');
                    if(el) return el.textContent.trim().substring(0, 2000);
                    // 属性区域下方
                    var attr = document.querySelector('[class*="attributes"], .obj-attrs');
                    if(attr) return attr.textContent.trim().substring(0, 2000);
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # 属性
            attributes = self.page.run_js('''
                try {
                    var attrs = {};
                    // 1688属性通常在表格或列表中
                    var attrEls = document.querySelectorAll('[class*="attr-item"], [class*="prop-item"], .obj-attrs td, .attributes td');
                    attrEls.forEach(function(el) {
                        var text = el.textContent.trim();
                        if(text.includes('：') || text.includes(':')) {
                            var parts = text.split(/[：:]/);
                            if(parts.length >= 2) {
                                attrs[parts[0].trim()] = parts.slice(1).join(':').trim().substring(0, 100);
                            }
                        }
                    });
                    // 备用: 所有包含键值对的元素
                    if(Object.keys(attrs).length === 0) {
                        var allEls = document.querySelectorAll('div, span, li');
                        allEls.forEach(function(el) {
                            var text = el.textContent.trim();
                            if(text.length > 3 && text.length < 60 && (text.includes('：') || text.includes(':'))) {
                                var parts = text.split(/[：:]/);
                                if(parts.length === 2 && parts[0].length < 15) {
                                    attrs[parts[0].trim()] = parts[1].trim().substring(0, 100);
                                }
                            }
                        });
                    }
                    return JSON.stringify(attrs);
                } catch(e) { return '{}'; }
            ''')
            try:
                attrs_dict = json.loads(attributes)
            except Exception:
                attrs_dict = {}

            # 销量
            sales = self.page.run_js('''
                try {
                    var text = document.body.innerText;
                    var match = text.match(/成交(\\d+[万+]*)/);
                    if(match) return match[1];
                    match = text.match(/销量(\\d+)/);
                    if(match) return match[1];
                    match = text.match(/月销(\\d+)/);
                    if(match) return match[1];
                    return '0';
                } catch(e) { return '0'; }
            ''') or "0"

            # 卖家
            seller = self.page.run_js('''
                try {
                    var el = document.querySelector('[class*="company"], [class*="seller"], [class*="shop-name"]');
                    if(el) return el.textContent.trim().substring(0, 50);
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # 图片
            image_urls = self.page.run_js('''
                try {
                    var imgs = [];
                    var seen = new Set();
                    var selectors = [
                        '.tab-attr img', '[class*="carousel"] img',
                        '[class*="gallery"] img', '[class*="swiper"] img',
                        '.detail-gallery img', '#dt-tab img'
                    ];
                    for (var s = 0; s < selectors.length; s++) {
                        var els = document.querySelectorAll(selectors[s]);
                        els.forEach(function(img) {
                            var src = img.src || img.dataset.src || img.getAttribute('data-lazy-src') || '';
                            if(src && src.length > 30 && !seen.has(src)) {
                                seen.add(src);
                                imgs.push(src);
                            }
                        });
                    }
                    return imgs.slice(0, 20);
                } catch(e) { return []; }
            ''') or []

            # 下载图片
            item_dir = os.path.join(IMAGE_DIR, "1688_" + sanitize_filename(item_id or title[:20]))
            ensure_dir(item_dir)

            local_images = []
            for idx, img_url in enumerate(image_urls[:20]):
                result = self._download_and_dedup_image(img_url, item_dir, idx)
                if result:
                    local_images.append(result["path"])

            # 清洗价格
            price_float = 0.0
            try:
                price_clean = re.sub(r'[¥￥,，]', '', str(price_text)).strip()
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

            self._log(f"  ✓ 标题: {title[:40]}  价格: ¥{price_float}  销量: {sales}  图片: {len(local_images)}张")
            return item

        except Exception as e:
            self._log(f"采集失败 [{url}]: {e}")
            return None

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            self._log(f"正在搜索1688: {keyword}")
            links = self._collect_search_links(keyword, count)
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
                    self._log(f"  ✗ 采集失败")

                time.sleep(1)

            self._log(f"1688采集完成，共 {len(self.items)} 个商品")
            return self.items
        except Exception as e:
            raise Exception(f"1688采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个链接采集"""
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            self._log(f"采集1688商品: {url}")
            item = self._scrape_detail_page(url)
            if item:
                self.items.append(item)

            return self.items
        except Exception as e:
            raise Exception(f"1688采集失败: {e}")
        finally:
            self._close_browser()
