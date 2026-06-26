import json
import re
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QGroupBox,
    QRadioButton, QButtonGroup, QProgressBar, QMessageBox,
    QTextEdit, QSplitter, QComboBox,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont

from engine.xianyu_collector import XianyuCollector
from engine.pdd_collector import PddCollector
from engine.alibaba_collector import AlibabaCollector
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    pattern = r'^https?://[^\s<>"{}|\\^`\[\]]+$'
    return bool(re.match(pattern, url, re.IGNORECASE))


class CollectWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, platform, mode, value, count):
        super().__init__()
        self.platform = platform
        self.mode = mode
        self.value = value
        self.count = count

    def run(self):
        def on_progress(msg):
            self.progress.emit(msg)

        try:
            if self.platform == "xianyu":
                collector = XianyuCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_homepage(self.value, self.count)
            elif self.platform == "pdd":
                collector = PddCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_link(self.value)
            elif self.platform == "1688":
                collector = AlibabaCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_link(self.value)
            else:
                items = []

            self.finished.emit(items)
        except Exception as e:
            self.error.emit(str(e))


class PddLoginWorker(QThread):
    """拼多多登录Worker - 打开浏览器让用户扫码登录"""
    progress = pyqtSignal(str)
    login_success = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running = True
        self.chromium = None

    def run(self):
        try:
            from DrissionPage import Chromium
            from engine.pdd_collector import PDD_USER_DATA_DIR
            
            os.makedirs(PDD_USER_DATA_DIR, exist_ok=True)
            
            self.progress.emit("正在启动浏览器...")
            co, _port = get_chromium_options_for_login(user_data_dir=PDD_USER_DATA_DIR)
            self.chromium = Chromium(co)
            tab = self.chromium.latest_tab

            self.progress.emit("正在打开拼多多登录页面...")
            tab.get("https://mobile.yangkeduo.com/login.html")
            
            import time
            time.sleep(3)
            self.progress.emit("⚠️ 请用拼多多APP扫描页面二维码登录...")
            
            # 等待登录成功（最多3分钟）
            for i in range(180):
                if not self._running:
                    return
                time.sleep(1)
                
                try:
                    current_url = tab.url
                    # 登录成功后会跳转离开login页面
                    if "login" not in current_url.lower():
                        self.progress.emit("✅ 拼多多登录成功！")
                        # 保存Cookie
                        cookies = tab.cookies()
                        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                        if cookie_str:
                            db.save_cookie("pdd", cookie_str)
                        self.login_success.emit()
                        return
                except Exception:
                    pass
                
                if i % 10 == 0 and i > 0:
                    self.progress.emit(f"等待登录中... ({i}s)")
            
            self.error.emit("登录超时，请重试")
            
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if self.chromium:
                try:
                    self.chromium.quit()
                except:
                    pass

    def stop(self):
        self._running = False
        if self.chromium:
            try:
                self.chromium.quit()
            except:
                pass


def get_chromium_options_for_login(user_data_dir=None, headless=False):
    """获取登录用的Chrome配置（非headless，让用户看到二维码）"""
    from utils.browser_config import get_chromium_options
    return get_chromium_options(user_data_dir=user_data_dir, headless=False)


class CollectTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.worker = None
        self.pdd_login_worker = None
        self._setup_ui()
        self._check_pdd_login_status()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 平台选择 + 登录按钮
        platform_group = QGroupBox("采集平台")
        platform_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        platform_layout = QHBoxLayout(platform_group)

        platform_layout.addWidget(QLabel("选择平台:"))
        self.platform_combo = QComboBox()
        self.platform_combo.setMinimumHeight(36)
        self.platform_combo.addItem("🐟 闲鱼", "xianyu")
        self.platform_combo.addItem("🛒 拼多多", "pdd")
        self.platform_combo.addItem("🏭 阿里巴巴(1688)", "1688")
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        platform_layout.addWidget(self.platform_combo)
        platform_layout.addStretch()

        # 拼多多登录状态 + 按钮
        self.pdd_login_label = QLabel("拼多多: 🔴 未登录")
        self.pdd_login_label.setStyleSheet("color: #c62828; font-size: 13px;")
        platform_layout.addWidget(self.pdd_login_label)

        self.pdd_login_btn = QPushButton("🔐 登录拼多多")
        self.pdd_login_btn.setMinimumHeight(36)
        self.pdd_login_btn.setStyleSheet(
            "QPushButton { background: #e91e63; color: white; "
            "border-radius: 4px; padding: 6px 16px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background: #c2185b; }"
        )
        self.pdd_login_btn.clicked.connect(self._login_pdd)
        platform_layout.addWidget(self.pdd_login_btn)

        layout.addWidget(platform_group)

        # 模式选择
        mode_group = QGroupBox("采集模式")
        mode_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(8)

        self.keyword_radio = QRadioButton("🔍 关键词搜索采集")
        self.homepage_radio = QRadioButton("🔗 商品链接采集")
        self.keyword_radio.setChecked(True)

        self.mode_btn_group = QButtonGroup(self)
        self.mode_btn_group.addButton(self.keyword_radio, 0)
        self.mode_btn_group.addButton(self.homepage_radio, 1)
        self.mode_btn_group.idToggled.connect(self._on_mode_changed)

        mode_layout.addWidget(self.keyword_radio)
        mode_layout.addWidget(self.homepage_radio)
        layout.addWidget(mode_group)

        # 采集参数
        param_group = QGroupBox("采集参数")
        param_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        param_layout = QVBoxLayout(param_group)
        param_layout.setSpacing(8)

        self.input_label = QLabel("关键词:")
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入要搜索的关键词，如：iPhone 15")
        self.input_field.setMinimumHeight(36)
        param_layout.addWidget(self.input_label)
        param_layout.addWidget(self.input_field)

        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("采集数量:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(20)
        self.count_spin.setMinimumHeight(36)
        count_layout.addWidget(self.count_spin)
        count_layout.addStretch()
        param_layout.addLayout(count_layout)

        layout.addWidget(param_group)

        # 日志区
        log_group = QGroupBox("采集日志")
        log_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        log_layout = QVBoxLayout(log_group)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(200)
        self.log_area.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        log_layout.addWidget(self.log_area)
        layout.addWidget(log_group)

        # 进度
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(28)
        layout.addWidget(self.progress_bar)

        # 按钮
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("🚀 开始采集")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.start_btn.clicked.connect(self._start_collect)

        self.cancel_btn = QPushButton("⏹ 取消")
        self.cancel_btn.setMinimumHeight(42)
        self.cancel_btn.setStyleSheet(
            "QPushButton { background: #e53935; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #C62828; }"
        )
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(False)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 已采集数据统计
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #666; font-size: 13px; padding: 4px;")
        layout.addWidget(self.stats_label)

        layout.addStretch()

    def _on_platform_changed(self):
        """平台切换时更新提示"""
        platform = self.platform_combo.currentData()
        if platform == "xianyu":
            self.input_field.setPlaceholderText("输入要搜索的关键词，如：iPhone 15")
        elif platform == "pdd":
            self.input_field.setPlaceholderText("输入拼多多搜索关键词，或粘贴商品链接")
        elif platform == "1688":
            self.input_field.setPlaceholderText("输入1688搜索关键词，或粘贴商品链接")

    def _on_mode_changed(self, btn_id, checked):
        if checked:
            if btn_id == 0:
                self.input_label.setText("关键词:")
                self.input_field.setPlaceholderText("输入要搜索的关键词")
            else:
                self.input_label.setText("商品链接:")
                self.input_field.setPlaceholderText("粘贴商品详情页链接")

    def _check_pdd_login_status(self):
        """检查拼多多登录状态"""
        cookie = db.get_cookie("pdd")
        if cookie and len(cookie) > 50:
            self.pdd_login_label.setText("拼多多: 🟢 已登录")
            self.pdd_login_label.setStyleSheet("color: #2e7d32; font-size: 13px;")
            self.pdd_login_btn.setText("🔄 重新登录")
        else:
            self.pdd_login_label.setText("拼多多: 🔴 未登录")
            self.pdd_login_label.setStyleSheet("color: #c62828; font-size: 13px;")
            self.pdd_login_btn.setText("🔐 登录拼多多")

    def _login_pdd(self):
        """登录拼多多"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License")
            return

        self.pdd_login_btn.setEnabled(False)
        self.pdd_login_btn.setText("登录中...")
        self._append_log("正在打开拼多多登录页面，请用APP扫码...")

        self.pdd_login_worker = PddLoginWorker()
        self.pdd_login_worker.progress.connect(self._on_pdd_login_progress)
        self.pdd_login_worker.login_success.connect(self._on_pdd_login_success)
        self.pdd_login_worker.error.connect(self._on_pdd_login_error)
        self.pdd_login_worker.start()

    def _on_pdd_login_progress(self, msg):
        self._append_log(msg)

    def _on_pdd_login_success(self):
        self.pdd_login_btn.setEnabled(True)
        self._check_pdd_login_status()
        self._append_log("✅ 拼多多登录成功！现在可以采集拼多多商品了")
        QMessageBox.information(self, "登录成功", "拼多多登录成功，现在可以采集拼多多商品了")

    def _on_pdd_login_error(self, msg):
        self.pdd_login_btn.setEnabled(True)
        self.pdd_login_btn.setText("🔐 登录拼多多")
        self._append_log(f"❌ 拼多多登录失败: {msg}")
        QMessageBox.critical(self, "登录失败", f"拼多多登录失败: {msg}")

    def _start_collect(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用采集功能")
            return

        platform = self.platform_combo.currentData()
        mode = "keyword" if self.keyword_radio.isChecked() else "homepage"
        value = self.input_field.text().strip()

        if not value:
            QMessageBox.warning(self, "提示", "请输入关键词或商品链接")
            return

        if mode == "homepage" and not is_valid_url(value):
            QMessageBox.warning(
                self, "无效的URL",
                "请输入有效的商品链接\n\n"
                "格式示例：\n"
                "闲鱼: https://www.goofish.com/item?id=xxx\n"
                "拼多多: https://mobile.yangkeduo.com/goods.html?goods_id=xxx\n"
                "1688: https://detail.1688.com/offer/xxx.html"
            )
            return

        # 拼多多需要登录
        if platform == "pdd":
            cookie = db.get_cookie("pdd")
            if not cookie:
                reply = QMessageBox.question(
                    self, "需要登录",
                    "拼多多需要登录后才能搜索商品。\n是否现在登录拼多多？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._login_pdd()
                return

        count = self.count_spin.value()

        self.start_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.log_area.clear()
        self._append_log(f"开始采集 [{self.platform_combo.currentText()}]...")

        self.worker = CollectWorker(platform, mode, value, count)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
        self._reset_ui()
        self._append_log("采集已取消")

    def _on_progress(self, msg):
        self._append_log(msg)

    def _on_finished(self, items):
        # 保存到数据库
        for item in items:
            try:
                db_id = db.save_product(item)
                item["db_id"] = db_id
            except Exception as e:
                print(f"保存商品失败: {e}")

        self.main_window.set_items(items)
        self._reset_ui()
        total_imgs = sum(len(it.get("local_images", [])) for it in items)
        self._append_log(f"\n✅ 采集完成！共 {len(items)} 个商品，{total_imgs} 张图片（已MD5去重）")
        self._append_log(f"💾 数据已保存到本地数据库，关闭软件不会丢失")
        QMessageBox.information(self, "完成", f"采集完成，共 {len(items)} 个商品，{total_imgs} 张图片\n数据已自动保存")

    def _on_error(self, msg):
        self._reset_ui()
        self._append_log(f"❌ 采集失败: {msg}")
        QMessageBox.critical(self, "错误", f"采集失败: {msg}")

    def _append_log(self, msg):
        self.log_area.append(msg)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _reset_ui(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.worker = None

    def refresh_items(self, items):
        """刷新统计信息"""
        platform_counts = {}
        for item in items:
            p = item.get("platform", "xianyu")
            platform_counts[p] = platform_counts.get(p, 0) + 1

        stats = " | ".join([f"{k}: {v}个" for k, v in platform_counts.items()])
        self.stats_label.setText(f"📊 当前数据: {stats}" if stats else "📊 暂无数据")
