"""京东商品上架器 - 自动在京东商家后台发布商品

商家后台: https://pop.jd.com/
发布商品入口: 商品管理 → 新增商品
"""
import time
import os
from DrissionPage import Chromium
from utils.browser_config import get_chromium_options, check_browser_available


class JDLister:
    """京东商品上架器

    流程:
    1. 打开 pop.jd.com 商家后台
    2. 等待用户登录（Cookie 持久化，二次使用免登录）
    3. 进入商品发布页（商品管理 → 发布商品）
    4. 填写: 商品名称 / 详情描述 / 价格 / 库存 / 图片
    5. 提交审核/发布

    注意: 京东商家后台需要营业执照认证，普通用户无法使用此功能。
    """

    SELLER_URL = "https://pop.jd.com/"
    GOODS_URL = "https://pop.jd.com/goods/addGoods.html"

    def __init__(self, on_progress=None):
        self.chromium = None
        self.tab = None
        self.on_progress = on_progress

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)

    def _init_browser(self):
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")
        import os as _os
        user_data_dir = _os.path.join(_os.path.expanduser("~"), ".jd_seller_profile")
        _os.makedirs(user_data_dir, exist_ok=True)
        co, _port = get_chromium_options(user_data_dir=user_data_dir)
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

    def _wait_for_login(self, timeout: int = 180) -> bool:
        """等待京东商家后台登录"""
        self._log("请在浏览器中完成京东商家后台（pop.jd.com）登录...")
        for i in range(timeout):
            time.sleep(1)
            try:
                url = self.tab.url
                if "pop.jd.com" in url and "login" not in url.lower() and "passport" not in url.lower():
                    self._log("✅ 京东商家后台登录成功")
                    return True
            except Exception:
                pass
            if i % 20 == 0 and i > 0:
                self._log(f"等待登录中... ({i}s)")
        return False

    def _select_category(self, category: str = None):
        """选择商品类目（京东必填，按需传入）"""
        if not category:
            return
        try:
            # 尝试点击类目选择区域
            cat_btn = self.tab.ele('css:[class*="category"], [class*="cate"]', timeout=3)
            if cat_btn:
                cat_btn.click()
                time.sleep(1)
                # 搜索类目
                search_input = self.tab.ele('css:input[placeholder*="类目"]', timeout=2)
                if search_input:
                    search_input.input(category)
                    time.sleep(1.5)
                    result = self.tab.ele('css:[class*="search-result"] li', timeout=2)
                    if result:
                        result.click()
                        time.sleep(0.5)
        except Exception:
            pass  # 类目选择不阻塞流程

    def _fill_title(self, title: str) -> bool:
        """填写商品名称"""
        try:
            selectors = [
                'input[placeholder*="商品名称"]',
                'input[placeholder*="请输入标题"]',
                'input[name*="title"]',
                'input[name*="name"]',
                '#goodsTitle',
                '.goods-title input',
            ]
            for sel in selectors:
                el = self.tab.ele(f"css:{sel}", timeout=2)
                if el:
                    el.clear()
                    el.input(title[:60])
                    self._log(f"  ✓ 商品名称: {title[:30]}...")
                    return True
            self._log("  ⚠ 未找到商品名称框")
            return False
        except Exception as e:
            self._log(f"  ✗ 名称填写失败: {e}")
            return False

    def _fill_price(self, price: str) -> bool:
        """填写商品价格"""
        try:
            selectors = [
                'input[placeholder*="价格"]',
                'input[placeholder*="销售价"]',
                'input[name*="price"]',
                '#jdPrice',
                '.price-input input',
            ]
            for sel in selectors:
                el = self.tab.ele(f"css:{sel}", timeout=2)
                if el:
                    el.clear()
                    el.input(str(price))
                    self._log(f"  ✓ 价格: ¥{price}")
                    return True
            return False
        except Exception as e:
            self._log(f"  ✗ 价格填写失败: {e}")
            return False

    def _fill_stock(self, stock: int = 100) -> bool:
        """填写库存"""
        try:
            selectors = [
                'input[placeholder*="库存"]',
                'input[name*="stock"]',
                'input[name*="num"]',
                '#stockNum',
            ]
            for sel in selectors:
                el = self.tab.ele(f"css:{sel}", timeout=2)
                if el:
                    el.clear()
                    el.input(str(stock))
                    self._log(f"  ✓ 库存: {stock}")
                    return True
            return False
        except Exception as e:
            self._log(f"  ✗ 库存填写失败: {e}")
            return False

    def _fill_description(self, description: str) -> bool:
        """填写商品详情（富文本）"""
        try:
            # 尝试富文本编辑器
            editor = self.tab.ele('css:[contenteditable="true"]', timeout=3)
            if editor:
                editor.click()
                time.sleep(0.3)
                editor.input(description[:3000])
                self._log("  ✓ 商品详情已填写")
                return True

            # 普通 textarea
            ta = self.tab.ele('css:textarea[placeholder*="详情"]', timeout=2)
            if ta:
                ta.clear()
                ta.input(description[:3000])
                return True
            return False
        except Exception as e:
            self._log(f"  ✗ 详情填写失败: {e}")
            return False

    def _upload_images(self, image_paths: list) -> bool:
        """上传商品主图"""
        if not image_paths:
            return True
        try:
            file_input = self.tab.ele('css:input[type="file"]', timeout=5)
            if not file_input:
                self._log("  ⚠ 未找到上传控件")
                return False
            valid = [p for p in image_paths[:10] if os.path.exists(p)]
            if not valid:
                return False
            file_input.input(valid)
            self._log(f"  ✓ 上传 {len(valid)} 张图片...")
            time.sleep(6)
            return True
        except Exception as e:
            self._log(f"  ✗ 图片上传失败: {e}")
            return False

    def _submit(self) -> bool:
        """提交发布"""
        try:
            for text in ["提交审核", "发布商品", "保存并发布", "提交"]:
                btn = self.tab.ele(f"text:{text}", timeout=2)
                if btn:
                    btn.click()
                    time.sleep(4)
                    self._log(f"  ✓ 点击「{text}」")
                    return True

            btn = self.tab.ele('css:button[type="submit"]', timeout=2)
            if btn:
                btn.click()
                time.sleep(4)
                return True
            return False
        except Exception as e:
            self._log(f"  ✗ 提交失败: {e}")
            return False

    def list_item(
        self,
        item: dict,
        price: str = None,
        price_markup_pct: float = 0,
        stock: int = 100,
        category: str = None,
        wait_login: bool = True,
    ) -> dict:
        """上架单个商品到京东

        Returns:
            {"success": bool, "item_id": str, "error": str, "platform": "jd"}
        """
        try:
            self._init_browser()

            # 计算价格
            final_price = price
            if not final_price:
                base = item.get("price", 0) or item.get("original_price", 0)
                try:
                    base = float(str(base).replace(",", ""))
                except Exception:
                    base = 0.0
                if base > 0 and price_markup_pct > 0:
                    final_price = f"{base * (1 + price_markup_pct / 100):.2f}"
                elif base > 0:
                    final_price = f"{base:.2f}"
                else:
                    final_price = "19.90"

            # 打开后台
            self._log("打开京东商家后台 (pop.jd.com)...")
            self.tab.get(self.SELLER_URL)
            time.sleep(3)

            if "login" in self.tab.url.lower() or "passport" in self.tab.url.lower():
                if wait_login:
                    if not self._wait_for_login(180):
                        return {"success": False, "item_id": item.get("item_id", ""), "error": "登录超时", "platform": "jd"}
                else:
                    return {"success": False, "item_id": item.get("item_id", ""), "error": "未登录京东商家后台", "platform": "jd"}

            # 进入发布页
            self._log("进入商品发布页...")
            self.tab.get(self.GOODS_URL)
            time.sleep(4)

            # 选类目
            if category:
                self._select_category(category)
                time.sleep(1)

            # 上传图片
            self._upload_images(item.get("local_images", []))
            time.sleep(1)

            # 填写信息
            title = item.get("ai_title") or item.get("original_title") or item.get("title", "")
            self._fill_title(title)
            time.sleep(0.3)

            self._fill_price(final_price)
            time.sleep(0.3)

            self._fill_stock(stock)
            time.sleep(0.3)

            desc = item.get("ai_description") or item.get("description", "")
            if desc:
                self._fill_description(desc)
                time.sleep(0.3)

            success = self._submit()

            page_text = self.tab.run_js("return document.body.innerText || ''") or ""
            if "审核中" in page_text or "成功" in page_text or "提交成功" in page_text:
                success = True

            return {
                "success": success,
                "item_id": item.get("item_id", ""),
                "error": "" if success else "请在商家后台确认发布状态",
                "platform": "jd",
            }

        except Exception as e:
            return {"success": False, "item_id": item.get("item_id", ""), "error": str(e), "platform": "jd"}
        finally:
            self._close_browser()

    def batch_list(self, items: list, price=None, price_markup_pct=0, stock=100, delay=15, on_progress=None) -> list:
        """批量上架到京东"""
        results = []
        for i, item in enumerate(items):
            if on_progress:
                on_progress(i + 1, len(items))
            result = self.list_item(item, price, price_markup_pct, stock, wait_login=(i == 0))
            results.append(result)
            if i < len(items) - 1:
                time.sleep(delay)
        return results
