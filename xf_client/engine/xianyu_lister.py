import time
import os
from DrissionPage import ChromiumPage, ChromiumOptions
from config import PLATFORM_URLS

XIANYU_BASE_URL = PLATFORM_URLS['xianyu']['home'].rstrip('/')


class XianyuLister:
    """闲鱼商品上架器
    闲鱼发布页结构：
    - 无独立标题输入框，标题在描述编辑器的第一行
    - 描述用 contenteditable div (class*="editor--")
    - 价格有3个input: 价格(第一个)、原价(第二个)、运费(第三个)
    - 图片上传: input[type="file"], accept=image/*, multiple
    - 发布按钮: button[class*="publish-button"]
    - 所在地弹窗: 搜索地点 input[class*="search-input"]
    """

    def __init__(self, on_progress=None):
        self.page = None
        self._log = on_progress or (lambda msg: None)

    def _init_browser(self):
        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1920,1080')
        self.page = ChromiumPage(co)
        self.page.run_js("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

    def _close_browser(self):
        if self.page:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None

    def _set_title_and_desc(self, title: str, description: str):
        """填写标题和描述
        闲鱼发布页没有独立标题框，标题+描述都在一个 contenteditable 编辑器里。
        第一行写标题，空一行后写描述。
        """
        editor = self.page.ele('css:[class*="editor--"]')
        if not editor:
            # fallback: 找 contenteditable div
            editor = self.page.ele('css:[contenteditable="true"]')
        
        if editor:
            editor.click()
            time.sleep(0.3)
            
            # 清空已有内容
            self.page.run_js('''
                var editor = document.querySelector('[class*="editor--"]') || document.querySelector('[contenteditable="true"]');
                if(editor) {
                    editor.focus();
                    editor.innerHTML = '';
                }
            ''')
            time.sleep(0.2)
            
            # 第一行标题，空行，然后描述
            full_text = f"{title}\n\n{description}" if description else title
            editor.input(full_text)
            time.sleep(0.5)

    def _set_price(self, price: str, original_price: str = None, shipping: str = "0"):
        """填写价格
        3个 input.ant-input:
        [0] = 价格  [1] = 原价  [2] = 运费
        """
        price_inputs = self.page.eles('css:input.ant-input')
        
        if price_inputs and len(price_inputs) >= 1:
            # 价格
            price_inputs[0].clear()
            price_inputs[0].input(price)
            time.sleep(0.3)
            
            # 原价（可选）
            if original_price and len(price_inputs) >= 2:
                price_inputs[1].clear()
                price_inputs[1].input(original_price)
                time.sleep(0.3)
            
            # 运费
            if len(price_inputs) >= 3:
                price_inputs[2].clear()
                price_inputs[2].input(shipping)
                time.sleep(0.3)

    def _upload_images(self, image_paths: list):
        """上传商品图片"""
        if not image_paths:
            return
        
        file_input = self.page.ele('css:input[type="file"]')
        if file_input:
            # 最多9张
            valid_paths = []
            for p in image_paths[:9]:
                if os.path.exists(p):
                    valid_paths.append(p)
            
            if valid_paths:
                # DrissionPage 支持多文件上传
                file_input.input(valid_paths)
                # 等待上传完成
                time.sleep(3)

    def _set_location(self, location: str = "全国"):
        """设置发货地
        闲鱼发布页有"宝贝所在地"弹窗，需要搜索地点并选择。
        如果传"全国"则跳过（使用默认）。
        """
        if not location or location == "全国":
            return
        
        try:
            # 找到所在地的搜索框
            search_input = self.page.ele('css:input[class*="search-input"]')
            if search_input:
                search_input.clear()
                search_input.input(location)
                time.sleep(1.5)
                
                # 点击第一个搜索结果
                result = self.page.ele('css:[class*="search-result"]')
                if result:
                    result.click()
                    time.sleep(1)
                    
                    # 点确定
                    confirm_btn = self.page.ele('text:确定')
                    if confirm_btn:
                        confirm_btn.click()
                        time.sleep(0.5)
        except Exception:
            pass  # 地址设置失败不阻塞发布

    def _click_publish(self) -> bool:
        """点击发布按钮"""
        pub_btn = self.page.ele('css:button[class*="publish-button"]')
        if not pub_btn:
            pub_btn = self.page.ele('text:发布')
        
        if pub_btn:
            pub_btn.click()
            time.sleep(5)
            
            # 检查是否发布成功：URL变化或出现成功提示
            current_url = self.page.url
            if 'publish' not in current_url:
                return True
            
            # 检查页面是否有错误提示
            page_text = self.page.run_js('return document.body.innerText')
            if '发布成功' in page_text or '发布中' in page_text:
                return True
            if '请填写' in page_text or '不能为空' in page_text:
                return False
            
            # 如果还在发布页，检查是否有弹窗
            return True
        return False

    def list_item(self, item: dict, price: str = None, price_markup_pct: float = 0,
                  stock: str = "1", location: str = "全国", schedule_time: str = None,
                  wait_login: bool = True) -> dict:
        """上架单个商品
        
        Args:
            item: 商品数据（需含 title/original_title, description, price/original_price, local_images）
            price: 覆盖价格（如为None则用商品自身价格）
            price_markup_pct: 加价百分比
            stock: 库存（闲鱼发闲置固定为1）
            location: 发货地
            schedule_time: 定时发布（未实现）
        
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
                    base = float(base)
                except (ValueError, TypeError):
                    base = 0
                
                if base > 0 and price_markup_pct > 0:
                    final_price = f"{base * (1 + price_markup_pct / 100):.2f}"
                elif base > 0:
                    final_price = f"{base:.2f}"
                else:
                    final_price = "0.01"

            # 打开发布页
            self.page.get(f"{XIANYU_BASE_URL}/publish")
            time.sleep(4)

            # 检查是否跳到登录页
            if 'login' in self.page.url or 'passport' in self.page.url:
                return {"success": False, "item_id": item.get("item_id", ""), "error": "未登录闲鱼，请先在浏览器中登录"}

            # 1. 上传图片（先上传，闲鱼会自动识别属性）
            images = item.get("local_images", [])
            if images:
                self._upload_images(images)
                time.sleep(2)

            # 2. 填写标题+描述
            title = item.get("ai_title") or item.get("original_title") or item.get("title", "")
            desc = item.get("ai_description") or item.get("description", "")
            self._set_title_and_desc(title, desc)
            time.sleep(0.5)

            # 3. 填写价格
            original_price = item.get("original_price", "")
            self._set_price(final_price, original_price, "0")
            time.sleep(0.5)

            # 4. 设置发货地
            self._set_location(location)
            time.sleep(0.5)

            # 5. 点击发布
            success = self._click_publish()

            return {
                "success": success,
                "item_id": item.get("item_id", ""),
                "error": "" if success else "发布可能失败，请检查闲鱼页面",
            }
        except Exception as e:
            return {"success": False, "item_id": item.get("item_id", ""), "error": str(e)}
        finally:
            self._close_browser()

    def batch_list(self, items: list, price: str = None, price_markup_pct: float = 0,
                   stock: str = "1", location: str = "全国", delay: int = 5) -> list:
        """批量上架"""
        results = []
        for i, item in enumerate(items):
            result = self.list_item(item, price, price_markup_pct, stock, location)
            results.append(result)
            if i < len(items) - 1:
                time.sleep(delay)
        return results
