import sys
import json
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QMessageBox, QLabel, QApplication,
)
from PyQt6.QtCore import Qt, QTimer
import time
from PyQt6.QtGui import QIcon, QFont

from ui.collect_tab import CollectTab
from ui.copywriting_tab import CopywritingTab
from ui.listing_tab import ListingTab
from ui.export_tab import ExportTab
from ui.order_tab import OrderTab
from ui.settings_tab import SettingsTab
from license.license_validator import LicenseValidator
from database.db_manager import db


# 全局字体
GLOBAL_FONT_SIZE = 14
GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("闲管家采集上架助手 v3.0")
        self.setMinimumSize(1200, 800)

        # 设置全局字体
        font = QFont()
        font.setFamilies(["Microsoft YaHei", "PingFang SC", "Helvetica"])
        font.setPointSize(GLOBAL_FONT_SIZE)
        QApplication.instance().setFont(font)

        # 共享数据 - 从数据库加载
        self.collected_items = self._load_products_from_db()
        self.license_validator = LicenseValidator()
        self._license_cache = None
        self._license_cache_ts = 0.0
        self._license_cache_ttl = 30.0  # 秒：避免每次点击都打网络

        # 中心部件
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        # 状态栏
        self.status_label = QLabel()
        self.status_label.setFont(QFont(GLOBAL_FONT_FAMILY, 11))
        self._update_status()
        layout.addWidget(self.status_label)

        # 未激活提示
        self.unlicensed_label = QLabel(
            "⚠️ 未激活：采集、文案优化、上架、导出功能不可用。请在设置页面输入License Key激活。"
        )
        self.unlicensed_label.setStyleSheet(
            "color: #e65100; background: #fff3e0; padding: 10px; "
            "border-radius: 4px; font-size: 14px; font-weight: bold;"
        )
        self.unlicensed_label.setWordWrap(True)
        self.unlicensed_label.setVisible(not self.is_licensed())
        layout.addWidget(self.unlicensed_label)

        # Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setFont(QFont(GLOBAL_FONT_FAMILY, 12))

        self.collect_tab = CollectTab(self)
        self.copywriting_tab = CopywritingTab(self)
        self.listing_tab = ListingTab(self)
        self.export_tab = ExportTab(self)
        self.order_tab = OrderTab(self)
        self.settings_tab = SettingsTab(self)

        self.tabs.addTab(self.collect_tab,      "🔍 采集")
        self.tabs.addTab(self.copywriting_tab,  "✍️ 文案优化")
        self.tabs.addTab(self.listing_tab,      "📦 上架闲管家")
        self.tabs.addTab(self.export_tab,       "📊 导出")
        self.tabs.addTab(self.order_tab,        "🛒 订单代采")
        self.tabs.addTab(self.settings_tab,     "⚙️ 设置")

        layout.addWidget(self.tabs)

        # 底部状态栏
        self.statusBar().showMessage(f"就绪 | 已加载 {len(self.collected_items)} 个商品")

        # 刷新各Tab数据
        self._refresh_all_tabs()

        # 心跳定时器：运行期实时反映吊销/强制下线
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._on_heartbeat)
        self._heartbeat_timer.start(60_000)

    def _load_products_from_db(self) -> list:
        """从数据库加载所有商品"""
        try:
            return db.get_all_products()
        except Exception as e:
            print(f"加载数据库失败: {e}")
            return []

    def _refresh_all_tabs(self):
        """刷新所有Tab的数据"""
        self.copywriting_tab.refresh_items(self.collected_items)
        self.listing_tab.refresh_items(self.collected_items)
        self.export_tab.refresh_items(self.collected_items)
        self.order_tab.refresh_items(self.collected_items)

    def is_licensed(self, force: bool = False) -> bool:
        """检查是否已激活（带短期缓存，避免每次点击都打网络）。"""
        now = time.time()
        if (not force) and self._license_cache is not None and (now - self._license_cache_ts) < self._license_cache_ttl:
            return self._license_cache
        result = self.license_validator.verify()
        valid = result.get("valid", False)
        self._license_cache = valid
        self._license_cache_ts = now
        self._last_license_reason = result.get("reason", "")
        return valid

    def _update_status(self):
        info = self.license_validator.get_license_info()
        if info.get("license_key"):
            expires = info.get("expires_at", "N/A")
            if isinstance(expires, str) and len(expires) > 10:
                expires = expires[:10]
            self.status_label.setText(
                f"✅ 已激活 | License: {info['license_key'][:8]}... | 到期: {expires}"
            )
            self.status_label.setStyleSheet("color: #2e7d32; padding: 4px;")
        else:
            self.status_label.setText("❌ 未激活 | 请在设置页面输入License Key激活")
            self.status_label.setStyleSheet("color: #c62828; padding: 4px;")

    def set_items(self, items: list):
        """更新共享数据并刷新所有Tab"""
        self.collected_items = items
        self._refresh_all_tabs()
        self.statusBar().showMessage(f"已加载 {len(items)} 个商品")

    def get_items(self) -> list:
        return self.collected_items

    def add_item(self, item: dict):
        """添加单个商品"""
        try:
            db_id = db.save_product(item)
            item["db_id"] = db_id
        except Exception as e:
            print(f"保存商品到数据库失败: {e}")
        self.collected_items.append(item)
        self._refresh_all_tabs()
        self.statusBar().showMessage(f"已加载 {len(self.collected_items)} 个商品")

    def update_item(self, index: int, item: dict):
        """更新商品信息"""
        if 0 <= index < len(self.collected_items):
            self.collected_items[index] = item
            try:
                db.save_product(item)
            except Exception as e:
                print(f"更新数据库失败: {e}")

    def delete_item(self, index: int):
        """删除商品"""
        if 0 <= index < len(self.collected_items):
            item = self.collected_items.pop(index)
            try:
                if item.get("db_id"):
                    db.delete_product(item["db_id"])
            except Exception as e:
                print(f"删除数据库记录失败: {e}")
            self._refresh_all_tabs()
            self.statusBar().showMessage(f"已加载 {len(self.collected_items)} 个商品")

    def reload_from_db(self):
        """从数据库重新加载所有数据"""
        self.collected_items = self._load_products_from_db()
        self._refresh_all_tabs()
        self.statusBar().showMessage(f"已加载 {len(self.collected_items)} 个商品")

    def update_status(self):
        self._update_status()
        # 激活/状态变更后强制刷新缓存，立即反映到 UI 与各功能门控
        self.unlicensed_label.setVisible(not self.is_licensed(force=True))

    def _on_heartbeat(self):
        """定时心跳：服务端指示下线时立即失效并提示。"""
        try:
            res = self.license_validator.heartbeat()
        except Exception:
            return
        action = res.get("action", "continue")
        if action in ("logout", "deactivate"):
            self._license_cache = False
            self._license_cache_ts = time.time()
            self.update_status()
            QMessageBox.warning(
                self, "授权状态变更",
                f"当前授权已失效：{res.get('reason', '已被管理员下线')}\n相关功能已停用。",
            )

    def closeEvent(self, event):
        """关闭窗口时保存数据"""
        try:
            for item in self.collected_items:
                db.save_product(item)
        except Exception as e:
            print(f"关闭时保存失败: {e}")
        event.accept()
