import time
import re
import json
import hashlib
import os
import requests
from urllib.parse import urlparse, parse_qs
from DrissionPage import Chromium
from config import PLATFORM_URLS, IMAGE_DIR

XIANYU_BASE_URL = PLATFORM_URLS['xianyu']['home'].rstrip('/')
from utils.helpers import ensure_dir, sanitize_filename
from utils.browser_config import get_chromium_options, check_browser_available


class XianyuCollector:
    """闲鱼商品采集器 - 支持关键词搜索、主页采集、商品链接采集
    采集完整信息：标题、价格、描述、所有图片（MD5去重）、想要数、浏览数、卖家信息等

    闲鱼详情页DOM结构（2026-06实测）:
    - item-main-info--xxx (主信息容器)
      ├─ tips--xxx (价格+想要+浏览)
      │   ├─ price--xxx → "1628"
      │   └─ want--xxx → "39人想要 402浏览" 或 "11浏览"
      ├─ card--xxx (服务保障卡片，如"描述不符包邮退")
      ├─ main--xxx → desc--xxx span (商品描述)
      └─ labels--xxx (结构化属性: 品牌/型号/容量)
    - main-title--xxx 是推荐区标题，不是当前商品标题！
    - 商品标题从 document.title 提取（格式: "标题_闲鱼"）
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
        # 检查浏览器
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

    def _clean_image_url(self, url: str) -> str:
        """将缩略图URL转换为高清原图URL"""
        clean = re.sub(r'_\d+x\d+.*$', '', url)
        if clean.endswith('.heic'):
            clean = clean + '_960x960.jpg'
        return clean

    def _download_and_dedup_image(self, url: str, save_dir: str, index: int) -> dict | None:
        """下载图片并MD5去重"""
        try:
            clean_url = self._clean_image_url(url)
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.goofish.com/"
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
            return {"path": save_path, "md5": md5, "url": clean_url}
        except Exception:
            return None

    def _extract_item_id(self, url: str) -> str:
        if not url:
            return ""
        if 'id=' in url:
            return parse_qs(urlparse(url).query).get('id', [''])[0]
        match = re.search(r'/item/(\d+)', url)
        if match:
            return match.group(1)
        return ""

    # ─── 搜索列表页 ───
    def _collect_search_links(self, keyword: str, count: int = 50) -> list[str]:
        url = PLATFORM_URLS['xianyu']['search'].format(kw=keyword)
        self.page.get(url)
        time.sleep(3)

        links = []
        seen = set()
        last_height = 0
        scroll_attempts = 0
        max_scroll = 30

        while len(links) < count and scroll_attempts < max_scroll:
            item_links = self.page.run_js('''
                try {
                    var links = document.querySelectorAll('a[href*="/item?id="]');
                    var result = [];
                    for(var i=0; i<links.length; i++) {
                        var h = links[i].href;
                        if(h && h.includes('/item?id=') && !result.includes(h)) {
                            result.push(h);
                        }
                    }
                    return JSON.stringify(result);
                } catch(e) { return '[]'; }
            ''') or '[]'
            item_links = json.loads(item_links) if isinstance(item_links, str) else (item_links or [])

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

    # ─── 主页采集 ───
    def _collect_homepage_links(self, homepage_url: str, count: int = 50) -> list[str]:
        self.page.get(homepage_url)
        time.sleep(3)

        links = []
        seen = set()
        last_height = 0
        scroll_attempts = 0

        while len(links) < count and scroll_attempts < 20:
            item_links = self.page.run_js('''
                try {
                    var links = document.querySelectorAll('a[href*="/item?id="]');
                    var result = [];
                    for(var i=0; i<links.length; i++) {
                        result.push(links[i].href);
                    }
                    return JSON.stringify(result);
                } catch(e) { return '[]'; }
            ''') or '[]'
            item_links = json.loads(item_links) if isinstance(item_links, str) else (item_links or [])

            for link in item_links:
                if link not in seen:
                    seen.add(link)
                    links.append(link)

            if len(links) >= count:
                break

            self.page.scroll.to_bottom()
            time.sleep(2)
            new_height = self.page.run_js("return document.body.scrollHeight")
            if new_height == last_height:
                scroll_attempts += 1
            else:
                scroll_attempts = 0
            last_height = new_height

        return links[:count]

    # ─── 详情页采集（核心） ───
    def _scrape_detail_page(self, url: str) -> dict | None:
        """采集单个商品详情页的完整信息"""
        try:
            self.page.get(url)
            time.sleep(3)

            item_id = self._extract_item_id(url)

            # 1. 标题 - 从 document.title 提取（格式: "标题_闲鱼"）
            #    闲鱼详情页没有独立标题元素，main-title-- 是推荐区的
            title = self.page.run_js('''
                try {
                    var t = document.title.replace(/_闲鱼.*$/, '').trim();
                    if(t && t.length > 2) return t.substring(0, 200);
                    // fallback: 描述第一行
                    var mainEl = document.querySelector('[class*="main--"]');
                    if(mainEl) {
                        var descSpan = mainEl.querySelector('[class*="desc--"]');
                        if(descSpan) {
                            var text = descSpan.textContent.trim();
                            return text.split('\\n')[0].substring(0, 200);
                        }
                    }
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # 2. 价格 - item-main-info 内的 price--xxx
            price_text = self.page.run_js('''
                try {
                    var mainInfo = document.querySelector('[class*="item-main-info--"]');
                    var scope = mainInfo || document;
                    var el = scope.querySelector('[class*="price--"]');
                    if(el) {
                        var text = el.textContent.trim();
                        var match = text.match(/[\\d,.]+/);
                        return match ? match[0] : text;
                    }
                    return '0';
                } catch(e) { return '0'; }
            ''') or "0"

            # 3. 想要数 + 浏览数 - item-main-info 内的 want--xxx
            stats = self.page.run_js('''
                try {
                    var result = {wants: '0', views: '0', collect: '0'};
                    var mainInfo = document.querySelector('[class*="item-main-info--"]');
                    var scope = mainInfo || document;
                    
                    // want--xxx 容器: "39人想要 402浏览" 或仅 "11浏览"
                    var wantEl = scope.querySelector('[class*="want--"]');
                    if(wantEl) {
                        var text = wantEl.textContent.trim();
                        var wantMatch = text.match(/(\\d+)人想要/);
                        var viewMatch = text.match(/(\\d+)浏览/);
                        if(wantMatch) result.wants = wantMatch[1];
                        if(viewMatch) result.views = viewMatch[1];
                    }
                    
                    // 备用: tips--xxx 整体文本
                    if(result.wants === '0' && result.views === '0') {
                        var tipsEls = scope.querySelectorAll('[class*="tips--"]');
                        for(var i=0; i<tipsEls.length; i++) {
                            var t = tipsEls[i].textContent.trim();
                            if(t.includes('想要') || t.includes('浏览')) {
                                var wMatch = t.match(/(\\d+)人想要/);
                                var vMatch = t.match(/(\\d+)浏览/);
                                if(wMatch) result.wants = wMatch[1];
                                if(vMatch) result.views = vMatch[1];
                                break;
                            }
                        }
                    }
                    
                    // 收藏数
                    var collectMatch = document.body.innerText.match(/(\\d+)收藏/);
                    if(collectMatch) result.collect = collectMatch[1];
                    
                    return JSON.stringify(result);
                } catch(e) { return '{}'; }
            ''')
            try:
                stats_dict = json.loads(stats)
            except Exception:
                stats_dict = {}

            # 4. 描述 - main--xxx 容器内的 desc--xxx span
            #    第一个 desc--xxx 是服务保障文案（"满足条件时..."），不是商品描述
            description = self.page.run_js('''
                try {
                    // 优先: main--xxx 容器内的 desc--xxx
                    var mainEl = document.querySelector('[class*="main--"]');
                    if(mainEl) {
                        var descSpan = mainEl.querySelector('[class*="desc--"]');
                        if(descSpan) return descSpan.textContent.trim().substring(0, 2000);
                    }
                    // fallback: 所有 desc--xxx 中最长的
                    var descs = document.querySelectorAll('[class*="desc--"]');
                    var longest = '';
                    descs.forEach(function(el) {
                        var t = el.textContent.trim();
                        if(t.length > longest.length && t.length < 2000) longest = t;
                    });
                    return longest;
                } catch(e) { return ''; }
            ''') or ""

            # 5. 商品属性 - labels--xxx 容器内结构化属性
            attributes = self.page.run_js('''
                try {
                    var attrs = {};
                    var labelsEl = document.querySelector('[class*="labels--"]');
                    if(labelsEl) {
                        var items = labelsEl.querySelectorAll('[class*="item--"]');
                        items.forEach(function(item) {
                            var label = item.querySelector('[class*="label--"]');
                            var value = item.querySelector('[class*="value--"]');
                            if(label && value) {
                                attrs[label.textContent.trim()] = value.textContent.trim().substring(0, 100);
                            }
                        });
                    }
                    // fallback: 通用属性选择器
                    if(Object.keys(attrs).length === 0) {
                        var attrEls = document.querySelectorAll('[class*="attr"], [class*="property"], [class*="spec"]');
                        attrEls.forEach(function(el) {
                            var text = el.textContent.trim();
                            if(text.includes('：') || text.includes(':')) {
                                var parts = text.split(/[：:]/);
                                if(parts.length >= 2) {
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

            # 6. 卖家信息
            seller = self.page.run_js('''
                try {
                    var el = document.querySelector('[class*="seller-text--"]');
                    if(el) return el.textContent.trim().substring(0, 50);
                    var el2 = document.querySelector('[class*="seller-left--"]');
                    if(el2) return el2.textContent.trim().substring(0, 50);
                    return '';
                } catch(e) { return ''; }
            ''') or ""

            # 7. 卖家信用等级
            seller_credit = self.page.run_js('''
                try {
                    var text = document.body.innerText;
                    var match = text.match(/卖家信用(优秀|良好|一般)/);
                    return match ? match[1] : '';
                } catch(e) { return ''; }
            ''') or ""

            # 8. 图片列表
            image_urls = self.page.run_js('''
                try {
                    var imgs = [];
                    var seen = new Set();
                    
                    // 商品轮播图: ant-image-img
                    var antImgs = document.querySelectorAll('.ant-image-img');
                    antImgs.forEach(function(img) {
                        var src = img.src || img.dataset.src || '';
                        if(src && src.includes('bao/uploaded') && !src.includes('tps-') && src.length > 50) {
                            if(!seen.has(src)) { seen.add(src); imgs.push(src); }
                        }
                    });
                    
                    // 轮播列表项
                    var carouselImgs = document.querySelectorAll('[class*="carouselItem"] img, [class*="fadeInImg"] img');
                    carouselImgs.forEach(function(img) {
                        var src = img.src || img.dataset.src || '';
                        if(src && src.includes('bao/uploaded') && !src.includes('tps-') && src.length > 50) {
                            if(!seen.has(src)) { seen.add(src); imgs.push(src); }
                        }
                    });
                    
                    // 详情描述区域图片
                    var descEl = document.querySelector('[class*="desc--"]');
                    if(descEl) {
                        var descImgs = descEl.querySelectorAll('img');
                        descImgs.forEach(function(img) {
                            var src = img.src || img.dataset.src || '';
                            if(src && src.includes('alicdn') && !src.includes('tps-') && src.length > 50) {
                                if(!seen.has(src)) { seen.add(src); imgs.push(src); }
                            }
                        });
                    }
                    return JSON.stringify(imgs);
                } catch(e) { return '[]'; }
            ''') or '[]'
            image_urls = json.loads(image_urls) if isinstance(image_urls, str) else []

            # URL去重
            seen_urls = set()
            unique_urls = []
            for u in image_urls:
                normalized = re.sub(r'_\d+x\d+.*', '', u)
                if normalized not in seen_urls:
                    seen_urls.add(normalized)
                    unique_urls.append(u)

            # 9. 下载图片
            item_dir = os.path.join(IMAGE_DIR, sanitize_filename(item_id or title[:20]))
            ensure_dir(item_dir)

            local_images = []
            img_records = []
            for idx, img_url in enumerate(unique_urls):
                result = self._download_and_dedup_image(img_url, item_dir, idx)
                if result:
                    local_images.append(result["path"])
                    img_records.append(result)

            # 清洗价格
            price_float = 0.0
            try:
                price_clean = re.sub(r'[¥￥,，]', '', str(price_text)).strip()
                price_float = float(re.search(r'[\d.]+', price_clean).group())
            except Exception:
                pass

            item = {
                "item_id": item_id,
                "platform": "xianyu",
                "title": title,
                "original_title": title,
                "description": description,
                "original_price": str(price_float) if price_float else price_text,
                "price": price_float,
                "image_urls": unique_urls,
                "local_images": local_images,
                "image_records": img_records,
                "image_dir": item_dir,
                "attributes": attrs_dict,
                "seller": seller,
                "seller_credit": seller_credit,
                "wants": stats_dict.get("wants", "0"),
                "views": stats_dict.get("views", "0"),
                "collects": stats_dict.get("collect", "0"),
                "link": url,
                "source_url": url,
                "source_item_id": item_id,
            }

            self._log(f"  ✓ 标题: {title[:40]}  价格: ¥{price_float}  想要: {item['wants']}  浏览: {item['views']}  图片: {len(local_images)}张")
            return item

        except Exception as e:
            self._log(f"采集失败 [{url}]: {e}")
            return None

    # ─── 公开接口 ───
    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            self._log(f"正在搜索: {keyword}")
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

            self._log(f"采集完成，共 {len(self.items)} 个商品")
            return self.items
        except Exception as e:
            raise Exception(f"采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_homepage(self, homepage_url: str, count: int = 50) -> list:
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            self._log(f"正在采集主页: {homepage_url}")
            links = self._collect_homepage_links(homepage_url, count)
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

                time.sleep(1)

            self._log(f"采集完成，共 {len(self.items)} 个商品")
            return self.items
        except Exception as e:
            raise Exception(f"主页采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_links(self, links: list[str]) -> list:
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            self._log(f"批量采集 {len(links)} 个链接...")
            for i, link in enumerate(links):
                item_id = self._extract_item_id(link)
                if item_id in self.seen_ids:
                    continue
                self.seen_ids.add(item_id)

                self._log(f"采集 {i+1}/{len(links)}: {item_id}")
                item = self._scrape_detail_page(link)
                if item:
                    self.items.append(item)

                time.sleep(1)

            return self.items
        except Exception as e:
            raise Exception(f"批量采集失败: {e}")
        finally:
            self._close_browser()

    def get_items(self) -> list:
        return self.items
