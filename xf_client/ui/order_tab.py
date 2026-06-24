import json
import webbrowser
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QMessageBox, QComboBox, QLineEdit,
    QFormLayout, QDialog, QTextEdit, QTabWidget,
    QStyledItemDelegate, QApplication, QSplitter,
    QFrame, QGridLayout,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QFont, QColor, QBrush, QIcon

from database.db_manager import db
from utils.browser_config import get_chromium_options, check_browser_available


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class XianyuLoginWorker(QThread):
    """闲鱼登录Worker - 打开浏览器让用户扫码登录"""
    progress = pyqtSignal(str)
    cookie_saved = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running = True
        self.page = None
        self.user_data_dir = None

    def run(self):
        try:
            from DrissionPage import Chromium
            import os
            
            ok, msg = check_browser_available()
            if not ok:
                self.error.emit(f"浏览器检查失败: {msg}")
                return
            
            # 创建持久化用户数据目录
            home = os.path.expanduser("~")
            self.user_data_dir = os.path.join(home, ".xf_chrome_profile")
            os.makedirs(self.user_data_dir, exist_ok=True)
            
            self.progress.emit("正在启动浏览器...")
            co, _port = get_chromium_options(user_data_dir=self.user_data_dir)
            chromium = Chromium(co)
            self.page = chromium.latest_tab
            
            # 访问闲鱼登录页
            self.progress.emit("正在打开闲鱼登录页面...")
            self.page.get("https://www.goofish.com")
            
            import time
            time.sleep(2)
            
            # 检查是否需要登录
            if "login" in self.page.url.lower() or "passport" in self.page.url.lower():
                self.progress.emit("⚠️ 请使用淘宝/支付宝扫码登录闲鱼...")
            else:
                self.progress.emit("✅ 已登录，正在获取Cookie...")
            
            # 等待用户完成登录（最多3分钟）
            for i in range(180):
                if not self._running:
                    return
                time.sleep(1)
                
                # 检查是否已登录成功
                current_url = self.page.url
                if "login" not in current_url.lower() and "passport" not in current_url.lower():
                    # 已登录，获取Cookie
                    cookies = self.page.cookies()
                    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                    
                    if cookie_str and "_tb_token_" in cookie_str:
                        db.save_cookie("xianyu", cookie_str)
                        self.progress.emit("✅ 登录成功，Cookie已保存")
                        self.cookie_saved.emit(cookie_str)
                        return
                
                if i % 10 == 0:
                    self.progress.emit(f"等待登录中... ({i}s)")
            
            self.error.emit("登录超时，请重试")
            
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if self.page:
                try:
                    self.page.browser.quit()
                except:
                    pass

    def stop(self):
        self._running = False
        if self.page:
            try:
                self.page.browser.quit()
            except:
                pass


class OrderFetchWorker(QThread):
    """订单获取Worker - 使用已保存的Cookie获取订单"""
    progress = pyqtSignal(str)
    orders_found = pyqtSignal(list)
    error = pyqtSignal(str)
    need_login = pyqtSignal()

    def __init__(self, cookie_data=None):
        super().__init__()
        self.cookie_data = cookie_data
        self._running = True
        self.page = None
        self.user_data_dir = None

    def run(self):
        try:
            from DrissionPage import Chromium
            import os
            
            ok, msg = check_browser_available()
            if not ok:
                self.error.emit(f"浏览器检查失败: {msg}")
                return
            
            # 使用相同的用户数据目录
            home = os.path.expanduser("~")
            self.user_data_dir = os.path.join(home, ".xf_chrome_profile")
            os.makedirs(self.user_data_dir, exist_ok=True)
            
            self.progress.emit("正在启动浏览器...")
            co, _port = get_chromium_options(user_data_dir=self.user_data_dir, headless=True)
            chromium = Chromium(co)
            self.page = chromium.latest_tab
            
            # 如果有Cookie，先设置
            if self.cookie_data:
                self.progress.emit("正在使用已保存的Cookie...")
                self.page.get("https://www.goofish.com")
                import time
                time.sleep(1)
                
                # 设置Cookie
                try:
                    for cookie in self.cookie_data.split(';'):
                        if '=' in cookie:
                            name, value = cookie.strip().split('=', 1)
                            self.page.run_js(f'document.cookie = "{name}={value}; domain=.goofish.com; path=/"')
                except:
                    pass
            
            # 访问已卖出订单页面
            self.progress.emit("正在获取订单数据...")
            self.page.get("https://www.goofish.com/sold")
            import time
            time.sleep(3)
            
            # 检查是否需要重新登录
            if "login" in self.page.url.lower():
                self.need_login.emit()
                return
            
            # 解析订单数据
            orders = self._parse_orders()
            self.orders_found.emit(orders)
            
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if self.page:
                try:
                    self.page.browser.quit()
                except:
                    pass

    def _parse_orders(self) -> list:
        """解析订单列表"""
        orders = []
        try:
            import time
            # 滚动加载更多
            for _ in range(5):
                self.page.scroll.to_bottom()
                time.sleep(1)
            
            # 通过JS提取订单数据
            js_script = """
            () => {
                const orders = [];
                const orderEls = document.querySelectorAll('[class*="order-item"], [class*="trade-item"], [class*="order-card"]');
                
                orderEls.forEach(el => {
                    try {
                        // 商品标题
                        const titleEl = el.querySelector('[class*="title"], [class*="item-title"], [class*="goods-title"]');
                        const title = titleEl ? titleEl.textContent.trim() : '';
                        
                        // 价格
                        const priceEl = el.querySelector('[class*="price"], [class*="amount"]');
                        const price = priceEl ? priceEl.textContent.trim() : '';
                        
                        // 买家
                        const buyerEl = el.querySelector('[class*="buyer"], [class*="user-name"]');
                        const buyer = buyerEl ? buyerEl.textContent.trim() : '';
                        
                        // 状态
                        const statusEl = el.querySelector('[class*="status"], [class*="order-status"]');
                        const status = statusEl ? statusEl.textContent.trim() : '';
                        
                        // 订单号
                        const orderNoEl = el.querySelector('[class*="order-no"], [class*="trade-no"]');
                        const orderNo = orderNoEl ? orderNoEl.textContent.trim() : '';
                        
                        // 链接
                        const linkEl = el.querySelector('a[href*="item"], a[href*="trade"]');
                        const link = linkEl ? linkEl.href : '';
                        
                        if (title) {
                            orders.push({title, price, buyer, status, orderNo, link});
                        }
                    } catch(e) {}
                });
                
                return JSON.stringify(orders);
            }
            """
            result = self.page.run_js(js_script)
            if result:
                orders = json.loads(result)
                
        except Exception as e:
            self.progress.emit(f"解析订单异常: {e}")
        
        return orders

    def stop(self):
        self._running = False


class OrderTab(QWidget):
    """订单管理Tab - 完整的店铺订单监控"""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.login_worker = None
        self.fetch_worker = None
        self._setup_ui()
        self._load_orders()
        self._check_login_status()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ===== 顶部：账号状态区 =====
        account_frame = QFrame()
        account_frame.setStyleSheet("""
            QFrame {
                background: #f5f5f5;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        account_layout = QHBoxLayout(account_frame)
        account_layout.setContentsMargins(12, 8, 12, 8)

        # 账号状态
        self.account_status_label = QLabel("🔴 未登录")
        self.account_status_label.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        account_layout.addWidget(self.account_status_label)

        account_layout.addStretch()

        # 登录按钮
        self.login_btn = QPushButton("🔐 登录闲鱼账号")
        self.login_btn.setMinimumHeight(36)
        self.login_btn.setStyleSheet(
            "QPushButton { background: #ff6f00; color: white; "
            "border-radius: 4px; padding: 6px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #e65100; }"
        )
        self.login_btn.clicked.connect(self._start_login)
        account_layout.addWidget(self.login_btn)

        # 刷新Cookie按钮
        self.refresh_cookie_btn = QPushButton("🔄 刷新Cookie")
        self.refresh_cookie_btn.setMinimumHeight(36)
        self.refresh_cookie_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 6px 20px; font-size: 14px; }"
            "QPushButton:hover { background: #1565C0; }"
        )
        self.refresh_cookie_btn.clicked.connect(self._start_login)
        self.refresh_cookie_btn.setVisible(False)
        account_layout.addWidget(self.refresh_cookie_btn)

        # 清除登录按钮
        self.logout_btn = QPushButton("🚪 退出登录")
        self.logout_btn.setMinimumHeight(36)
        self.logout_btn.setStyleSheet(
            "QPushButton { background: #757575; color: white; "
            "border-radius: 4px; padding: 6px 20px; font-size: 14px; }"
        )
        self.logout_btn.clicked.connect(self._logout)
        self.logout_btn.setVisible(False)
        account_layout.addWidget(self.logout_btn)

        layout.addWidget(account_frame)

        # ===== 操作栏 =====
        toolbar = QHBoxLayout()

        title_label = QLabel("📋 店铺订单监控")
        title_label.setFont(QFont(GLOBAL_FONT_FAMILY, 14, QFont.Weight.Bold))
        toolbar.addWidget(title_label)

        toolbar.addStretch()

        # 获取订单按钮
        self.fetch_btn = QPushButton("🔄 获取订单")
        self.fetch_btn.setMinimumHeight(36)
        self.fetch_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 6px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.fetch_btn.clicked.connect(self._fetch_orders)
        toolbar.addWidget(self.fetch_btn)

        # 自动刷新
        toolbar.addWidget(QLabel("自动刷新:"))
        self.auto_refresh_combo = QComboBox()
        self.auto_refresh_combo.setMinimumHeight(36)
        self.auto_refresh_combo.addItems(["关闭", "每5分钟", "每10分钟", "每30分钟"])
        self.auto_refresh_combo.currentTextChanged.connect(self._on_auto_refresh_changed)
        toolbar.addWidget(self.auto_refresh_combo)

        layout.addLayout(toolbar)

        # ===== 状态提示 =====
        self.status_label = QLabel("💡 请先登录闲鱼账号，然后点击「获取订单」")
        self.status_label.setStyleSheet("color: #666; font-size: 13px; padding: 4px;")
        layout.addWidget(self.status_label)

        # ===== 统计卡片 =====
        stats_layout = QHBoxLayout()
        
        self.stat_total = self._create_stat_card("总订单", "0", "#1976D2")
        self.stat_pending = self._create_stat_card("待处理", "0", "#ff6f00")
        self.stat_completed = self._create_stat_card("已完成", "0", "#2e7d32")
        self.stat_today = self._create_stat_card("今日新单", "0", "#7b1fa2")
        
        stats_layout.addWidget(self.stat_total)
        stats_layout.addWidget(self.stat_pending)
        stats_layout.addWidget(self.stat_completed)
        stats_layout.addWidget(self.stat_today)
        layout.addLayout(stats_layout)

        # ===== 订单表格 =====
        self.orders_table = QTableWidget()
        self.orders_table.setColumnCount(9)
        self.orders_table.setHorizontalHeaderLabels([
            "订单时间", "商品标题", "买家", "金额", "订单状态",
            "上游平台", "上游链接", "下单状态", "操作"
        ])
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.orders_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.orders_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self.orders_table.setColumnWidth(8, 220)
        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self.orders_table.setMinimumHeight(300)
        layout.addWidget(self.orders_table)

    def _create_stat_card(self, title: str, value: str, color: str) -> QFrame:
        """创建统计卡片"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: white;
                border-left: 4px solid {color};
                border-radius: 4px;
                padding: 12px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(title_label)
        
        value_label = QLabel(value)
        value_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: bold;")
        value_label.setObjectName(f"stat_value_{title}")
        layout.addWidget(value_label)
        
        return card

    def _update_stat_card(self, card: QFrame, value: str):
        """更新统计卡片数值"""
        for child in card.findChildren(QLabel):
            if child.objectName().startswith("stat_value_"):
                child.setText(value)
                break

    def _check_login_status(self):
        """检查登录状态"""
        cookie = db.get_cookie("xianyu")
        if cookie and len(cookie) > 100:
            self.account_status_label.setText("🟢 已登录")
            self.account_status_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
            self.login_btn.setVisible(False)
            self.refresh_cookie_btn.setVisible(True)
            self.logout_btn.setVisible(True)
            self.status_label.setText("✅ 已登录，点击「获取订单」开始监控")
        else:
            self.account_status_label.setText("🔴 未登录")
            self.account_status_label.setStyleSheet("color: #c62828; font-weight: bold;")
            self.login_btn.setVisible(True)
            self.refresh_cookie_btn.setVisible(False)
            self.logout_btn.setVisible(False)
            self.fetch_btn.setEnabled(False)

    def _start_login(self):
        """开始登录流程"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先激活License")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("登录中...")
        self.status_label.setText("正在打开浏览器，请扫码登录...")

        self.login_worker = XianyuLoginWorker()
        self.login_worker.progress.connect(self._on_login_progress)
        self.login_worker.cookie_saved.connect(self._on_login_success)
        self.login_worker.error.connect(self._on_login_error)
        self.login_worker.start()

    def _on_login_progress(self, msg):
        self.status_label.setText(msg)

    def _on_login_success(self, cookie):
        self._check_login_status()
        self.fetch_btn.setEnabled(True)
        QMessageBox.information(self, "登录成功", "闲鱼账号登录成功，Cookie已保存")

    def _on_login_error(self, msg):
        self.login_btn.setEnabled(True)
        self.login_btn.setText("🔐 登录闲鱼账号")
        self.status_label.setText(f"❌ 登录失败: {msg}")
        QMessageBox.critical(self, "登录失败", msg)

    def _logout(self):
        """退出登录"""
        reply = QMessageBox.question(
            self, "确认退出", 
            "确定要退出登录并清除保存的Cookie吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_cookie("xianyu")
            self._check_login_status()
            self.status_label.setText("已退出登录")

    def _fetch_orders(self):
        """获取订单"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先激活License")
            return

        cookie = db.get_cookie("xianyu")
        if not cookie:
            QMessageBox.warning(self, "未登录", "请先登录闲鱼账号")
            return

        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("获取中...")
        self.status_label.setText("正在获取订单数据...")

        self.fetch_worker = OrderFetchWorker(cookie)
        self.fetch_worker.progress.connect(self._on_fetch_progress)
        self.fetch_worker.orders_found.connect(self._on_orders_found)
        self.fetch_worker.error.connect(self._on_fetch_error)
        self.fetch_worker.need_login.connect(self._on_need_relogin)
        self.fetch_worker.start()

    def _on_fetch_progress(self, msg):
        self.status_label.setText(msg)

    def _on_orders_found(self, orders):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🔄 获取订单")

        if not orders:
            self.status_label.setText("暂无新订单")
            return

        # 处理订单数据
        new_count = 0
        for order_data in orders:
            # 尝试匹配本地商品
            title = order_data.get("title", "")
            matched_product = None

            for item in self.main_window.get_items():
                item_title = item.get("original_title", "")
                if title and (item_title in title or title in item_title):
                    matched_product = item
                    break

            # 检查是否已存在
            existing_orders = db.get_all_orders()
            exists = False
            for eo in existing_orders:
                if eo.get("platform_order_id") == order_data.get("orderNo"):
                    exists = True
                    break

            if not exists:
                order_dict = {
                    "product_id": matched_product["db_id"] if matched_product else None,
                    "platform_order_id": order_data.get("orderNo", ""),
                    "platform": "xianyu",
                    "buyer_name": order_data.get("buyer", ""),
                    "order_amount": order_data.get("price", ""),
                    "order_status": "pending",
                    "notes": title,
                }
                db.save_order(order_dict)
                new_count += 1

        self.status_label.setText(f"✅ 发现 {len(orders)} 个订单，新增 {new_count} 个")
        self._load_orders()

    def _on_fetch_error(self, msg):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🔄 获取订单")
        self.status_label.setText(f"❌ 获取失败: {msg}")
        QMessageBox.critical(self, "错误", f"获取订单失败: {msg}")

    def _on_need_relogin(self):
        """需要重新登录"""
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🔄 获取订单")
        self.status_label.setText("❌ Cookie已过期，需要重新登录")
        
        reply = QMessageBox.question(
            self, "需要重新登录",
            "Cookie已过期，是否重新登录？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_login()

    def _load_orders(self):
        """加载并显示订单"""
        try:
            orders = db.get_all_orders()
            self._display_orders(orders)
            self._update_stats(orders)
        except Exception as e:
            self.status_label.setText(f"❌ 加载订单失败: {e}")

    def _update_stats(self, orders):
        """更新统计数据"""
        total = len(orders)
        pending = len([o for o in orders if o.get("order_status") == "pending"])
        completed = len([o for o in orders if o.get("order_status") == "completed"])
        
        # 今日新单
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        today_count = len([o for o in orders if str(o.get("created_at", "")).startswith(today)])

        self._update_stat_card(self.stat_total, str(total))
        self._update_stat_card(self.stat_pending, str(pending))
        self._update_stat_card(self.stat_completed, str(completed))
        self._update_stat_card(self.stat_today, str(today_count))

    def _display_orders(self, orders):
        """显示订单列表"""
        self.orders_table.setRowCount(len(orders))

        for i, order in enumerate(orders):
            # 获取关联的商品信息
            product = None
            if order.get("product_id"):
                product = db.get_product_by_id(order["product_id"])

            # 订单时间
            created = order.get("created_at", "")
            if isinstance(created, str) and len(created) > 16:
                created = created[:16]
            self.orders_table.setItem(i, 0, QTableWidgetItem(created or "-"))

            # 商品标题
            title = product["original_title"] if product else order.get("notes", "未知商品")
            self.orders_table.setItem(i, 1, QTableWidgetItem(title[:50]))

            # 买家
            buyer = order.get("buyer_name", "")
            self.orders_table.setItem(i, 2, QTableWidgetItem(buyer or "未知"))

            # 金额
            self.orders_table.setItem(i, 3, QTableWidgetItem(order.get("order_amount", "")))

            # 订单状态
            status = order.get("order_status", "pending")
            status_map = {
                "pending": "⏳ 待处理",
                "upstream_ordered": "✅ 已上游下单",
                "shipped": "📦 已发货",
                "completed": "✅ 已完成",
                "cancelled": "❌ 已取消",
            }
            status_text = status_map.get(status, status)
            status_item = QTableWidgetItem(status_text)
            if status == "pending":
                status_item.setForeground(QBrush(QColor("#e65100")))
            elif status == "completed":
                status_item.setForeground(QBrush(QColor("#2e7d32")))
            self.orders_table.setItem(i, 4, status_item)

            # 上游平台
            platform = product["platform"] if product else "xianyu"
            platform_text = {"xianyu": "闲鱼", "pdd": "拼多多", "1688": "阿里巴巴"}.get(platform, platform)
            self.orders_table.setItem(i, 5, QTableWidgetItem(platform_text))

            # 上游链接
            source_url = product["source_url"] if product else ""
            if source_url:
                link_item = QTableWidgetItem("🔗 点击打开")
                link_item.setForeground(QBrush(QColor("#1976D2")))
                link_item.setData(Qt.ItemDataRole.UserRole, source_url)
                link_item.setToolTip(source_url)
            else:
                link_item = QTableWidgetItem("-")
            self.orders_table.setItem(i, 6, QTableWidgetItem(link_item))

            # 下单状态
            upstream_status = order.get("upstream_status", "")
            self.orders_table.setItem(i, 7, QTableWidgetItem(upstream_status or "未下单"))

            # 操作按钮
            ops_widget = QWidget()
            ops_layout = QHBoxLayout(ops_widget)
            ops_layout.setContentsMargins(4, 2, 4, 2)

            if source_url and status == "pending":
                go_btn = QPushButton("🛒 去下单")
                go_btn.setMinimumHeight(30)
                go_btn.setStyleSheet(
                    "QPushButton { background: #ff6f00; color: white; "
                    "border-radius: 4px; padding: 2px 12px; font-size: 12px; font-weight: bold; }"
                )
                go_btn.clicked.connect(lambda checked, url=source_url, oid=order["id"]: self._go_upstream(url, oid))
                ops_layout.addWidget(go_btn)

                copy_btn = QPushButton("📋 复制信息")
                copy_btn.setMinimumHeight(30)
                copy_btn.setStyleSheet(
                    "QPushButton { background: #757575; color: white; "
                    "border-radius: 4px; padding: 2px 12px; font-size: 12px; }"
                )
                copy_btn.clicked.connect(lambda checked, o=order, p=product: self._copy_buyer_info(o, p))
                ops_layout.addWidget(copy_btn)

            ops_layout.addStretch()
            self.orders_table.setCellWidget(i, 8, ops_widget)

    def _go_upstream(self, url, order_id):
        """打开上游链接去下单"""
        if not url:
            QMessageBox.warning(self, "提示", "该商品没有上游链接")
            return

        webbrowser.open(url)
        self.status_label.setText(f"已打开上游链接，请完成下单")

        # 更新订单状态
        db.update_order_status(order_id, "upstream_ordered", {
            "upstream_status": "已打开上游链接"
        })
        self._load_orders()

    def _copy_buyer_info(self, order, product):
        """复制买家信息到剪贴板"""
        info = f"商品: {product['original_title'] if product else '未知'}\n"
        info += f"买家: {order.get('buyer_name', '')}\n"
        info += f"电话: {order.get('buyer_phone', '')}\n"
        info += f"地址: {order.get('buyer_address', '')}\n"
        info += f"上游链接: {product['source_url'] if product else '无'}"

        clipboard = QApplication.clipboard()
        clipboard.setText(info)
        self.status_label.setText("✅ 买家信息已复制到剪贴板")

    def _on_auto_refresh_changed(self, text):
        """自动刷新设置"""
        intervals = {
            "关闭": 0,
            "每5分钟": 300000,
            "每10分钟": 600000,
            "每30分钟": 1800000,
        }
        interval = intervals.get(text, 0)

        if hasattr(self, '_auto_timer'):
            self._auto_timer.stop()

        if interval > 0:
            self._auto_timer = QTimer()
            self._auto_timer.timeout.connect(self._fetch_orders)
            self._auto_timer.start(interval)
            self.status_label.setText(f"✅ 自动刷新已开启: {text}")
        else:
            self.status_label.setText("自动刷新已关闭")

    def refresh_data(self):
        """刷新数据"""
        self._load_orders()

    def closeEvent(self, event):
        """关闭时停止Worker"""
        if self.login_worker and self.login_worker.isRunning():
            self.login_worker.stop()
        if self.fetch_worker and self.fetch_worker.isRunning():
            self.fetch_worker.stop()
        event.accept()
