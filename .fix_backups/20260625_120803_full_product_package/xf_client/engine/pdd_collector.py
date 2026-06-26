"""拼多多商品采集器 - PC首页推荐商品采集

拼多多移动端和PC端商品详情页都需要登录才能访问。
采集策略：PC首页推荐商品 + 商品链接详情页采集（需登录）。

采集流程：
1. 访问 https://www.pinduoduo.com/ 首页
2. 提取所有 goods-group 中的商品信息
3. 商品图片用高质量URL下载
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


# 拼多多PC用户数据目录（登录态）
PDD_USER_DATA_DIR = os.path.join(os.path.expanduser("~"), ".xf_pdd_profile")


class PddCollector:
    """拼多多商品采集器
    
    支持两种采集模式：
    1. 首页推荐：访问PC首页 https://www.pinduoduo.com/ 采集推荐商品（无需登录）
    2. 链接采集：直接访问商品详情页（需要登录，详情页强制登录）
    
    提示：拼多多搜索功能需要APP登录，PC端不支持关键词搜索。
    如需搜索商品，请使用"商品链接采集"模式，将APP中的商品链接粘贴进来。
    """

    def __init__(self, on_progress=None):
        self.chromium = None
        self.tab = None
        self.items = []
        self.seen_img_md5 = set()
        self.on_progress = on_progress

    def _log(self, msg):
        if self.on_progress:
            self.on_progress(msg)

    def _init_browser(self):
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")
        co, _port = get_chromium_options(user_data_dir=PDD_USER_DATA_DIR)
        self.chromium = Chromium(co)
        self.tab = self.chromium.latest_tab

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
            # 高质量图片URL：去掉缩略图参数
            clean_url = re.sub(r'[?&/]thumbnail[=/][^&?]*', '', url)
            clean_url = re.sub(r'[?&/]imageMogr2[^&?]*', '', clean_url)
            clean_url = clean_url.replace('?imageMogr2/thumbnail/400x/q/80/format/webp', '')
            
            # 尝试高清图
            if 'gaudit-image' in clean_url or 'pddpic.com' in clean_url:
                # gaudi图片格式：替换后缀获取高清
                if '.jpeg' in clean_url.lower():
                    hd_url = clean_url.replace('.jpeg.a.jpeg', '.jpeg')
                elif '.jpg' in clean_url.lower():
                    hd_url = clean_url.replace('.jpg.a.jpg', '.jpg')
                else:
                    hd_url = clean_url
            else:
                hd_url = clean_url

            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.pinduoduo.com/"
            }
            resp = requests.get(hd_url, timeout=15, headers=headers)
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

    def _extract_goods_id_from_url(self, url: str) -> str:
        """从URL提取goods_id"""
        match = re.search(r'goods_id=(\d+)', url)
        if match:
            return match.group(1)
        match = re.search(r'/goods[_-]?(\d+)', url)
        if match:
            return match.group(1)
        return ""

    def _collect_from_homepage(self, count: int = 50) -> list[dict]:
        """从PC首页提取推荐商品"""
        self._log("访问拼多多PC首页...")
        self.tab.get("https://www.pinduoduo.com/")
        
        # 等待页面加载
        for _ in range(10):
            time.sleep(2)
            body_len = self.tab.run_js("return document.body.innerText.length;")
            if body_len and int(body_len) > 500:
                break
        
        # 滚动加载更多
        self._log("滚动加载商品...")
        for i in range(5):
            self.tab.run_js("window.scrollBy(0, 1000);")
            time.sleep(2)
        
        # 提取商品数据
        goods_data = self.tab.run_js("""
        try {
            var groups = document.querySelectorAll('.goods-group');
            var result = [];
            
            for(var i=0; i<groups.length; i++) {
                var g = groups[i];
                
                // 标题：div.title
                var titleEl = g.querySelector('.title');
                var title = titleEl ? titleEl.textContent.trim() : '';
                
                // 现价：span.group-price
                var priceEl = g.querySelector('.group-price');
                var price = priceEl ? priceEl.textContent.trim() : '';
                
                // 原价：span.market-price
                var marketEl = g.querySelector('.market-price');
                var marketPrice = marketEl ? marketEl.textContent.replace('￥', '').trim() : '';
                
                // 图片：img.img 或 img
                var imgEl = g.querySelector('img.img') || g.querySelector('img');
                var imgSrc = imgEl ? (imgEl.src || imgEl.getAttribute('data-src') || '') : '';
                
                // 销量（如果有）
                var salesEl = g.querySelector('[class*="sales"], [class*="sold"]');
                var sales = salesEl ? salesEl.textContent.trim() : '';
                
                // 提取goods_id
                var onclick = g.getAttribute('onclick') || '';
                var gidMatch = onclick.match(/goods_id[=:]?['"]?(\d+)/);
                var goodsId = gidMatch ? gidMatch[1] : '';
                
                if(!goodsId) {
                    // 尝试从data-*属性
                    var dataGoods = g.getAttribute('data-goods-id') || g.getAttribute('data-goodsId') || '';
                    var dm = dataGoods.match(/(\d+)/);
                    if(dm) goodsId = dm[1];
                }
                
                // 商品链接
                var link = goodsId ? 'https://mobile.yangkeduo.com/goods.html?goods_id=' + goodsId : '';
                
                if(title && title.length > 3) {
                    result.push({
                        title: title.substring(0, 100),
                        price: price || '0',
                        marketPrice: marketPrice || '0',
                        image: imgSrc,
                        goodsId: goodsId,
                        link: link,
                        index: i
                    });
                }
            }
            
            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.toString()}); }
        """)
        
        try:
            items = json.loads(goods_data) if goods_data else []
            if isinstance(items, dict) and 'error' in items:
                self._log(f"提取失败: {items['error']}")
                return []
            return items
        except Exception:
            return []

    def _scroll_to_load_more(self):
        """滚动加载更多商品"""
        try:
            for _ in range(3):
                self.tab.run_js("window.scrollBy(0, 1000);")
                time.sleep(2)
        except Exception:
            pass

    def search_by_keyword(self, keyword: str, count: int = 50) -> list:
        """关键词搜索采集
        
        拼多多反爬严格：
        - PC端不支持关键词搜索，首页商品是分类推荐（无商品链接）
        - 移动端搜索需要登录，且检测到自动化会返回"系统繁忙"
        
        策略：采集PC首页推荐商品（标题/价格/图片），提示用户用链接采集获取详情。
        """
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            self._log("⚠️ 拼多多不支持关键词搜索（反爬限制）")
            self._log("改为采集PC首页推荐商品（标题/价格/图片）")
            self._log("💡 如需特定商品，请用拼多多APP复制商品链接，粘贴到「商品链接采集」")

            # 采集首页商品
            home_items = self._collect_from_homepage(count)
            self._log(f"首页找到 {len(home_items)} 个商品")
            
            if not home_items:
                self._log("未找到商品，可能页面未加载")
                return []

            # 下载图片
            ensure_dir(IMAGE_DIR)
            
            for i, home_item in enumerate(home_items):
                self._log(f"处理 {i+1}/{len(home_items)}: {home_item.get('title', '')[:30]}...")
                
                goods_id = home_item.get('goodsId', f'pdd_home_{i}')
                item_dir = os.path.join(IMAGE_DIR, f"pdd_{sanitize_filename(goods_id)}")
                ensure_dir(item_dir)

                # 下载图片
                local_images = []
                img_url = home_item.get('image', '')
                if img_url:
                    saved = self._download_image(img_url, item_dir, 0)
                    if saved:
                        local_images.append(saved)

                # 提取价格数字
                price_str = home_item.get('price', '0')
                try:
                    price_float = float(re.search(r'[\d.]+', price_str).group())
                except Exception:
                    price_float = 0.0

                market_price_str = home_item.get('marketPrice', '0')
                try:
                    market_price = float(re.search(r'[\d.]+', market_price_str).group())
                except Exception:
                    market_price = price_float

                item = {
                    "item_id": f"pdd_{goods_id}",
                    "platform": "pdd",
                    "title": home_item.get('title', ''),
                    "original_title": home_item.get('title', ''),
                    "description": f"拼多多推荐商品 | 原价: ¥{market_price}",
                    "original_price": str(market_price),
                    "price": price_float,
                    "image_urls": [img_url] if img_url else [],
                    "local_images": local_images,
                    "image_dir": item_dir,
                    "attributes": {},
                    "seller": "",
                    "seller_credit": "",
                    "wants": home_item.get('sales', '0'),
                    "views": "0",
                    "collects": "0",
                    "link": home_item.get('link', ''),
                    "source_url": home_item.get('link', ''),
                    "source_item_id": goods_id,
                }
                
                self.items.append(item)

            self._log(f"拼多多采集完成，共 {len(self.items)} 个商品")
            return self.items

        except Exception as e:
            raise Exception(f"拼多多采集失败: {e}")
        finally:
            self._close_browser()

    def collect_by_link(self, url: str) -> list:
        """单个商品链接采集
        
        拼多多商品详情页需要登录才能访问。
        如果未登录，会被重定向到登录页。
        请先在采集页面点击「登录拼多多」按钮扫码登录。
        """
        try:
            self._init_browser()
            self.items = []
            self.seen_img_md5 = set()

            goods_id = self._extract_goods_id_from_url(url)
            if not goods_id:
                raise Exception("无法从URL提取商品ID，请输入拼多多商品链接")

            self._log(f"采集拼多多商品: {url}")

            # 检查登录状态（访问移动端详情页）
            self._log("检查登录状态...")
            self.tab.get("https://mobile.yangkeduo.com/goods.html?goods_id=" + goods_id)
            time.sleep(5)

            if "login" in self.tab.url.lower():
                raise Exception("拼多多未登录，请先在采集页面点击「登录拼多多」按钮扫码登录")

            # 滚动加载
            for _ in range(3):
                try:
                    self.tab.run_js("window.scrollBy(0, 600);")
                except Exception:
                    pass
                time.sleep(1)

            # 提取详情页数据（用双转义避免Python三引号问题）
            detail_js = """
            function() {
                var result = {};
                var bodyText = document.body.innerText;
                
                // 标题：匹配 "已拼123件\n商品名" 格式
                var titleMatch = bodyText.match(/已拼\\d+件\\n([^\n]{10,200})/);
                if(!titleMatch) {
                    titleMatch = bodyText.match(/(?:即将卖完|即将恢复原价)\\n([^\n]{10,200})/);
                }
                if(!titleMatch) {
                    var priceIdx = bodyText.indexOf('¥');
                    if(priceIdx > -1) {
                        var afterPrice = bodyText.substring(priceIdx);
                        var lines = afterPrice.split('\\n');
                        for(var li=0; li<lines.length; li++) {
                            if(lines[li].length > 10 && lines[li].length < 100) {
                                titleMatch = [null, lines[li].trim()];
                                break;
                            }
                        }
                    }
                }
                result.title = titleMatch ? titleMatch[1].trim() : '';
                if(result.title === '拼多多' || result.title.length < 3) result.title = '';
                
                // 价格
                var priceMatch = bodyText.match(/¥\s*([\d.]+)/);
                result.price = priceMatch ? priceMatch[1] : '0';
                
                // 销量
                var salesMatch = bodyText.match(/已拼(\d+[万人+]*)件/);
                result.sales = salesMatch ? salesMatch[1] : '0';
                
                // 商品详情（商品详情区域）
                var detailIdx = bodyText.indexOf('商品详情');
                if(detailIdx > -1) {
                    var endIdx = bodyText.indexOf('点击查看', detailIdx);
                    if(endIdx === -1) endIdx = detailIdx + 2000;
                    result.description = bodyText.substring(detailIdx, endIdx).trim();
                }
                
                // 图片
                var imgs = document.querySelectorAll('img');
                var imgUrls = [];
                var seen = {};
                for(var ii=0; ii<imgs.length; ii++) {
                    var src = imgs[ii].src || '';
                    if(src.length < 30) continue;
                    if(seen[src]) continue;
                    if(src.includes('avatar') || src.includes('icon') || src.includes('logo')) continue;
                    if(src.includes('pddpic') || src.includes('yangkeduo') || src.includes('omsimg') || src.includes('pddugc')) {
                        seen[src] = true;
                        imgUrls.push(src);
                    }
                }
                result.images = imgUrls.slice(0, 20);
                
                // 店铺
                var shopMatch = bodyText.match(/\\n([^\n]{2,20})\\n本店已拼/);
                result.shop = shopMatch ? shopMatch[1].trim() : '';
                
                return JSON.stringify(result);
            }
            """
            
            try:
                detail = self.tab.run_js(detail_js)
            except Exception:
                detail = "{}"

            try:
                data = json.loads(detail) if detail else {}
            except Exception:
                data = {}

            if not data:
                self._log("详情页解析失败，尝试从URL采集基础信息")
                data = {}

            # 下载图片
            item_dir = os.path.join(IMAGE_DIR, f"pdd_{sanitize_filename(goods_id)}")
            ensure_dir(item_dir)

            local_images = []
            for idx, img_url in enumerate(data.get('images', [])[:15]):
                saved = self._download_image(img_url, item_dir, idx)
                if saved:
                    local_images.append(saved)

            # 如果没有下载到图片，尝试从page重新获取
            if not local_images:
                self._log("尝试补充图片...")
                img_urls = self.tab.run_js("""
                var imgs = document.querySelectorAll('img');
                var urls = [];
                var seen = {};
                for(var i=0; i<imgs.length; i++) {
                    var src = imgs[i].src || '';
                    if(src.length > 30 && !seen[src] && 
                       (src.includes('pddpic') || src.includes('yangkeduo') || src.includes('omsimg'))) {
                        seen[src] = true;
                        urls.push(src);
                    }
                }
                return JSON.stringify(urls.slice(0, 15));
                """)
                try:
                    extra_imgs = json.loads(img_urls) if img_urls else []
                    for idx, img_url in enumerate(extra_imgs):
                        saved = self._download_image(img_url, item_dir, len(local_images) + idx)
                        if saved:
                            local_images.append(saved)
                except Exception:
                    pass

            price_float = 0.0
            try:
                price_float = float(re.search(r'[\d.]+', data.get('price', '0')).group())
            except Exception:
                pass

            item = {
                "item_id": f"pdd_{goods_id}",
                "platform": "pdd",
                "title": data.get('title', '') or f"拼多多商品 {goods_id}",
                "original_title": data.get('title', '') or f"拼多多商品 {goods_id}",
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

            self._log(f"  ✓ {item['title'][:40]}  ¥{price_float}  销量:{item['wants']}  图片:{len(local_images)}张")
            self.items.append(item)

            self._log(f"拼多多采集完成，共 {len(self.items)} 个商品")
            return self.items

        except Exception as e:
            raise Exception(f"拼多多采集失败: {e}")
        finally:
            self._close_browser()
