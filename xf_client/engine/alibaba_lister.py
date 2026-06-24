"""阿里巴巴(1688)商品上架器 - 自动在1688商家后台发布商品

商家后台: https://wangpu.1688.com/
发布商品入口: 商品管理 → 发布新产品
"""
import time
import os
from DrissionPage import Chromium
from utils.browser_config import get_chromium_options, check_browser_available


class AlibabaLister:
    """1688商品上架器

    流程:
    1. 打开 wangpu.1688.com 商家后台
    2. 等待用户登录（Cookie 持久化，二次免登录）
    3. 进入发布产品页
    4. 填写: 产品标题 / 描述 / 价格/起订量 / 库存 / 图片
    5. 发布

    注意: 1688是B2B平台，发布时需要设置起订量(MOQ)和批发价格区间。
    """

    SELLER_URL = "https://wangpu.1688.com/"
    PUBLISH_URL = "https://product.1688.com/product/publishProduct.htm"
    ALT_PUBLISH_URL = "https://wangpu.1688.com/product/addProduct.htm"

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
        user_data_dir = _os.path.join(_os.path.expanduser("~"), ".alibaba_seller_profile")
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
        """等待1688商家后台登录"""
        self._log("请在浏览器中完成1688商家后台登录（手淘/支付宝扫码）...")
        for i in range(timeout):
            time.sleep(1)
            try:
                url = self.tab.url
                if ("wangpu.1688.com" in url or "1688.com" in url) and \
                   "login" not in url.lower() and "passport" not in url.lower():
                    self._log("✅ 1688商家后台登录成功")
                    return True
            except Exception:
                pass
            if i % 20 == 0 and i > 0:
                self._log(f"等待1688登录中... ({i}s)")
        return False

    def _fill_title(self, title: str) -> bool:
        """填写产品标题"""
        try:
            selectors = [
                'input[placeholder*="产品标题"]',
                'input[placeholder*="请输入产品名称"]',
                'input[placeholder*="标题"]',
                'input[name*="subject"]',
                'input[name*="title"]',
                '#subject',
                '.product-title input',
            ]
            for sel in selectors:
                el = self.tab.ele(f"css:{sel}", timeout=2)
                if el:
                    el.clear()
                    el.input(title[:100])
                    self._log(f"  ✓ 产品标题: {title[:30]}...")
                    return True
            self._log("  ⚠ 未找到产品标题框")
            return False
        except Exception as e:
            self._log(f"  ✗ 填写标题失败: {e}")
            return False

    def _fill_price_and_moq(self, price: str, moq: int = 1):
        """填写价格和起订量

        1688是批发平台，价格通常以区间形式展示。
        """
        try:
            # 起批价
            price_selectors = [
                'input[placeholder*="起批价"]',
                'input[placeholder*="批发价"]',
                'input[placeholder*="价格"]',
                'input[name*="price"]',
                '.price-input input',
            ]
            for sel in price_selectors:
                el = self.tab.ele(f"css:{sel}", timeout=2)
                if el:
                    el.clear()
                    el.input(str(price))
                    self._log(f"  ✓ 价格: ¥{price}")
                    break

            # 起订量(MOQ)
            moq_selectors = [
                'input[placeholder*="起订量"]',
                'input[placeholder*="最小起订"]',
                'input[name*="moq"]',
                'input[name*="minBuy"]',
            ]
            for sel in moq_selectors:
                el = self.tab.ele(f"css:{sel}", timeout=2)
                if el:
                    el.clear()
                    el.input(str(moq))
                    self._log(f"  ✓ 起订量: {moq}件")
                    break
            return True
        except Exception as e:
            self._log(f"  ✗ 价格/起订量填写失败: {e}")
            return False

    def _fill_stock(self, stock: int = 9999) -> bool:
        """填写库存（1688通常库存较大）"""
        try:
            selectors = [
                'input[placeholder*="库存"]',
                'input[placeholder*="可用库存"]',
                'input[name*="amount"]',
                'input[name*="stock"]',
                '#skuAmount',
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
        """填写产品详情"""
        try:
            # 富文本编辑器
            editor = self.tab.ele('css:[contenteditable="true"]', timeout=3)
            if editor:
                editor.click()
                time.sleep(0.3)
                editor.input(description[:3000])
                self._log("  ✓ 产品详情已填写")
                return True

            # 普通 textarea
            ta = self.tab.ele('css:textarea[id*="desc"], textarea[name*="desc"]', timeout=2)
            if ta:
                ta.clear()
                ta.input(description[:3000])
                return True
            return False
        except Exception as e:
            self._log(f"  ✗ 详情填写失败: {e}")
            return False

    def _upload_images(self, image_paths: list) -> bool:
        """上传产品图片"""
        if not image_paths:
            return True
        try:
            file_input = self.tab.ele('css:input[type="file"]', timeout=5)
            if not file_input:
                self._log("  ⚠ 未找到上传控件")
                return False
            valid = [p for p in image_paths[:15] if os.path.exists(p)]
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
        """提交/发布"""
        try:
            for text in ["立即发布", "发布产品", "提交", "保存并发布", "发布"]:
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
        stock: int = 9999,
        moq: int = 1,
        wait_login: bool = True,
    ) -> dict:
        """上架单个商品到1688

        Args:
            item: 商品数据字典
            price: 覆盖价格（B2B批发价，通常比零售价低）
            price_markup_pct: 在原价基础上的调价百分比
            stock: 库存（默认9999，1688常见做法）
            moq: 最小起订量（默认1件）
            wait_login: 是否等待用户登录

        Returns:
            {"success": bool, "item_id": str, "error": str, "platform": "1688"}
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
                # 1688批发价通常是零售价的6-8折
                if base > 0 and price_markup_pct > 0:
                    final_price = f"{base * (1 + price_markup_pct / 100):.2f}"
                elif base > 0:
                    # 默认不加价
                    final_price = f"{base:.2f}"
                else:
                    final_price = "9.90"

            # 打开商家后台
            self._log("打开1688商家后台 (wangpu.1688.com)...")
            self.tab.get(self.SELLER_URL)
            time.sleep(3)

            current_url = self.tab.url
            if "login" in current_url.lower() or "passport" in current_url.lower():
                if wait_login:
                    if not self._wait_for_login(180):
                        return {
                            "success": False,
                            "item_id": item.get("item_id", ""),
                            "error": "登录超时，请重试",
                            "platform": "1688",
                        }
                else:
                    return {
                        "success": False,
                        "item_id": item.get("item_id", ""),
                        "error": "未登录1688商家后台",
                        "platform": "1688",
                    }

            # 进入发布产品页
            self._log("进入产品发布页面...")
            self.tab.get(self.PUBLISH_URL)
            time.sleep(4)

            if "error" in self.tab.url.lower() or "404" in self.tab.url:
                self.tab.get(self.ALT_PUBLISH_URL)
                time.sleep(3)

            # 上传图片
            self._upload_images(item.get("local_images", []))
            time.sleep(1)

            # 填写产品信息
            title = item.get("ai_title") or item.get("original_title") or item.get("title", "")
            self._fill_title(title)
            time.sleep(0.5)

            self._fill_price_and_moq(final_price, moq)
            time.sleep(0.5)

            self._fill_stock(stock)
            time.sleep(0.5)

            desc = item.get("ai_description") or item.get("description", "")
            if desc:
                self._fill_description(desc)
                time.sleep(0.5)

            # 提交
            success = self._submit()

            # 验证结果
            time.sleep(2)
            page_text = self.tab.run_js("return document.body.innerText || ''") or ""
            if any(kw in page_text for kw in ["发布成功", "审核中", "提交成功"]):
                success = True
            elif any(kw in page_text for kw in ["发布失败", "请填写", "必填项"]):
                success = False

            return {
                "success": success,
                "item_id": item.get("item_id", ""),
                "error": "" if success else "请在商家后台确认发布状态",
                "platform": "1688",
            }

        except Exception as e:
            return {
                "success": False,
                "item_id": item.get("item_id", ""),
                "error": str(e),
                "platform": "1688",
            }
        finally:
            self._close_browser()

    def batch_list(
        self,
        items: list,
        price=None,
        price_markup_pct=0,
        stock=9999,
        moq=1,
        delay=15,
        on_progress=None,
    ) -> list:
        """批量上架到1688"""
        results = []
        for i, item in enumerate(items):
            if on_progress:
                on_progress(i + 1, len(items))
            result = self.list_item(
                item, price, price_markup_pct, stock, moq,
                wait_login=(i == 0),
            )
            results.append(result)
            if i < len(items) - 1:
                time.sleep(delay)
        return results
