"""拼多多商品采集器 - 搜索页+详情页双阶段采集"""
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


class PddCollector:
    """拼多多商品采集器
    
    采集流程:
    1. 搜索页提取商品列表（标题/价格/销量/缩略图）
    2. 点击进入详情页采集完整信息（描述/大图/属性/评价）
    """

    def __init__(self, on_progress=None):
        self.chromium = None
        self.tab = None
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
        self.chromium = Chromium(co)
        self.tab = self.chromium.latest_tab
        self.tab.run_js("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

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
        """下载图片并MD5去重"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://mobile.yangkeduo.com/"
            }
            resp = requests.get(url, timeout=15, headers=headers)
            if resp.status_code != 200 or len(resp.content) < 500:
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
            return save_path
        except Exception:
            return None

    def _extract_goods_id(self, url: str) -> str:
        """从URL中提取goods_id"""
        match = re.search(r'goods_id=(\d+)', url)
        return match.group(1) if match else ""

    def _scroll_page(self, times=3, wait=2):
        """滚动页面加载更多内容（用JS避免PageDisconnected）"""
        for _ in range(times):
            try:
                self.tab.run_js("window.scrollBy(0, 800);")
            except Exception:
                try:
                    self.tab = self.chromium.latest_tab
                    self.tab.run_js("window.scrollBy(0, 800);")
                except Exception:
                    pass
            time.sleep(wait)

    def _collect_from_search_page(self, keyword: str, count: int = 50) -> list[dict]:
        """从搜索页提取商品列表"""
        url = f"https://mobile.yangkeduo.com/search_result.html?search_key={keyword}"
        self.tab.get(url)
        time.sleep(5)

        # 滚动加载
        self._scroll_page(times=5, wait=2)

        items = self.tab.run_js("""
        try {
            var cards = document.querySelectorAll('._3glhOBhU');
            var result = [];
            var seen = new Set();
            
            for(var i=0; i<cards.length; i++) {
                var card = cards[i];
                var text = card.textContent.trim();
                if(text.length < 20) continue;
                
                var key = text.substring(0, 30);
                if(seen.has(key)) continue;
                seen.add(key);
                
                // 标题：最长的纯文本叶子节点
                var title = '';
                var maxLen = 0;
                card.querySelectorAll('*').forEach(function(el) {
                    if(el.children.length === 0) {
                        var t = el.textContent.trim();
                        if(t.length > maxLen && t.length > 8 
                           && !t.includes('¥') && !t.includes('已拼') 
                           && !t.includes('预计') && !t.includes('好评')
                           && !t.includes('立减') && !t.includes('券后')
                           && !t.includes('即将') && !t.includes('本店')) {
                            maxLen = t.length;
                            title = t;
                        }
                    }
                });
                
                // 价格
                var priceMatch = text.match(/[券后立减]?¥\\s*([\\d.]+)/);
                var price = priceMatch ? parseFloat(priceMatch[1]) : 0;
                
                // 销量
                var salesMatch = text.match(/已拼([\\d.]+[万千+]*)件/);
                var sales = salesMatch ? salesMatch[1] : '';
                if(!sales) {
                    salesMatch = text.match(/本店已拼([\\d.]+[万千+]*)/);
                    sales = salesMatch ? salesMatch[1] : '';
                }
                
                // 图片
                var imgs = card.querySelectorAll('img');
                var imgUrls = [];
                for(var j=0; j<imgs.length; j++) {
                    var src = imgs[j].src || imgs[j].dataset.src || '';
                    if(src && src.length > 30 && !src.includes('avatar') && !src.includes('icon')) {
                        imgUrls.push(src);
                    }
                }
                
                // 属性标签
                var attrs = '';
                var attrMatch = text.match(/预计[^\\n]*送达[\\s]*([^¥]*?)(?=[¥券立])/);
                if(attrMatch) attrs = attrMatch[1].trim();
                if(!attrs) {
                    attrMatch = text.match(/同款\\s*([^¥]*?)(?=[¥券立])/);
                    if(attrMatch) attrs = attrMatch[1].trim();
                }
                
                result.push({
                    title: title,
                    price: price,
                    sales: sales,
                    thumbImages: imgUrls.slice(0, 3),
                    attrs: attrs,
                    index: i
                });
            }
            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.toString()}); }
        """)
        
        try:
            return json.loads(items) if items else []
        except Exception:
            return []

    def _collect_detail_page(self, card_element, fallback_title: str = '') -> dict | None:
        """点击商品卡进入详情页，采集完整信息"""
        try:
            # 记录当前URL（搜索页）
            search_url = self.tab.url

            # 点击商品卡
            card_element.click()
            time.sleep(5)

            # 点击后tab可能断开连接，重新获取
            try:
                _ = self.tab.url
            except Exception:
                self.tab = self.chromium.latest_tab
                time.sleep(1)

            # 检查是否跳转到详情页
            current_url = self.tab.url
            goods_id = self._extract_goods_id(current_url)
            if not goods_id:
                self._log("  ⚠ 未跳转到详情页，跳过")
                return None

            # 滚动加载详情
            self._scroll_page(times=3, wait=1)

            # 提取详情页数据
            detail = self.tab.run_js("""
            try {
                var result = {};
                var bodyText = document.body.innerText;
                
                // === 标题 ===
                // 模式1: 已拼XX件后换行取标题行
                var titleMatch = bodyText.match(/已拼\\d+件\\n([^\\n]{10,200})/);
                if(titleMatch) result.title = titleMatch[1].trim();
                // 模式2: 即将卖完/恢复后取标题行
                if(!result.title) {
                    titleMatch = bodyText.match(/(?:即将卖完|即将恢复原价)\\n([^\\n]{10,200})/);
                    if(titleMatch) result.title = titleMatch[1].trim();
                }
                // 模式3: 价格后跳过数字行取标题
                if(!result.title) {
                    titleMatch = bodyText.match(/¥[\\d.\\n]+(?:已拼\\d+件|即将[^\\n]*)\\n([^\\n]{10,200})/);
                    if(titleMatch) result.title = titleMatch[1].trim();
                }
                // 过滤掉"拼多多"这种无效标题
                if(result.title && result.title.length < 5) result.title = '';
                if(!result.title || result.title === '拼多多') {
                    // 从搜索页传入的标题
                    result.title = '';
                }
                
                // === 价格 ===
                var priceMatch = bodyText.match(/¥\\s*([\\d.]+)/);
                result.price = priceMatch ? priceMatch[1] : '0';
                
                // === 销量 ===
                var salesMatch = bodyText.match(/已拼(\\d+[万+]*)件/);
                result.sales = salesMatch ? salesMatch[1] : '0';
                
                // === 商品详情区域 ===
                var detailStart = bodyText.indexOf('商品详情');
                if(detailStart > -1) {
                    var detailEnd = bodyText.indexOf('点击查看商品价格', detailStart);
                    if(detailEnd === -1) detailEnd = bodyText.indexOf('收藏', detailStart);
                    if(detailEnd === -1) detailEnd = detailStart + 3000;
                    result.description = bodyText.substring(detailStart, detailEnd).trim();
                } else {
                    result.description = '';
                }
                
                // === 评价摘要 ===
                var reviewStart = bodyText.indexOf('商品评价');
                if(reviewStart > -1) {
                    result.reviews = bodyText.substring(reviewStart, reviewStart + 500).trim();
                }
                
                // === 店铺 ===
                var shopMatch = bodyText.match(/\\n([^\\n]{2,20})\\n本店已拼/);
                result.shop = shopMatch ? shopMatch[1].trim() : '';
                if(!result.shop) {
                    shopMatch = bodyText.match(/([^\\n]{2,20})\\n进店逛逛/);
                    result.shop = shopMatch ? shopMatch[1].trim() : '';
                }
                
                // === 图片（商品图） ===
                var imgs = document.querySelectorAll('img');
                var imgUrls = [];
                var seen = new Set();
                for(var i=0; i<imgs.length; i++) {
                    var src = imgs[i].src || '';
                    if(src.length < 30) continue;
                    if(seen.has(src)) continue;
                    if(src.includes('avatar') || src.includes('icon') || src.includes('logo') || src.includes('banner')) continue;
                    if(src.includes('promotion') && !src.includes('goods')) continue;
                    if(src.includes('pddpic') || src.includes('yangkeduo') || src.includes('omsimg') || src.includes('pddugc')) {
                        // 排除小图标和loading图
                        var w = imgs[i].naturalWidth || imgs[i].width || 0;
                        if(w > 0 && w < 50) continue;
                        seen.add(src);
                        imgUrls.push(src);
                    }
                }
                result.images = imgUrls.slice(0, 20);
                
                // === SKU属性 ===
                var skuText = '';
                var skuLabels = document.querySelectorAll('[class*="sku"], [class*="Sku"]');
                skuLabels.forEach(function(el) {
                    var t = el.textContent.trim();
                    if(t.length > 0 && t.length < 50) skuText += t + '\\n';
                });
                result.sku = skuText.trim();
                
                // === 服务标签 ===
                var serviceMatch = bodyText.match(/([\\d]+天无理由退货[\\s\\S]*?)(?:商品评价|店铺)/);
                result.service = serviceMatch ? serviceMatch[1].trim() : '';
                
                return JSON.stringify(result);
            } catch(e) { return JSON.stringify({error: e.toString()}); }
            """)

            try:
                data = json.loads(detail) if detail else {}
            except Exception:
                data = {}

            if not data or 'error' in data:
                self._log(f"  ⚠ 详情页解析失败: {data.get('error', 'unknown')}")
                return None

            # 下载图片
            item_dir = os.path.join(IMAGE_DIR, f"pdd_{sanitize_filename(goods_id)}")
            ensure_dir(item_dir)

            local_images = []
            for idx, img_url in enumerate(data.get('images', [])[:15]):
                saved = self._download_image(img_url, item_dir, idx)
                if saved:
                    local_images.append(saved)

            # 构造商品对象
            price_str = data.get('price', '0')
            try:
                price_float = float(re.search(r'[\d.]+', price_str).group())
            except Exception:
                price_float = 0.0

            item = {
                "item_id": f"pdd_{goods_id}",
                "platform": "pdd",
                "title": data.get('title', '') or fallback_title,
                "original_title": data.get('title', '') or fallback_title,
                "description": data.get('description', ''),
                "original_price": str(price_float),
                "price": price_float,
                "image_urls": data.get('images', []),
                "local_images": local_images,
                "image_dir": item_dir,
                "attributes": {"sku": data.get('sku', ''), "service": data.get('service', '')},
                "seller": data.get('shop', ''),
                "seller_credit": '',
                "wants": data.get('sales', '0'),
                "views": "0",
                "collects": "0",
                "link": current_url,
                "source_url": current_url,
                "source_item_id": goods_id,
                "reviews": data.get('reviews', ''),
            }

            self._log(f"  ✓ {item['title'][:40]}  ¥{price_float}  销量:{item['wants']}  图片:{len(local_images)}张")
            return item

        except Exception as e:
            self._log(f"  ✗ 详情页采集失败: {e}")
            return None
        finally:
            # 返回搜索页
            try:
                self.tab = self.chromium.latest_tab
                self.tab.get(search_url)
                time.sleep(3)
            except Exception:
                pass

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        """关键词搜索采集"""
        try:
            self._init_browser()
            self.items = []
            self.seen_ids = set()
            self.seen_img_md5 = set()

            # === Step 1: 搜索页提取商品列表 ===
            self._log(f"正在搜索拼多多: {keyword}")
            search_items = self._collect_from_search_page(keyword, count)
            self._log(f"搜索页找到 {len(search_items)} 个商品")

            if not search_items:
                self._log("未找到商品，可能页面未加载或被反爬")
                return []

            # === Step 2: 逐个点击进入详情页采集 ===
            self._log(f"开始逐个采集详情页 (共{min(len(search_items), count)}个)...")

            for i, search_item in enumerate(search_items[:count]):
                self._log(f"采集 {i+1}/{min(len(search_items), count)}: {search_item.get('title', '')[:30]}...")

                # 重新找商品卡元素（页面可能刷新了）
                cards = self.tab.eles('._3glhOBhU', timeout=5)
                if i < len(cards):
                    item = self._collect_detail_page(cards[i], fallback_title=search_item.get('title', ''))
                    if item:
                        self.items.append(item)
                
                time.sleep(1)

            self._log(f"拼多多采集完成，共 {len(self.items)} 个商品")
            return self.items

        except Exception as e:
            raise Exception(f"拼多多采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个商品链接采集"""
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            goods_id = self._extract_goods_id(url)
            if not goods_id:
                raise Exception("无法从URL提取商品ID，请输入拼多多商品链接")

            self._log(f"采集拼多多商品: {url}")
            self.tab.get(url)
            time.sleep(5)
            self._scroll_page(times=3, wait=1)

            # 直接从详情页提取
            detail = self.tab.run_js("""
            try {
                var result = {};
                var bodyText = document.body.innerText;
                
                // 标题提取（多模式）
                var titleMatch = bodyText.match(/已拼\\d+件\\n([^\\n]{10,200})/);
                if(titleMatch) result.title = titleMatch[1].trim();
                if(!result.title) {
                    titleMatch = bodyText.match(/(?:即将卖完|即将恢复原价)\\n([^\\n]{10,200})/);
                    if(titleMatch) result.title = titleMatch[1].trim();
                }
                if(!result.title) {
                    titleMatch = bodyText.match(/¥[\\d.\\n]+(?:已拼\\d+件|即将[^\\n]*)\\n([^\\n]{10,200})/);
                    if(titleMatch) result.title = titleMatch[1].trim();
                }
                if(result.title && result.title.length < 5) result.title = '';
                if(!result.title || result.title === '拼多多') result.title = '';
                
                var priceMatch = bodyText.match(/¥\\s*([\\d.]+)/);
                result.price = priceMatch ? priceMatch[1] : '0';
                
                var salesMatch = bodyText.match(/已拼(\\d+[万+]*)件/);
                result.sales = salesMatch ? salesMatch[1] : '0';
                
                var detailStart = bodyText.indexOf('商品详情');
                if(detailStart > -1) {
                    var detailEnd = bodyText.indexOf('点击查看商品价格', detailStart);
                    if(detailEnd === -1) detailEnd = detailStart + 3000;
                    result.description = bodyText.substring(detailStart, detailEnd).trim();
                }
                
                var imgs = document.querySelectorAll('img');
                var imgUrls = [];
                var seen = new Set();
                for(var i=0; i<imgs.length; i++) {
                    var src = imgs[i].src || '';
                    if(src.length < 30 || seen.has(src)) continue;
                    if(src.includes('avatar') || src.includes('icon') || src.includes('logo')) continue;
                    if(src.includes('pddpic') || src.includes('yangkeduo') || src.includes('omsimg') || src.includes('pddugc')) {
                        seen.add(src);
                        imgUrls.push(src);
                    }
                }
                result.images = imgUrls.slice(0, 20);
                
                var shopMatch = bodyText.match(/\\n([^\\n]{2,20})\\n本店已拼/);
                result.shop = shopMatch ? shopMatch[1].trim() : '';
                
                return JSON.stringify(result);
            } catch(e) { return JSON.stringify({error: e.toString()}); }
            """)

            try:
                data = json.loads(detail) if detail else {}
            except Exception:
                data = {}

            if not data or 'error' in data:
                raise Exception(f"详情页解析失败: {data.get('error', 'unknown')}")

            # 下载图片
            item_dir = os.path.join(IMAGE_DIR, f"pdd_{sanitize_filename(goods_id)}")
            ensure_dir(item_dir)

            local_images = []
            for idx, img_url in enumerate(data.get('images', [])[:15]):
                saved = self._download_image(img_url, item_dir, idx)
                if saved:
                    local_images.append(saved)

            price_float = 0.0
            try:
                price_float = float(re.search(r'[\d.]+', data.get('price', '0')).group())
            except Exception:
                pass

            item = {
                "item_id": f"pdd_{goods_id}",
                "platform": "pdd",
                "title": data.get('title', ''),
                "original_title": data.get('title', ''),
                "description": data.get('description', ''),
                "original_price": str(price_float),
                "price": price_float,
                "image_urls": data.get('images', []),
                "local_images": local_images,
                "image_dir": item_dir,
                "attributes": {},
                "seller": data.get('shop', ''),
                "seller_credit": '',
                "wants": data.get('sales', '0'),
                "views": "0",
                "collects": "0",
                "link": url,
                "source_url": url,
                "source_item_id": goods_id,
            }

            self._log(f"✓ {item['title'][:40]}  ¥{price_float}  图片:{len(local_images)}张")
            self.items.append(item)
            return self.items

        except Exception as e:
            raise Exception(f"拼多多采集失败: {e}")
        finally:
            self._close_browser()
