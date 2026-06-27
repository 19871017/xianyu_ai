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
from engine.alibaba_sku_parser import parse_sku_from_html
from utils.image_dedup import dhash, is_near_duplicate, is_valid_product_image


# ─── 持久化Profile目录 ───────────────────────────────────────
ALIBABA_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".xf_1688_profile")

# ─── URL 常量 ─────────────────────────────────────────────────
LOGIN_URL = "https://login.1688.com/member/signin.htm"
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

    def _read_cookie_names(self) -> set:
        """读取当前标签的所有 Cookie 名称（含 httpOnly，document.cookie 读不到的）。"""
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
        """检查是否已登录1688 - 用 DrissionPage cookies() 读取(含 httpOnly)

        判断逻辑:
        1. 当前URL是否被重定向到登录页 → 未登录
        2. Cookie 中是否包含 1688 核心登录凭证 unb(5+位数字)
           - cookie17/_nk_/sg 作为辅助佐证, 任一存在即增强可信度
           - 关键修复: 旧实现用 document.cookie 读 cookie17, 但它是 httpOnly,
             JS 永远读不到 → 即使登录成功也判 false → 一直等待
        """
        try:
            tab = self._safe_tab()
            current_url = tab.url or ""

            # 被重定向到登录页 → 未登录
            if "login.taobao.com" in current_url or "login.1688.com" in current_url:
                return False

            names = self._read_cookie_names()
            if not names:
                return False

            # unb 是 1688/淘系核心用户ID, 存在即视为已登录
            if "unb" in names:
                return True

            # 辅助凭证(部分场景 unb 可能延迟写入), 命中任一也视为已登录
            aux_keys = {"cookie17", "_nk_", "sg", "_l_g_", "lid", "cancelledSubSites"}
            if aux_keys & names:
                return True

            return False
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

    def _bring_browser_to_front(self):
        """把采集浏览器窗口激活到前台, 确保用户能看到滑块验证页。"""
        try:
            tab = self._safe_tab()
            try:
                tab.set.activate()
            except Exception:
                pass
        except Exception:
            pass
        # macOS: 用 AppleScript 把 Chromium/Chrome 拉到最前
        try:
            import subprocess, sys
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

    def _detect_captcha(self) -> bool:
        """检测当前页是否被风控验证码拦截。

        1688 连续/快速访问详情页会触发滑块验证码(_____tmd_____/baxia/nc_ 等),
        页面标题常为"验证码拦截"。命中时返回 True, 让上层暂停并提示人工处理,
        避免静默返回空数据被误判为采集器损坏。
        """
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
                if (document.querySelector(\'#baxia-dialog-content, .nc-container, #nc_1_wrapper, [id*="nocaptcha"]\')) return true;
                var t = (document.body && document.body.innerText) ? document.body.innerText : \'\';
                if (t.indexOf(\'请拖动滑块\') >= 0 || t.indexOf(\'安全验证\') >= 0 || t.indexOf(\'验证码拦截\') >= 0) return true;
                return false;
            } catch(e) { return false; }
            ''')
            return bool(hit)
        except Exception:
            return False

    def _try_auto_solve_slider(self) -> bool:
        """尝试程序化拖动 nc 滑块验证码(拟人化轨迹)。

        1688 详情页风控常是 nc 滑块(#nc_1_n1z / .btn_slide)。这里用 DrissionPage
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
        """检测到验证码时, 提示用户在浏览器手动完成滑块, 轮询等待通过。

        Returns:
            True 验证码已通过(或本就无验证码); False 超时仍被拦截。
        """
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
        self._log("⚠️  自动验证未通过，触发1688安全验证(滑块验证码)")
        self._log("请在弹出的浏览器窗口手动完成滑块验证")
        self._log("完成后采集将自动继续")
        self._log("=" * 50)
        for i in range(timeout):
            time.sleep(1)
            if not self._detect_captcha():
                self._log("✅ 验证已通过，继续采集...")
                time.sleep(1)
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

    def _download_and_dedup_image(self, url: str, save_dir: str, index: int,
                                  md5_pool: set | None = None,
                                  dhash_pool: list | None = None) -> dict | None:
        """下载图片并去重（字节级 MD5 + 感知 dHash）

        Args:
            md5_pool/dhash_pool: 去重作用域。默认用主图池(self.seen_img_*)；
                SKU 图传入独立池，避免 SKU 色块图与主图内容相同时被误删。

        Returns:
            {"path": local_path, "md5": md5, "url": original_url} 或 None
        """
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
            # 过滤 SVG 图标/占位图/超小 UI 元素(非有效商品位图)
            if not is_valid_product_image(img_data):
                return None
            md5 = self._md5(img_data)
            if md5 in md5_pool:
                return None

            # 感知去重: 同款图换尺寸/重压缩后字节不同但肉眼一致, dHash 能挡掉
            img_hash = dhash(img_data)
            if is_near_duplicate(img_hash, dhash_pool):
                return None

            md5_pool.add(md5)
            if img_hash is not None:
                dhash_pool.append(img_hash)

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
        # detail.m.1688.com/page/index.html?offerId=123456789
        match = re.search(r'offerId=(\d+)', url)
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
        # 1688 老搜索端点 offer_search.htm 期望 GBK 编码的关键词;
        # 用默认 UTF-8 quote 会让服务器按 GBK 解出乱码 -> 搜不到结果。
        try:
            encoded_kw = quote(keyword, encoding="gbk")
        except Exception:
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
            # 提取页面中的商品链接。
            # 1688 搜索结果链接形态多变, offerId 可能在路径(offer/123.html)
            # 也可能在查询参数(detail.m.1688.com/page/index.html?offerId=123),
            # 这里统一抓出所有链接, 在 Python 侧用正则提取 offerId 再规范化。
            item_links = tab.run_js('''
                try {
                    var a = document.querySelectorAll('a[href]');
                    var result = [];
                    for(var i=0; i<a.length; i++) {
                        var h = a[i].href || '';
                        if(h.indexOf('offer') >= 0 || h.indexOf('detail') >= 0) {
                            result.push(h);
                        }
                    }
                    return JSON.stringify(result);
                } catch(e) { return '[]'; }
            ''') or '[]'

            item_links = json.loads(item_links) if isinstance(item_links, str) else (item_links or [])

            for link in item_links:
                offer_id = self._extract_item_id(link)
                if not offer_id:
                    continue
                # 跳过相似商品/导购等非详情入口
                if "similar_search" in link:
                    continue
                clean_link = f"https://detail.1688.com/offer/{offer_id}.html"
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

            # 风控验证码检测：触发滑块时暂停等待人工处理，避免静默返回空数据
            if self._detect_captcha():
                if not self._wait_captcha_cleared():
                    return None
                tab = self._safe_tab()

            item_id = self._extract_item_id(url)

            # ── 标题 ──
            # 1688 详情页 document.title 形如 "{商品标题} - 阿里巴巴", 最稳定;
            # 旧版优先用 [class*="title"] 选择器会误匹配页眉公司名/客服, 故改为优先 document.title。
            title = tab.run_js('''
                try {
                    var t = (document.title || '').replace(/[-_|]\s*阿里巴巴.*$/, '')
                              .replace(/[-_|]\s*1688.*$/, '').trim();
                    if (t && t.length >= 4) return t.substring(0, 200);
                    var h1 = document.querySelector('h1');
                    if (h1 && h1.textContent.trim()) return h1.textContent.trim().substring(0, 200);
                    var el = document.querySelector('.title-text, .od-pc-offer-title, [class*="offer-title"], [class*="d-title"]');
                    if (el) return el.textContent.trim().substring(0, 200);
                    return (document.title || '').trim().substring(0, 200);
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

            # ── SKU 多规格多价格 (来自页面内嵌 skuModel，最稳定) ──
            sku_list = []
            try:
                sku_list = parse_sku_from_html(tab.html or "")
            except Exception as e:
                self._log(f"  ⚠ SKU 解析异常: {e}")

            # 下载 SKU 规格图，回填本地路径
            # 用独立去重池: SKU 色块图可能与某张主图内容相同, 不能被主图池误删,
            # 但 SKU 之间仍需去重(同一张图对应多个规格时只存一份)。
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
                # SKU 价格回退到商品价
                for sku in sku_list:
                    if not sku.get("price"):
                        sku["price"] = price_float

            # ── 价格校正: DOM 抓的 [class*="price"] 易误匹配(如 ¥1.0 起订标识),
            #    SKU 价格来自内嵌数据最可靠, 用 SKU 最低有效价覆盖商品价。
            sku_prices = [
                float(s.get("price")) for s in (sku_list or [])
                if isinstance(s.get("price"), (int, float)) and float(s.get("price")) > 0
            ]
            if sku_prices:
                price_float = min(sku_prices)

            item = {
                "item_id": f"1688_{item_id}",
                "platform": "1688",
                "title": title,
                "original_title": title,
                "description": description,
                "original_price": str(price_float) if price_float else price_text,
                "price": price_float,
                "sku_list": sku_list,
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
                f"销量: {sales}  图片: {len(local_images)}张  SKU: {len(sku_list)}个"
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
            self.seen_img_dhash = []

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
            self.seen_img_dhash = []

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
