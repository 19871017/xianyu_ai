"""拼多多商品上架器 - 自动在拼多多商家后台发布商品

商家后台地址: https://mms.pinduoduo.com/
发布商品路径: 商品管理 → 发布商品
"""
import time
import os
import json
from DrissionPage import Chromium
from utils.browser_config import get_chromium_options, check_browser_available


class PddLister:
    """拼多多商品上架器

    流程:
    1. 打开商家后台 mms.pinduoduo.com
    2. 检查登录状态（未登录则等待用户扫码）
    3. 进入发布商品页面
    4. 填写: 商品名称 / 描述 / 价格 / 库存 / 运费模板 / 图片
    5. 提交发布
    """

    SELLER_URL = "https://mms.pinduoduo.com/"
    PUBLISH_URL = "https://mms.pinduoduo.com/goods/goods_commit"

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
        user_data_dir = _os.path.join(_os.path.expanduser("~"), ".pdd_seller_profile")
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
        """等待用户完成拼多多商家后台登录（最多 timeout 秒）"""
        self._log("请在浏览器中完成拼多多商家后台登录（扫码或账号密码）...")
        for i in range(timeout):
            time.sleep(1)
            try:
                url = self.tab.url
                if "mms.pinduoduo.com" in url and "login" not in url.lower():
                    self._log("✅ 拼多多商家后台登录成功")
                    return True
            except Exception:
                pass
            if i % 15 == 0 and i > 0:
                self._log(f"等待登录中... ({i}s/{timeout}s)")
        return False

    def _fill_title(self, title: str):
        """填写商品名称"""
        try:
            # 拼多多商品名称输入框
            selectors = [
                'input[placeholder*="商品名称"]',
                'input[placeholder*="请输入商品名称"]',
                '.goods-name input',
                'input[maxlength="60"]',
            ]
            for sel in selectors:
                el = self.tab.ele(f"css:{sel}", timeout=3)
                if el:
                    el.clear()
                    el.input(title[:60])
                    self._log(f"  ✓ 填写商品名称: {title[:30]}...")
                    return True
            self._log("  ⚠ 未找到商品名称输入框")
            return False
        except Exception as e:
            self._log(f"  ✗ 填写名称失败: {e}")
            return False

    def _fill_price(self, price: str, original_price: str = None):
        """填写价格"""
        try:
            # 主价格
            price_selectors = [
                'input[placeholder*="请输入拼团价"]',
                'input[placeholder*="销售价"]',
                'input[placeholder*="价格"]',
                '.price-input input',
            ]
            for sel in price_selectors:
                el = self.tab.ele(f"css:{sel}", timeout=3)
                if el:
                    el.clear()
                    el.input(str(price))
                    self._log(f"  ✓ 填写价格: ¥{price}")
                    break

            # 划线原价（可选）
            if original_price:
                orig_selectors = [
                    'input[placeholder*="原价"]',
                    'input[placeholder*="市场价"]',
                    'input[placeholder*="划线价"]',
                ]
                for sel in orig_selectors:
                    el = self.tab.ele(f"css:{sel}", timeout=2)
                    if el:
                        el.clear()
                        el.input(str(original_price))
                        break
            return True
        except Exception as e:
            self._log(f"  ✗ 填写价格失败: {e}")
            return False

    def _fill_stock(self, stock: int = 999):
        """填写库存"""
        try:
            stock_selectors = [
                'input[placeholder*="库存"]',
                'input[placeholder*="请输入库存"]',
                '.stock-input input',
            ]
            for sel in stock_selectors:
                el = self.tab.ele(f"css:{sel}", timeout=3)
                if el:
                    el.clear()
                    el.input(str(stock))
                    self._log(f"  ✓ 填写库存: {stock}")
                    return True
            return False
        except Exception as e:
            self._log(f"  ✗ 填写库存失败: {e}")
            return False

    def _fill_description(self, description: str):
        """填写商品描述（富文本编辑器）"""
        try:
            desc_selectors = [
                '[contenteditable="true"]',
                '.rich-editor [contenteditable]',
                'textarea[placeholder*="描述"]',
                'textarea[placeholder*="详情"]',
            ]
            for sel in desc_selectors:
                el = self.tab.ele(f"css:{sel}", timeout=3)
                if el:
                    el.click()
                    time.sleep(0.3)
                    el.input(description[:2000])
                    self._log("  ✓ 填写商品描述")
                    return True
            return False
        except Exception as e:
            self._log(f"  ✗ 填写描述失败: {e}")
            return False

    def _upload_images(self, image_paths: list) -> bool:
        """上传商品主图"""
        if not image_paths:
            self._log("  ⚠ 无本地图片，跳过上传")
            return True
        try:
            file_input = self.tab.ele('css:input[type="file"]', timeout=5)
            if not file_input:
                self._log("  ⚠ 未找到文件上传控件")
                return False

            valid_paths = [p for p in image_paths[:10] if os.path.exists(p)]
            if not valid_paths:
                return False

            file_input.input(valid_paths)
            self._log(f"  ✓ 上传 {len(valid_paths)} 张图片，等待上传完成...")
            time.sleep(5)
            return True
        except Exception as e:
            self._log(f"  ✗ 上传图片失败: {e}")
            return False

    def _submit(self) -> bool:
        """点击提交/发布"""
        try:
            submit_selectors = [
                'button[type="submit"]',
                'button:contains("提交")',
                'button:contains("发布")',
                'button:contains("保存")',
                '.submit-btn',
                '.publish-btn',
            ]
            for sel in submit_selectors:
                try:
                    btn = self.tab.ele(f"css:{sel}", timeout=2)
                    if btn:
                        btn.click()
                        time.sleep(4)
                        self._log("  ✓ 已点击发布按钮")
                        return True
                except Exception:
                    pass

            # 文字匹配
            for text in ["提交", "发布", "保存并发布"]:
                try:
                    btn = self.tab.ele(f"text:{text}", timeout=2)
                    if btn:
                        btn.click()
                        time.sleep(4)
                        return True
                except Exception:
                    pass

            self._log("  ⚠ 未找到发布按钮")
            return False
        except Exception as e:
            self._log(f"  ✗ 提交失败: {e}")
            return False

    def list_item(
        self,
        item: dict,
        price: str = None,
        price_markup_pct: float = 0,
        stock: int = 999,
        wait_login: bool = True,
    ) -> dict:
        """上架单个商品到拼多多

        Args:
            item: 商品数据字典
            price: 覆盖价格
            price_markup_pct: 加价百分比
            stock: 库存数量（默认 999）
            wait_login: 是否等待用户登录（首次使用需要）

        Returns:
            {"success": bool, "item_id": str, "error": str}
        """
        try:
            self._init_browser()

            # 计算最终价格
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
                    final_price = "9.90"

            # 打开商家后台
            self._log("打开拼多多商家后台...")
            self.tab.get(self.SELLER_URL)
            time.sleep(3)

            # 检查登录状态
            if "login" in self.tab.url.lower() or "passport" in self.tab.url.lower():
                if wait_login:
                    logged_in = self._wait_for_login(180)
                    if not logged_in:
                        return {"success": False, "item_id": item.get("item_id", ""), "error": "登录超时"}
                else:
                    return {"success": False, "item_id": item.get("item_id", ""), "error": "未登录拼多多商家后台"}

            # 进入发布商品页
            self._log("进入发布商品页面...")
            self.tab.get(self.PUBLISH_URL)
            time.sleep(4)

            # 上传图片
            images = item.get("local_images", [])
            self._upload_images(images)
            time.sleep(1)

            # 填写标题
            title = item.get("ai_title") or item.get("original_title") or item.get("title", "")
            self._fill_title(title)
            time.sleep(0.5)

            # 填写价格
            orig_price = item.get("original_price", "")
            self._fill_price(final_price, orig_price)
            time.sleep(0.5)

            # 填写库存
            self._fill_stock(stock)
            time.sleep(0.5)

            # 填写描述
            desc = item.get("ai_description") or item.get("description", "")
            if desc:
                self._fill_description(desc)
                time.sleep(0.5)

            # 提交
            success = self._submit()

            # 检查结果
            time.sleep(2)
            page_text = self.tab.run_js("return document.body.innerText || ''") or ""
            if "成功" in page_text or "审核" in page_text:
                success = True
            elif "失败" in page_text or "错误" in page_text:
                success = False

            return {
                "success": success,
                "item_id": item.get("item_id", ""),
                "error": "" if success else "发布可能失败，请检查商家后台",
                "platform": "pdd",
            }

        except Exception as e:
            return {"success": False, "item_id": item.get("item_id", ""), "error": str(e), "platform": "pdd"}
        finally:
            self._close_browser()

    def batch_list(
        self,
        items: list,
        price: str = None,
        price_markup_pct: float = 0,
        stock: int = 999,
        delay: int = 10,
        on_progress=None,
    ) -> list:
        """批量上架到拼多多"""
        results = []
        for i, item in enumerate(items):
            if on_progress:
                on_progress(i + 1, len(items))
            result = self.list_item(item, price, price_markup_pct, stock, wait_login=(i == 0))
            results.append(result)
            if i < len(items) - 1:
                time.sleep(delay)
        return results
